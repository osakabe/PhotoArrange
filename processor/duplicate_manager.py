import logging
import os
import shutil
from dataclasses import replace
from typing import Iterable, Optional

import faiss
import numpy as np
import torch

from core.config import AppConfig
from core.models import MediaRecord
from core.repositories.setting_repository import SettingRepository
from core.utils import Profiler, normalize_path

logger = logging.getLogger("PhotoArrange")


class DisjointSetUnion:
    def __init__(self, elements: Iterable[str]):
        self.parent = {el: el for el in elements}
        self.size = {el: 1 for el in elements}

    def find(self, i: str) -> str:
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: str, j: str) -> bool:
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
        self.is_cancelled = False  # Track cancellation from worker thread

        # Load central configuration
        self.config = AppConfig.load(SettingRepository(db.db_path))

    def find_structural_duplicates(
        self, threshold=None, stage2_threshold=None, include_trash=False, progress_callback=None
    ) -> list[list[MediaRecord]]:
        """
        Performs a global multi-pass search for duplicates.
        Passes: MD5 (Pass 0), FAISS Global Vector (Pass 1), and Salient Patch Matching (Pass 2).
        Returns a list of duplicate groups (lists of media items).
        """
        threshold = threshold if threshold is not None else self.config.threshold
        stage2_thresh = (
            stage2_threshold if stage2_threshold is not None else self.config.dup_threshold_stage2
        )

        with Profiler("DuplicateManager.find_structural_duplicates"):
            try:
                media_list, path_to_item = self._load_and_filter_media(
                    include_trash, progress_callback
                )
                if len(media_list) < 2:
                    return []

                dsu = DisjointSetUnion([m.file_path for m in media_list])
                group_methods: dict[str, str] = {}

                self._pass0_exact_matches(media_list, dsu, group_methods, progress_callback)

                if progress_callback:
                    progress_callback("AI Global similarity search (FAISS)...", 10)
                candidates = self.find_ai_duplicates(media_list, threshold=threshold)

                if candidates:
                    self._pass2_local_patch_matching(
                        candidates,
                        path_to_item,
                        dsu,
                        group_methods,
                        stage2_thresh,
                        progress_callback,
                    )

                return self._reconstruct_groups(media_list, dsu, group_methods)
            except Exception:
                logger.exception("Structural Duplicate Search Error:")
                return []

    def _load_and_filter_media(
        self, include_trash: bool, progress_callback
    ) -> tuple[list[MediaRecord], dict[str, MediaRecord]]:
        if progress_callback:
            progress_callback("Loading media signatures...", 0)
        with self.db.get_connection() as conn:
            query = """
                SELECT m.file_path, m.group_id, m.metadata_json, m.is_in_trash, m.file_hash, m.capture_date, f.vector_blob, f.salient_blob, g.discovery_method
                FROM media m
                LEFT JOIN media_features f ON m.file_path = f.file_path
                LEFT JOIN duplicate_groups g ON m.group_id = g.group_id
                WHERE m.file_hash IS NOT NULL AND m.file_hash != ''
            """
            all_raw = conn.execute(query).fetchall()

        media_list: list[MediaRecord] = []
        path_to_item: dict[str, MediaRecord] = {}
        for row in all_raw:
            file_path = normalize_path(row[0])
            if not include_trash and bool(row[3]):
                continue

            m = MediaRecord.from_duplicate_search(row)
            m = replace(m, file_path=file_path, group_id=None)
            media_list.append(m)
            path_to_item[file_path] = m
        return media_list, path_to_item

    def _pass0_exact_matches(
        self,
        media_list: list[MediaRecord],
        dsu: DisjointSetUnion,
        group_methods: dict,
        progress_callback,
    ):
        if progress_callback:
            progress_callback("Grouping identical files (MD5)...", 5)
        md5_tracker = {}
        for m in media_list:
            md5 = m.file_hash
            if md5:
                if md5 not in md5_tracker:
                    md5_tracker[md5] = m.file_path
                else:
                    p1, p2 = m.file_path, md5_tracker[md5]
                    if dsu.union(p1, p2):
                        group_methods[dsu.find(p1)] = "exact"

    def _pass2_local_patch_matching(
        self,
        candidates,
        path_to_item: dict,
        dsu: DisjointSetUnion,
        group_methods: dict,
        stage2_threshold,
        progress_callback,
    ):
        if progress_callback:
            progress_callback("AI Batch Feature Extraction...", 12)

        salient_cache = self._ensure_salient_features(candidates, path_to_item, progress_callback)

        if progress_callback:
            progress_callback("AI Local Patch matching (Parallel)...", 20)

        active_candidates = [
            (normalize_path(p1), normalize_path(p2))
            for p1, p2 in candidates
            if dsu.find(normalize_path(p1)) != dsu.find(normalize_path(p2))
        ]

        if not active_candidates:
            return

        self._process_similarity_batches(
            active_candidates,
            salient_cache,
            dsu,
            group_methods,
            stage2_threshold,
            progress_callback,
        )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _ensure_salient_features(self, candidates, path_to_item, progress_callback) -> dict:
        unique_paths = list(set([p[0] for p in candidates] + [p[1] for p in candidates]))
        salient_cache = {}
        paths_to_extract = []
        for p in unique_paths:
            p_norm = normalize_path(p)
            m = path_to_item.get(p_norm)
            if m and m.salient_blob:
                arr = np.frombuffer(m.salient_blob, dtype=np.float32).reshape(64, 384)
                salient_cache[p_norm] = arr
            else:
                paths_to_extract.append(p_norm)

        if paths_to_extract:
            logger.info(f"Extracting salient features for {len(paths_to_extract)} items...")
            new_features = self.feat_ext.extract_salient_features_batch(
                paths_to_extract, batch_size=96, progress_callback=progress_callback
            )
            db_update_list = []
            for path, feat in new_features.items():
                if feat is not None:
                    salient_cache[path] = feat
                    db_update_list.append((path, feat.tobytes()))
            if db_update_list:
                self.db.update_salient_features_batch(db_update_list)
        return salient_cache

    def _process_similarity_batches(
        self,
        active_candidates,
        salient_cache,
        dsu,
        group_methods,
        stage2_threshold,
        progress_callback,
    ):
        total_active = len(active_candidates)
        sim_batch_size = 4096
        for i in range(0, total_active, sim_batch_size):
            if self.is_cancelled:
                break
            batch_pairs = active_candidates[i : i + sim_batch_size]
            valid_pairs, feats1, feats2 = [], [], []

            for p1, p2 in batch_pairs:
                f1, f2 = salient_cache.get(p1), salient_cache.get(p2)
                if f1 is not None and f2 is not None:
                    feats1.append(f1)
                    feats2.append(f2)
                    valid_pairs.append((p1, p2))

            if feats1:
                scores = self.feat_ext.compute_local_similarity_batch(feats1, feats2)
                for idx, score in enumerate(scores):
                    if score > stage2_threshold:
                        p1, p2 = valid_pairs[idx]
                        if dsu.union(p1, p2):
                            is_v = any(
                                p.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
                                for p in [p1, p2]
                            )
                            group_methods[dsu.find(p1)] = "ai_local_video" if is_v else "ai_local"

            processed = min(i + sim_batch_size, total_active)
            if progress_callback:
                progress_callback(
                    f"AI Local Match: {processed}/{total_active}",
                    20 + int((processed / total_active) * 40),
                )

    def _reconstruct_groups(
        self, media_list: list[MediaRecord], dsu: DisjointSetUnion, group_methods: dict
    ) -> list[list[MediaRecord]]:
        groups_map = {}
        for m in media_list:
            root = dsu.find(m.file_path)
            if root not in groups_map:
                groups_map[root] = []
            groups_map[root].append(m)

        results = []
        for root, members in groups_map.items():
            if len(members) > 1:
                method = group_methods.get(root, "exact")
                annotated_members = [replace(m, discovery_method=method) for m in members]
                results.append(annotated_members)
        return results

    def find_ai_duplicates(
        self, media_list: list[MediaRecord], threshold: Optional[float] = None
    ) -> list[tuple[str, str, float]]:
        """
        Uses FAISS to find globally similar images based on DINOv2 vectors.
        """
        threshold = threshold if threshold is not None else self.config.threshold
        with Profiler(f"DuplicateManager.find_ai_duplicates (items={len(media_list)})"):
            valid_vecs, vec_paths = [], []
            for m in media_list:
                if m.vector_blob:
                    try:
                        arr = np.frombuffer(m.vector_blob, dtype=np.float32)
                        if arr.shape[0] == 384:
                            valid_vecs.append(arr)
                            vec_paths.append(m.file_path)
                    except (ValueError, TypeError):
                        continue

            if len(valid_vecs) < 2:
                return []

            index = self._build_faiss_index(np.vstack(valid_vecs).astype("float32"))
            video_threshold = threshold * self.config.video_threshold_ratio
            lims, dists, indices = index.range_search(
                np.vstack(valid_vecs).astype("float32"), threshold
            )

            candidates = []
            for i in range(len(lims) - 1):
                for j in range(lims[i], lims[i + 1]):
                    if i < indices[j]:
                        p1, p2 = vec_paths[i], vec_paths[indices[j]]
                        is_v = any(
                            p.lower().endswith((".mp4", ".avi", ".mov", ".mkv")) for p in [p1, p2]
                        )
                        if not is_v or dists[j] < video_threshold:
                            candidates.append((p1, p2))
            return candidates

    def _build_faiss_index(self, data_np: np.ndarray) -> faiss.Index:
        dim = data_np.shape[1]
        cpu_index = faiss.IndexFlatL2(dim)
        try:
            res = faiss.StandardGpuResources()
            index = faiss.index_cpu_to_gpu(res, 0, cpu_index)
        except Exception:
            faiss.omp_set_num_threads(16)
            index = cpu_index
        index.add(data_np)
        return index

    def unify_duplicate_hashes(self, groups: list[list[MediaRecord]]):
        """
        Unifies the group associations for all members of a duplicate group.
        Assigns the group_id of the 'best' version (based on EXIF and size).
        """
        if not groups:
            return []

        sorted_groups = []
        for group in groups:
            # Sort to find the 'primary' version
            group.sort(
                key=lambda x: (
                    1 if (x.metadata.get("has_exif_date") or x.metadata.get("has_location")) else 0,
                    x.metadata.get("size", 0),
                ),
                reverse=True,
            )

            # Determine method
            methods = {m.discovery_method for m in group if m.discovery_method}
            if "exact" in methods:
                unified_method = "exact"
            elif any(m in methods for m in ["ai_local_video", "ai_video_global"]):
                unified_method = "ai_local_video"
            else:
                unified_method = "ai_local"

            annotated_group = [replace(m, discovery_method=unified_method) for m in group]
            sorted_groups.append(annotated_group)

        if sorted_groups:
            self.db.unify_duplicate_hashes(sorted_groups)
        return sorted_groups

    def mark_file_as_trashed(self, old_path: str, new_path: str, item: MediaRecord) -> None:
        """Updates the database when a file is moved to trash."""
        trashed_record = replace(item, file_path=new_path, is_in_trash=True)
        self.db.add_media_batch([trashed_record])
        if new_path != old_path:
            self.db.delete_media(old_path)

    def restore_file_from_trash(self, file_path: str) -> str:
        """
        Restores a file from trash both physically and in the database.
        Returns the new physical path of the file.
        """
        media_info = self.db.get_media(file_path)
        if not media_info:
            return file_path

        new_path = file_path
        target_marker = next(
            (m for m in [".TRASH", "_TRASH", "RECYCLE.BIN"] if m in file_path.upper()), None
        )

        if target_marker:
            new_path = self._execute_physical_restore(file_path, target_marker)

        # Update Database (is_in_trash=0)
        restored_record = replace(media_info, file_path=new_path, is_in_trash=False)
        self.db.add_media_batch([restored_record])
        return new_path

    def _execute_physical_restore(self, file_path: str, target_marker: str) -> str:
        try:
            parts = file_path.replace("\\", "/").split("/")
            trash_idx = next((i for i, p in enumerate(parts) if p.upper() == target_marker), -1)

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
                    if dest_path != file_path:
                        self.db.delete_media(file_path)
                    return dest_path
        except Exception as e:
            logger.error(f"Physical restore error for {file_path}: {e}")
        return file_path
