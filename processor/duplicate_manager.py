import time
import logging
import os
import shutil
import re
import json
import numpy as np
import faiss
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("PhotoArrange")

class DisjointSetUnion:
    def __init__(self, elements):
        self.parent = {el: el for el in elements}
        self.size = {el: 1 for el in elements}

    def find(self, i):
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i, j):
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            if self.size[root_i] < self.size[root_j]:
                root_i, root_j = root_j, root_i
            self.parent[root_j] = root_i
            self.size[root_i] += self.size[root_j]
            return True
        return False

class DuplicateManager:
    def __init__(self, db, image_processor, feature_extractor):
        self.db = db
        self.img_proc = image_processor
        self.feat_ext = feature_extractor
        self.is_cancelled = False # Track cancellation from worker thread

    def find_structural_duplicates(self, threshold=0.6, stage2_threshold=0.95, include_trash=False, progress_callback=None):
        """
        Performs a global multi-pass search for duplicates.
        Passes: MD5 (Pass 0), FAISS Global Vector (Pass 1), and Salient Patch Matching (Pass 2).
        Returns a list of duplicate groups (lists of media items).
        """
        try:
            if progress_callback: progress_callback("Loading media signatures...", 0)
            
            with self.db.get_connection() as conn:
                # v3.2 normalized join
                query = '''
                    SELECT 
                        m.file_path, m.group_id, m.metadata_json, 
                        m.is_in_trash, m.file_hash, m.capture_date, f.vector_blob, f.salient_blob,
                        g.discovery_method
                    FROM media m
                    LEFT JOIN media_features f ON m.file_path = f.file_path
                    LEFT JOIN duplicate_groups g ON m.group_id = g.group_id
                '''
                all_raw = conn.execute(query).fetchall()
            
            if len(all_raw) < 2: return []

            media_list, path_to_item = [], {}
            for row in all_raw:
                # Data Librarian: Immediate normalization on read to prevent DSU mismatches
                file_path = os.path.normcase(os.path.abspath(row[0]))
                is_in_trash = bool(row[3])
                if not include_trash and is_in_trash: continue
                
                # IMPORTANT: Reset group_id to None during the structural analysis pass.
                # This ensures we don't reuse old, potentially 'loose' group IDs 
                # (like the 9,000+ item groups from v3.1) in our current fresh clustering.
                item = {
                    "file_path": file_path, 
                    "group_id": None, # Force fresh grouping
                    "metadata": json.loads(row[2]) if row[2] else {}, 
                    "is_in_trash": is_in_trash,
                    "file_hash": row[4], # MD5
                    "capture_date": row[5],
                    "vector_blob": row[6], # DINOv2 Vector
                    "salient_blob": row[7], # Stage 2 Patches
                    "discovery_method": row[8]
                }
                media_list.append(item)
                path_to_item[file_path] = item

            if len(media_list) < 2: return []
            
            # Data Librarian: Use absolute normalized paths for DSU to ensure E2E consistency
            all_paths = [os.path.normcase(os.path.abspath(m["file_path"])) for m in media_list]
            dsu = DisjointSetUnion(all_paths)
            group_methods = {} 
            
            # --- PASS 0: Exact File MD5 Matches ---
            if progress_callback: progress_callback("Grouping identical files (MD5)...", 5)
            md5_tracker = {}
            for item in media_list:
                md5 = item["file_hash"]
                if md5:
                    if md5 not in md5_tracker:
                        md5_tracker[md5] = item["file_path"]
                    else:
                        p1, p2 = item["file_path"], md5_tracker[md5]
                        if dsu.union(p1, p2):
                            group_methods[dsu.find(p1)] = "exact"

            # --- PASS 1: AI Global Search (FAISS + DINOv2 CLS) ---
            if progress_callback: progress_callback("AI Global similarity search (FAISS)...", 10)
            candidates = self.find_ai_duplicates(media_list, threshold=threshold)
            
            # --- PASS 2: AI Precise Verification (Salient Patch matching) ---
            if candidates:
                import torch
                total_cand = len(candidates)
                processed = 0
                start_time = time.time()
                
                # 1. Batch extract ALL unique candidates at once (EXCEPTIONAL SPEEDUP)
                if progress_callback: progress_callback("AI Batch Feature Extraction...", 12)
                unique_paths = list(set([p[0] for p in candidates] + [p[1] for p in candidates]))
                
                # Filter out those already grouped or known
                salient_cache = {}
                paths_to_extract = []
                for p in unique_paths:
                    p_norm = os.path.normcase(os.path.abspath(p))
                    item = path_to_item.get(p_norm)
                    if item and item.get("salient_blob"):
                        # Load from DB
                        arr = np.frombuffer(item["salient_blob"], dtype=np.float32).reshape(64, 384)
                        salient_cache[p_norm] = arr
                    else:
                        paths_to_extract.append(p_norm)
                
                if paths_to_extract:
                    # Extract missing salient features (Batch Size 96 as requested)
                    logger.info(f"Extracting salient features for {len(paths_to_extract)} new candidates...")
                    new_features = self.feat_ext.extract_salient_features_batch(paths_to_extract, batch_size=96, progress_callback=progress_callback)

                    # Update local cache and prepare for DB update
                    db_update_list = []
                    for path, feat in new_features.items():
                        if feat is not None:
                            salient_cache[path] = feat
                            db_update_list.append((path, feat.tobytes()))

                    # Persist to media_features table
                    if db_update_list:
                        logger.info(f"Updating DB with {len(db_update_list)} new salient blobs.")
                        self.db.update_salient_features_batch(db_update_list)
                    else:
                        logger.warning("No salient features were extracted despite having paths_to_extract.")
                
                # 2. Batched Similarity Matching (Maximum GPU Throughput)
                if progress_callback: progress_callback("AI Local Patch matching (Parallel)...", 20)
                
                # Filter candidates that actually need verification
                active_candidates = []
                for p1, p2 in candidates:
                    p1_norm = os.path.normcase(os.path.abspath(p1))
                    p2_norm = os.path.normcase(os.path.abspath(p2))
                    if dsu.find(p1_norm) != dsu.find(p2_norm):
                        active_candidates.append((p1_norm, p2_norm))
                
                total_active = len(active_candidates)
                if total_active > 0:
                    # Increased batch size for 8GB VRAM (4096 pairs = ~400MB)
                    sim_batch_size = 4096 
                    for i in range(0, total_active, sim_batch_size):
                        if self.is_cancelled: break
                        
                        batch_pairs = active_candidates[i : i + sim_batch_size]
                        valid_pairs_in_batch = []
                        feats1, feats2 = [], []
                        
                        for p1_norm, p2_norm in batch_pairs:
                            f1 = salient_cache.get(p1_norm)
                            f2 = salient_cache.get(p2_norm)
                            if f1 is not None and f2 is not None:
                                feats1.append(f1)
                                feats2.append(f2)
                                valid_pairs_in_batch.append((p1_norm, p2_norm))
                        
                        if feats1:
                            scores = self.feat_ext.compute_local_similarity_batch(feats1, feats2)

                            for idx, score in enumerate(scores):
                                # Stage 2 threshold comparison
                                if score > stage2_threshold:
                                    p1_norm, p2_norm = valid_pairs_in_batch[idx]

                                    if dsu.union(p1_norm, p2_norm):
                                        is_v1 = p1_norm.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                                        is_v2 = p2_norm.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                                        method = "ai_local_video" if (is_v1 or is_v2) else "ai_local"
                                        group_methods[dsu.find(p1_norm)] = method
                        
                        processed = min(i + sim_batch_size, total_active)
                        if progress_callback:
                            progress_callback(f"AI Local Match (Batch): {processed}/{total_active}", 20 + int((processed / total_active) * 40))
                
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            # --- Reconstruct All Groups ---
            groups_map = {}
            for item in media_list:
                root = dsu.find(item["file_path"])
                if root not in groups_map: groups_map[root] = []
                groups_map[root].append(item)
            
            # Final list of groups, annotated with discovery method
            results = []
            for root, members in groups_map.items():
                if len(members) > 1:
                    method = group_methods.get(root, "exact")
                    for m in members:
                        m["discovery_method"] = method
                    results.append(members)
            
            return results
        except Exception as e:
            logger.error(f"Structural Duplicate Search Error: {e}")
            return []

    def find_ai_duplicates(self, media_list, threshold=0.6):
        """
        Uses FAISS to find globally similar images based on DINOv2 vectors.
        GPU-acceleration: Uses FAISS-GPU if available to process 50k+ images in milliseconds.
        """
        valid_vecs = []
        vec_paths = []
        for item in media_list:
            blob = item.get("vector_blob")
            if blob:
                try:
                    arr = np.frombuffer(blob, dtype=np.float32)
                    # DINOv2 ViT-S/14 is 384-dim
                    if arr.shape[0] == 384:
                        valid_vecs.append(arr)
                        vec_paths.append(item["file_path"])
                except:
                    continue

        if len(valid_vecs) < 2:
            return []

        # Prepare FAISS Index
        data_np = np.vstack(valid_vecs).astype('float32')
        dim = data_np.shape[1]

        # CPU Index First
        cpu_index = faiss.IndexFlatL2(dim)

        # ATTEMPT GPU ACCELERATION
        try:
            # Check for FAISS GPU support
            res = faiss.StandardGpuResources()
            index = faiss.index_cpu_to_gpu(res, 0, cpu_index)
            # logger.info("FAISS-GPU initialization successful.")
        except Exception as e:
            # logger.info(f"FAISS-GPU unavailable, using multi-threaded CPU index: {e}")
            # Ensure multi-threading for CPU index
            faiss.omp_set_num_threads(16)
            index = cpu_index

        index.add(data_np)

        # Radius search: find all pairs with distance < threshold
        # Scaling video threshold relative to the image threshold
        # (Standard image 0.6 -> video 0.4)
        video_threshold = threshold * (0.4 / 0.6)

        lims, dists, indices = index.range_search(data_np, threshold)

        candidates = []
        for i in range(len(lims) - 1):
            for j in range(lims[i], lims[i+1]):
                neighbor_idx = indices[j]
                if i < neighbor_idx: # Only process pairs once
                    p1 = vec_paths[i]
                    p2 = vec_paths[neighbor_idx]
                    dist2 = dists[j]

                    is_v1 = p1.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                    is_v2 = p2.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))

                    # Apply stricter threshold if either is a video
                    if is_v1 or is_v2:
                        if dist2 < video_threshold:
                            candidates.append((p1, p2))
                    else:
                        # Standard image threshold
                        candidates.append((p1, p2))

        return candidates
    def unify_duplicate_hashes(self, groups):
        """
        Unifies the group associations for all members of a duplicate group.
        Assigns the group_id of the 'best' version (based on EXIF and size).
        """
        if not groups: return []
        
        update_list = []
        for group in groups:
            # Sort to find the 'primary' version
            group.sort(key=lambda x: (
                1 if (x["metadata"].get("has_exif_date") or x["metadata"].get("has_location")) else 0,
                x["metadata"].get("size", 0)
            ), reverse=True)
            
            # Priority: prefer 'exact' over 'ai_local' and 'ai_local_video'
            methods = [m.get("discovery_method") for m in group if m.get("discovery_method")]
            if "exact" in methods:
                discovery_method = "exact"
            elif any(m in methods for m in ["ai_local_video", "ai_video_global"]):
                discovery_method = "ai_local_video"
            elif "ai_local" in methods:
                discovery_method = "ai_local"
            else:
                discovery_method = "ai_local" if "sim:" in (group[0]["group_id"] or "") else "exact"

            primary_id = group[0]["group_id"]
            # Fallback if primary doesn't have an ID
            if not primary_id:
                import hashlib
                primary_id = "sim:" + hashlib.md5(group[0]["file_path"].encode()).hexdigest()[:16]

            for item in group:
                update_list.append((primary_id, discovery_method, item["file_path"]))
        
        if update_list:
            self.db.update_image_hashes_batch(update_list)
        return update_list

    def mark_file_as_trashed(self, old_path, new_path, item):
        """Updates the database when a file is moved to trash."""
        # Using normalized v3.2 input format
        self.db.add_media_batch([(
            new_path, 
            item.get("last_modified", 0), 
            json.dumps(item["metadata"]), 
            item.get("group_id"),
            item["metadata"].get("lat"), item["metadata"].get("lon"), item["metadata"].get("alt"),
            item["metadata"].get("country"), item["metadata"].get("prefecture"), item["metadata"].get("city"),
            item["metadata"].get("year"), item["metadata"].get("month"),
            item.get("thumbnail_path"),
            1 if item["metadata"].get("corrupted") else 0,
            1, # is_in_trash
            item["metadata"].get("date_taken"), # capture_date (15)
            item.get("file_hash"), # md5 (16)
            item.get("vector_blob") # vector (17)
        )])
        
        # If the path changed, remove the old DB entry to avoid orphans
        if new_path != old_path:
            self.db.delete_media(old_path)

    def restore_file_from_trash(self, file_path):
        """
        Restores a file from trash both physically and in the database.
        Returns the new physical path of the file.
        """
        media_info = self.db.get_media(file_path)
        if not media_info: return file_path
        
        new_path = file_path
        upath = file_path.upper()
        trash_markers = [".TRASH", "_TRASH", "RECYCLE.BIN"]
        
        target_marker = None
        for marker in trash_markers:
            if marker in upath:
                target_marker = marker
                break
        
        if target_marker:
            try:
                parts = file_path.replace("\\", "/").split("/")
                trash_idx = -1
                for i, p in enumerate(parts):
                    if p.upper() == target_marker:
                        trash_idx = i
                        break
                
                if trash_idx > 0:
                    parent_dir = "/".join(parts[:trash_idx])
                    restore_dir = os.path.join(parent_dir, "restore")
                    os.makedirs(restore_dir, exist_ok=True)
                    
                    base_name = os.path.basename(file_path)
                    dest_path = os.path.join(restore_dir, base_name)
                    
                    if os.path.exists(dest_path):
                        name, ext = os.path.splitext(base_name)
                        counter = 1
                        while os.path.exists(os.path.join(restore_dir, f"{name}_{counter}{ext}")):
                            counter += 1
                        dest_path = os.path.join(restore_dir, f"{name}_{counter}{ext}")
                    
                    if os.path.exists(file_path):
                        shutil.move(file_path, dest_path)
                        new_path = dest_path
                        if new_path != file_path:
                            self.db.delete_media(file_path)
            except Exception as e:
                logger.error(f"Physical restore error for {file_path}: {e}")

        # Update Database (is_in_trash=0)
        # Flip is_in_trash to 0 and ensure new_path is used.
        # media_info is the 19-column list returned by db.get_media(file_path).
        media_list_data = list(media_info)
        media_list_data[0] = new_path
        media_list_data[14] = 0 # is_in_trash
        self.db.add_media_batch([tuple(media_list_data)])
        return new_path
