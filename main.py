import os
import sys

# 1. CRITICAL: Initialize environment and DLL search paths before ANY other imports
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
from core.utils import fix_dll_search_path
fix_dll_search_path()

# 2. Standard library and third-party imports
import sqlite3
import numpy as np
import shutil
import logging
import json
from datetime import datetime
from PIL import Image

from core.utils import get_app_data_dir, get_face_cache_dir




import time
import send2trash
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QProgressBar, QFileDialog,
                             QSplitter, QLabel, QMessageBox, QFrame, QMenu, QCheckBox,
                             QStatusBar, QComboBox)


from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QIcon, QAction


# Local imports
from core.database import Database
from processor.person_logic import PersonManagementWorker, PersonAction
from ui.widgets.tree_view import MediaTreeView
from ui.widgets.thumbnail_grid import ThumbnailGrid
from ui.theme import get_style_sheet
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.person_manager import PersonManagerDialog
from ui.dialogs.face_verification import FaceVerificationDialog
from ui.widgets.face_manager_view import FaceManagerView

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def move_file_to_local_trash(file_path, root_folder):
    """
    Moves a file to a local '.trash' directory within the root folder.
    Also migrates its thumbnail so it doesn't lose its 'image' in the UI.
    Returns the new path of the file.
    """
    if not root_folder:
        return file_path
    
    trash_dir = os.path.join(root_folder, ".trash")
    try:
        os.makedirs(trash_dir, exist_ok=True)
        
        base_name = os.path.basename(file_path)
        dest_path = os.path.join(trash_dir, base_name)
        
        # Handle collision
        if os.path.exists(dest_path):
            name, ext = os.path.splitext(base_name)
            counter = 1
            while os.path.exists(os.path.join(trash_dir, f"{name}_{counter}{ext}")):
                counter += 1
            dest_path = os.path.join(trash_dir, f"{name}_{counter}{ext}")
            
        # Migrate thumbnail before moving file (so we have the old path)
        try:
            from processor.image_processor import ImageProcessor
            img_proc = ImageProcessor()
            old_thumb = img_proc.get_thumbnail_path(file_path)
            if os.path.exists(old_thumb):
                new_thumb = img_proc.get_thumbnail_path(dest_path)
                # Check if dest thumb already exists, if so delete it
                if os.path.exists(new_thumb): os.remove(new_thumb)
                shutil.copy2(old_thumb, new_thumb) # Use copy to be safe, or move
        except Exception as te:
            logger.error(f"Failed to migrate thumbnail during trash move: {te}")

        shutil.move(file_path, dest_path)
        return dest_path
    except Exception as e:
        logger.error(f"Error moving file to local trash: {e}")
        return file_path

TRASH_NAMES = {'$RECYCLE.BIN', '.TRASH', 'TRASH', '.TRASH-1000', 'TRASH-1000'}

class WorkerBase(QThread):
    progress_val = Signal(int)
    phase_status = Signal(str)
    finished_all = Signal(bool, str)

    def __init__(self, folder_path, db, include_trash_folders=False, face_det_thresh=0.35):
        super().__init__()
        self.folder_path = os.path.abspath(os.path.normpath(folder_path))
        self.db = db
        self.include_trash_folders = include_trash_folders
        self.is_cancelled = False
        self.face_det_thresh = face_det_thresh
        from processor.image_processor import ImageProcessor
        from processor.geo_processor import GeoProcessor
        from processor.face_processor import FaceProcessor
        from processor.duplicate_manager import DuplicateManager
        from processor.feature_extractor import FeatureExtractor

        self.img_proc = ImageProcessor()
        self.geo_proc = GeoProcessor()
        self.face_proc = FaceProcessor(det_thresh=self.face_det_thresh)
        self.feat_ext = FeatureExtractor()
        self.duplicate_mgr = DuplicateManager(self.db, self.img_proc, self.feat_ext)

    def stop(self):
        self.is_cancelled = True
        if hasattr(self, 'duplicate_mgr'):
            self.duplicate_mgr.is_cancelled = True
        
        # Rule 7: Always empty cache on Worker stop
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def scan_files(self):
        files = []
        for root, dirs, filenames in os.walk(self.folder_path):
            if self.is_cancelled: return []
            
            if not self.include_trash_folders:
                dirs[:] = [d for d in dirs if d.upper() not in TRASH_NAMES and not d.startswith('.') and d not in ('.git', '__pycache__')]
            else:
                dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
            
            for f in filenames:
                if f.startswith('.') and not self.include_trash_folders: continue
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.mp4', '.avi', '.mov')):
                    # Use normcase and abspath to ensure a unique, consistent path key for Windows
                    full_path = os.path.normcase(os.path.abspath(os.path.join(root, f)))
                    files.append(full_path)
        return files

class FileSyncWorker(WorkerBase):
    """
    Fast synchronization between disk and DB.
    Deletes orphans and adds new files as placeholders.
    """
    def __init__(self, folder_path, db, include_trash_folders=False):
        super().__init__(folder_path, db, include_trash_folders)

    def run(self):
        try:
            self.phase_status.emit("Cleaning up library...")
            from concurrent.futures import ThreadPoolExecutor

            # Phase 0: Global Case-Normalization & Orphan Cleanup
            all_db_paths = self.db.get_all_media_paths()
            if all_db_paths:
                self.phase_status.emit(f"Verifying {len(all_db_paths)} library items...")
                self.progress_val.emit(0)
                        
                # Group DB paths by their normalized equivalent
                norm_map = {} # normalized -> list of original
                for p in all_db_paths:
                    n = os.path.normcase(os.path.abspath(p))
                    if n not in norm_map: norm_map[n] = []
                    norm_map[n].append(p)
                
                unique_norm_paths = list(norm_map.keys())
                
                # --- Parallel Existence Check ---
                def check_file(np):
                    return np, os.path.exists(np)
                
                real_orphans = []
                merges_to_perform = []
                
                with ThreadPoolExecutor(max_workers=32) as executor:
                    futures = [executor.submit(check_file, np) for np in unique_norm_paths]
                    for i, future in enumerate(futures):
                        if self.is_cancelled: return
                        norm_p, exists = future.result()
                        db_paths = norm_map[norm_p]
                        
                        if not exists:
                            real_orphans.extend(db_paths)
                        elif len(db_paths) > 1:
                            # Group all variants into one transaction later
                            db_paths.sort(reverse=True)
                            merges_to_perform.append((db_paths[0], db_paths[1:]))
                            
                        # Update progress every 100 items
                        if i % 100 == 0:
                            self.progress_val.emit(int((i / len(unique_norm_paths)) * 100))
                
                # --- Batch Database Updates ---
                if merges_to_perform:
                    self.phase_status.emit(f"Merging {len(merges_to_perform)} path variants...")
                    self.db.merge_duplicate_paths_batch(merges_to_perform)
                
                if real_orphans:
                    self.phase_status.emit(f"Pruning {len(real_orphans)} ghost records...")
                    self.db.delete_media_batch(real_orphans)
                
                self.progress_val.emit(100)

            self.phase_status.emit("Scanning current folder...")
            disk_files = self.scan_files()
            if self.is_cancelled: return
            
            db_paths = set(self.db.get_media_paths_in_folder(self.folder_path))
            disk_paths = set(disk_files)
            
            # 1. DELETE ORPHANS
            orphans = list(db_paths - disk_paths)
            if orphans and not self.is_cancelled:
                self.phase_status.emit(f"Removing {len(orphans)} missing items...")
                self.db.delete_media_batch(orphans)
            
            # 2. ADD NEW AS PLACEHOLDERS & GENERATE THUMBNAILS
            new_paths = list(disk_paths - db_paths)
            if new_paths and not self.is_cancelled:
                self.phase_status.emit(f"Adding {len(new_paths)} items & generating thumbnails...")
                batch = []
                
                # Use ThreadPoolExecutor to generate thumbnails in parallel for fast UX
                from concurrent.futures import ThreadPoolExecutor
                def thumb_task(p):
                    if self.is_cancelled: return
                    try:
                        self.img_proc.generate_thumbnail(p)
                    except:
                        pass

                with ThreadPoolExecutor(max_workers=8) as executor:
                    for i in range(0, len(new_paths), 500):
                        if self.is_cancelled: break
                        chunk = new_paths[i:i+500]
                        
                        # Generate thumbs for this chunk
                        executor.map(thumb_task, chunk)
                        
                        # Prepare DB records
                        db_batch = []
                        for p in chunk:
                            try:
                                mtime = os.path.getmtime(p)
                                is_in_trash = 1 if any(tn in p.upper() for tn in TRASH_NAMES) else 0
                                # Calculate thumb path immediately for DB caching
                                th_p = self.img_proc.get_thumbnail_path(p)
                                # MD5 immediately for Pass 0
                                file_md5 = self.img_proc.get_file_hash(p)
                                # V3.2 18-column tuple: 
                                # 0:path, 1:modified, 2:meta, 3:hash, 4:lat, 5:lon, 6:alt, 7:country, 8:pref, 9:city, 10:y, 11:m, 
                                # 12:thumbnail, 13:corrupted, 14:trash, 15:capture_date, 16:file_hash, 17:vector
                                db_batch.append((
                                    p, mtime, "{}", None, 
                                    None, None, None, None, None, None,
                                    None, None, th_p, 0, is_in_trash, None, 
                                    file_md5, None
                                ))
                            except Exception as e:
                                logger.error(f"Error prepping DB record for {p}: {e}")
                        
                        self.db.add_media_batch(db_batch)
                        self.progress_val.emit(int((i + len(chunk)) / len(new_paths) * 100))
            
            self.finished_all.emit(True, "Sync complete.")
        except Exception as e:
            logger.exception("FileSync Error:")
            self.finished_all.emit(False, str(e))

class DuplicateAnalysisWorker(WorkerBase):
    """Specialized in finding duplicates. Extracts global embeddings and performs local patch matching."""
    def __init__(self, folder_path, db, include_trash_folders=False, force_reanalyze=False, threshold=0.6, stage2_threshold=0.95):
        super().__init__(folder_path, db, include_trash_folders)
        self.force_reanalyze = force_reanalyze
        self.threshold = threshold
        self.stage2_threshold = stage2_threshold

    def run(self):
        try:
            self.phase_status.emit("Starting duplicate analysis...")
            
            # Reset existing AI-based groupings for this scope to allow fresh clustering with new thresholds
            self.db.clear_ai_duplicate_groups(self.folder_path)
            
            files = self.scan_files()
            total = len(files)
            if total == 0:
                self.finished_all.emit(True, "No files to analyze.")
                return

            import queue
            import threading
            from threading import Thread
            from concurrent.futures import ThreadPoolExecutor
            
            # Phase 1: Hash & Embedding Pass
            batch_size = 256
            prepped_queue = queue.Queue(maxsize=1024)
            
            # --- PROGRESS METRICS ---
            processed = 0
            start_time = time.time()
            video_counter = [0]
            v_lock = threading.Lock()
            
            def producer():
                # Map ALL files once to keep 8 threads pinned at 100% capacity
                with ThreadPoolExecutor(max_workers=8) as executor:
                    def process_single(file_path):
                        if self.is_cancelled: return None
                        try:
                            mtime = os.path.getmtime(file_path)
                            cached = self.db.get_media(file_path)
                            
                            h_val = cached[3] if cached else None
                            # V3.2 schema: vector_blob is index 17, file_hash is 16
                            has_vector = (cached is not None and len(cached) > 17 and cached[17] is not None)
                            f_hash = cached[16] if cached and len(cached) > 16 else None
                            
                            thumb_path = (cached[12] if cached and len(cached) > 12 and cached[12] else None) or self.img_proc.get_thumbnail_path(file_path)
                            thumb_exists = os.path.exists(thumb_path) if thumb_path else False
                            
                            # FAST SKIP: Only re-analyze if modified or requested FORCE
                            # Pure AI-Native Skip Logic: Only re-analyze if modified or forced
                            if cached and cached[1] == mtime and not self.force_reanalyze and thumb_exists and f_hash and has_vector:
                                return ("CACHED", file_path)

                            # --- FULL EXTRACTION ---
                            if not thumb_exists:
                                thumb_path = self.img_proc.generate_thumbnail(file_path)
                            
                            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                            metadata = self.img_proc.get_video_metadata(file_path) if is_video else self.img_proc.get_metadata(file_path)
                            
                            # We now rely exclusively on DINOv2 (AI) and MD5 (Exact)
                            # PRESERVE existing hash to avoid visual "flicker" or data loss in UI
                            img_hash = h_val 
                            file_md5 = self.img_proc.get_file_hash(file_path)
                            
                            ai_tensor = None
                            video_vec = None

                            if is_video:
                                # Extract 5 frames for Stage 1 averaging
                                frames = self.img_proc.extract_video_frames(file_path, num_frames=5)
                                if frames:
                                    # Extract features for these frames (Sequential here, but fast for 5 frames)
                                    video_vec = self.feat_ext.extract_features_from_video([f[0] for f in frames])
                                    
                                    # VRAM Management: Rule 7 - Clean up every 10 videos
                                    with v_lock:
                                        video_counter[0] += 1
                                        if video_counter[0] % 10 == 0:
                                            import torch
                                            if torch.cuda.is_available():
                                                torch.cuda.empty_cache()
                            else:
                                # PRE-PROCESS TENSOR ON CPU THREAD for batching
                                ai_tensor = self.feat_ext.prepare_tensor(thumb_path or file_path)
                                if ai_tensor is not None:
                                    ai_tensor = ai_tensor.pin_memory()
                            
                            lat, lon, alt = metadata.get('lat', 0), metadata.get('lon', 0), metadata.get('alt', 0)
                            country, prefecture, city = None, None, None
                            if (lat != 0 or lon != 0) and (not cached or (cached[4] != lat or cached[5] != lon)):
                                loc = self.geo_proc.get_location(lat, lon)
                                if loc: country, prefecture, city = loc.get('country'), loc.get('prefecture'), loc.get('city')

                            year, month = metadata.get('year', 0), metadata.get('month', 0)
                            cap_date = metadata.get('date_taken', '')
                            is_in_trash = 1 if any(tn in file_path.upper() for tn in TRASH_NAMES) else 0
                            is_corrupted = 1 if metadata.get('corrupted') else 0
                            
                            # --- TUPLE SCHEMA (v3.2.0 - 18 columns for DB batch) ---
                            # 0:NEW, 1:path, 2:mtime, 3:meta_json, 4:h_val, 5:lat, 6:lon, 7:alt, 8:country, 9:pref, 10:city, 
                            # 11:yr, 12:mo, 13:trash, 14:cap_date, 15:corrupted, 16:md5, 17:thumb, 18:ai_tensor, 19:v_vec
                            return ("NEW", file_path, mtime, json.dumps(metadata), img_hash, lat, lon, alt, country, prefecture, city, year, month, is_in_trash, cap_date, is_corrupted, file_md5, thumb_path, ai_tensor, video_vec)
                        except Exception as e:
                            logger.error(f"Analysis Thread Error for {file_path}: {e}")
                            # Error fallback tuple (must match schema length 20)
                            return ("NEW", file_path, None, "{}", None, 0, 0, 0, None, None, None, 0, 0, 0, "", 1, None, None, None, None)

                    # Map entire list without intermediate chunking to avoid worker starvation
                    for res in executor.map(process_single, files):
                        if self.is_cancelled: break
                        if res: prepped_queue.put(res)
                
                prepped_queue.put("DONE")

            Thread(target=producer, daemon=True).start()

            seen_done = False
            while not self.is_cancelled:
                batch = []
                while len(batch) < batch_size and not self.is_cancelled:
                    try:
                        item = prepped_queue.get(timeout=1.0)
                        if item == "DONE":
                            seen_done = True
                            break
                        batch.append(item)
                    except queue.Empty:
                        if seen_done: break
                        continue
                
                if not batch and seen_done: break

                # --- PART 1: BATCH GPU INFERENCE ---
                new_items = [res for res in batch if res[0] == "NEW"]
                if new_items:
                    # Index 18 is ai_tensor (torch.Tensor for batching)
                    tensors = [res[18] for res in new_items if res[18] is not None]
                    valid_tensor_indices = [idx for idx, res in enumerate(new_items) if res[18] is not None]
                    
                    vectors = [None] * len(new_items)
                    # Index 19 is video_vec (already averaged numpy array from producer)
                    for idx, res in enumerate(new_items):
                        if res[19] is not None: 
                            vectors[idx] = res[19]

                    if tensors:
                        try:
                            # Update UI while GPU is busy
                            self.phase_status.emit(f"AI Hash/Embedding {processed}/{total} [GPU ACTIVE]")
                            batch_vectors = self.feat_ext.extract_features_from_tensors(tensors)
                            for v_idx, vec in enumerate(batch_vectors):
                                vectors[valid_tensor_indices[v_idx]] = vec
                        except Exception as ai_e:
                            logger.error(f"GPU Batch Inference Error: {ai_e}")
                    
                    # Prepare batch for normalized DB storage using 18-column schema
                    db_batch = []
                    for idx, res in enumerate(new_items):
                        vec_blob = vectors[idx].tobytes() if vectors[idx] is not None else None
                        # V3.2 index mapping (must match database.py 18-column add_media_batch)
                        db_batch.append((
                            res[1], res[2], res[3], res[4],  # path, mtime, meta, hash
                            res[5], res[6], res[7],          # lat, lon, alt
                            res[8], res[9], res[10],         # country, pref, city
                            res[11], res[12],                # yr, mo
                            res[17], res[15], res[13],       # thumb, corrupted, trash
                            res[14], res[16],                # cap_date, file_hash (index 16)
                            vec_blob                         # vector (index 17)
                        ))
                    
                    if db_batch:
                        self.db.add_media_batch(db_batch)

                # VRAM Management: Rule 7 - Clean up GPU memory every 500 items
                if processed > 0 and processed % 500 == 0:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                
                processed += len(batch)
                elapsed = time.time() - start_time
                fps = processed / elapsed if elapsed > 0 else 0
                eta = int((total - processed) / fps) if fps > 0 else 0
                time_str = time.strftime('%H:%M:%S', time.gmtime(eta)) if eta < 86400 else "> 24h"
                
                prog_msg = f"Analyzing {processed}/{total} ({fps:.1f} fps) - ETA: {time_str}"
                self.phase_status.emit(prog_msg)
                self.progress_val.emit(int(processed / total * 60))

            # Phase 2: Structural Analysis
            if not self.is_cancelled:
                self.phase_status.emit("Global Structural AI Analysis...")
                
                # IMPORTANT: Clear existing AI groups globally before fresh clustering.
                # This prevents old 'loose' associations from persisting when items
                # should now be singletons or in smaller groups.
                self.db.clear_ai_duplicate_groups()

                def on_analysis_prog(msg, val):
                    self.phase_status.emit(msg)
                    self.progress_val.emit(60 + int(val * 0.4))
                
                groups = self.duplicate_mgr.find_structural_duplicates(
                    threshold=self.threshold,
                    stage2_threshold=self.stage2_threshold,
                    include_trash=self.include_trash_folders,
                    progress_callback=on_analysis_prog
                )
                if groups:
                    self.duplicate_mgr.unify_duplicate_hashes(groups)

            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
            self.finished_all.emit(True, "Duplicate analysis complete.")
        except Exception as e:
            logger.exception("DuplicateAnalysis Error:")
            self.finished_all.emit(False, str(e))

class DuplicateRegroupingWorker(WorkerBase):
    """
    Fast grouping redo: skips extraction and only performs 
    global FAISS search + salient patch verification on existing DB data.
    """
    def __init__(self, folder_path, db, include_trash_folders=False, threshold=0.6, stage2_threshold=0.95):
        super().__init__(folder_path, db, include_trash_folders)
        self.threshold = threshold
        self.stage2_threshold = stage2_threshold

    def run(self):
        try:
            self.phase_status.emit("Starting AI Fast Regrouping...")
            
            # Reset existing AI-based groupings for this scope to allow fresh clustering with new thresholds
            self.db.clear_ai_duplicate_groups(self.folder_path)
            
            self.progress_val.emit(10)
            
            def on_analysis_prog(msg, val):
                self.phase_status.emit(msg)
                self.progress_val.emit(10 + int(val * 0.8))
            
            groups = self.duplicate_mgr.find_structural_duplicates(
                threshold=self.threshold,
                stage2_threshold=self.stage2_threshold,
                include_trash=self.include_trash_folders,
                progress_callback=on_analysis_prog
            )
            if groups:
                self.duplicate_mgr.unify_duplicate_hashes(groups)

            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
            self.finished_all.emit(True, "AI grouping redo complete.")
        except Exception as e:
            logger.exception("Regrouping Error:")
            self.finished_all.emit(False, str(e))

class FaceRecognitionWorker(WorkerBase):
    """
    Specialized in finding faces. Runs AI inference and clustering.
    """
    def __init__(self, folder_path, db, include_trash_folders=False, force_reanalyze=False, 
                 min_samples=2, eps=0.42, det_thresh=0.35):
        super().__init__(folder_path, db, include_trash_folders, face_det_thresh=det_thresh)
        self.force_reanalyze = force_reanalyze
        self.min_samples = min_samples
        self.eps = eps

    def run(self):
        try:
            self.phase_status.emit("Starting AI Face Recognition...")
            files = self.scan_files()
            total = len(files)
            if total == 0:
                self.finished_all.emit(True, "No files to analyze.")
                return

            import queue
            from threading import Thread
            from concurrent.futures import ThreadPoolExecutor

            batch_size = 16 # GPU inference batch
            prepped_queue = queue.Queue(maxsize=batch_size * 2)

            def producer():
                with ThreadPoolExecutor(max_workers=8) as executor:
                    def process_single(file_path):
                        if self.is_cancelled: return None
                        try:
                            mtime = os.path.getmtime(file_path)
                            cached = self.db.get_media(file_path)
                            
                            # Check if faces already exist for this file path
                            # We check the faces table
                            with self.db.get_connection() as conn:
                                cur = conn.execute("SELECT 1 FROM faces WHERE file_path = ? LIMIT 1", (file_path,))
                                has_faces = cur.fetchone() is not None
                            
                            # Fallback: Check if thumbnail exists. If missing, treat as "NEW" to regenerate it.
                            thumb_exists = os.path.exists(self.img_proc.get_thumbnail_path(file_path))

                            if cached and cached[1] == mtime and not self.force_reanalyze and has_faces and thumb_exists:
                                return ("CACHED", file_path)

                            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                            p_img = None
                            if is_video:
                                p_img = self.img_proc.extract_video_frames(file_path, num_frames=3)
                            else:
                                p_img = self.face_proc.preprocess_image(file_path)
                            
                            # Ensure thumbnail exists during face analysis too
                            # Rule 2: Use low-res source for speed
                            self.img_proc.generate_thumbnail(file_path)
                            
                            return ("NEW", file_path, p_img, is_video)
                        except Exception as e:
                            logger.error(f"Face Producer Error: {e}")
                            return None

                    for i in range(0, total, 4):
                        if self.is_cancelled: break
                        try:
                            # 60s timeout for a chunk of 4 files
                            results = list(executor.map(process_single, files[i:i+4], timeout=60))
                            for res in results:
                                if res: prepped_queue.put(res)
                        except Exception as e:
                            logger.error(f"Face Producer Chunk Hang or Error at index {i}: {e}")
                            # No need to put dummy items here as they'll be skipped in the next phase
                prepped_queue.put("DONE")

            p_thread = Thread(target=producer, daemon=True)
            p_thread.start()

            processed = 0
            start_time = time.time()
            while not self.is_cancelled:
                batch = []
                while len(batch) < batch_size and not self.is_cancelled:
                    try:
                        item = prepped_queue.get(timeout=1.0)
                        if item == "DONE": break
                        batch.append(item)
                    except queue.Empty:
                        if not p_thread.is_alive(): break
                        continue
                if not batch: break

                new_items = [b for b in batch if b[0] == "NEW"]
                if new_items:
                    # Collect all images/frames for batch inference
                    all_imgs = []
                    mapping = []
                    for i in new_items:
                        _, f_path, p_img, is_v = i
                        if is_v and isinstance(p_img, list):
                            valid = [x for x in p_img if x[0] is not None]
                            all_imgs.extend([x[0] for x in valid])
                            mapping.append((f_path, [x[1] for x in valid]))
                        elif p_img is not None:
                            all_imgs.append(p_img)
                            mapping.append((f_path, [0]))
                    
                    if all_imgs:
                        raw_results = self.face_proc.detect_faces_batch(all_imgs)
                        curr = 0
                        face_cache_dir = get_face_cache_dir()
                        import cv2

                        with self.db.get_connection() as conn:
                            for f_path, indices in mapping:
                                if self.force_reanalyze:
                                    conn.execute("DELETE FROM faces WHERE file_path = ?", (f_path,))
                                
                                for idx in indices:
                                    img_cv = all_imgs[curr]
                                    frame_res = raw_results[curr]
                                    ih, iw = img_cv.shape[:2]

                                    for face in frame_res:
                                        # 1. Save record to DB
                                        cursor = conn.execute('''
                                            INSERT INTO faces (file_path, vector_blob, bbox_json, frame_index)
                                            VALUES (?, ?, ?, ?)
                                        ''', (f_path, face['embedding'].tobytes(), json.dumps(face['bbox']), idx))
                                        
                                        face_id = cursor.lastrowid
                                        
                                        # 2. Extract and Save Crop (Pre-emptive Caching)
                                        try:
                                            x1, y1, x2, y2 = face['bbox']
                                            w, h = x2 - x1, y2 - y1
                                            # Add padding (30%)
                                            px1, py1 = max(0, x1 - w * 0.3), max(0, y1 - h * 0.3)
                                            px2, py2 = min(iw, x2 + w * 0.3), min(ih, y2 + h * 0.3)
                                            
                                            crop = img_cv[int(py1):int(py2), int(px1):int(px2)]
                                            if crop.size > 0:
                                                crop = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
                                                cache_path = os.path.join(face_cache_dir, f"face_{face_id}.jpg")
                                                _, buf = cv2.imencode('.jpg', crop)
                                                buf.tofile(cache_path)
                                        except Exception as ce:
                                            logger.warning(f"Failed to pre-generate crop for face {face_id}: {ce}")
                                        
                                    curr += 1
                            conn.commit()

                # VRAM Management: Rule 7 - Clean up GPU memory every 500 items
                if processed > 0 and processed % 500 == 0:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                processed += len(batch)
                self.progress_val.emit(int(processed / total * 95))
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                eta = int((total - processed) / speed) if speed > 0 else 0
                self.phase_status.emit(f"AI Detecting faces {processed}/{total} - ETA: {time.strftime('%H:%M:%S', time.gmtime(eta))}")
            
            if not self.is_cancelled:
                # Reuse the dedicated clustering logic
                self.phase_status.emit("Clustering faces into groups...")
                clustering_logic(self.db, self.face_proc, self.folder_path, min_samples=self.min_samples, eps=self.eps)
                self.progress_val.emit(100)

            # Final GPU cleanup after face recognition and clustering
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            self.finished_all.emit(True, "Face recognition complete.")
        except Exception as e:
            logger.exception("FaceRecognition Error:")
            self.finished_all.emit(False, str(e))

class FaceClusteringWorker(WorkerBase):
    """
    Dedicated worker for re-clustering based on existing embeddings in the DB.
    Skips the expensive image analysis phase.
    """
    def __init__(self, folder_path, db, include_trash_folders=False, min_samples=2, eps=0.42, det_thresh=0.35):
        super().__init__(folder_path, db, include_trash_folders, face_det_thresh=det_thresh)
        self.min_samples = min_samples
        self.eps = eps

    def run(self):
        try:
            self.phase_status.emit("Starting standalone face clustering...")
            self.progress_val.emit(10)
            
            clustering_logic(self.db, self.face_proc, self.folder_path, min_samples=self.min_samples, eps=self.eps)
            
            self.progress_val.emit(100)
            self.finished_all.emit(True, "Standalone clustering complete.")
        except Exception as e:
            logger.exception("FaceClustering Error:")
            self.finished_all.emit(False, str(e))


def clustering_logic(db, face_proc, folder_path, min_samples=2, eps=0.42):
    """Shared clustering logic used by multiple workers."""
    # Use escaped pattern for folder path to avoid SQL injection/glob issues
    folder_pattern = folder_path.replace('_', '[_]').replace('%', '[%]') + "%"
    
    # 1. Fetch ignored vectors from DB
    ignored_vectors = db.get_ignored_vectors()
    
    with db.get_connection() as conn:
        # Get all faces belonging to files in this folder
        query = "SELECT face_id, vector_blob FROM faces WHERE file_path LIKE ?"
        all_faces = conn.execute(query, (folder_pattern,)).fetchall()
        
        if not all_faces:
            return

        face_ids = []
        embeddings = []
        ignored_face_ids = []

        # 2. Filter out ignored persons
        for fid, v_blob in all_faces:
            emb = np.frombuffer(v_blob, dtype=np.float32)
            is_ignored = False
            
            if ignored_vectors:
                # Cosine Similarity check
                # Normalize embedding for distance calculation
                norm_emb = emb / (np.linalg.norm(emb) + 1e-6)
                for i_vec in ignored_vectors:
                    norm_i_vec = i_vec / (np.linalg.norm(i_vec) + 1e-6)
                    dist = 1.0 - np.dot(norm_emb, norm_i_vec)
                    if dist < eps: # Same person as ignored
                        is_ignored = True
                        break
            
            if is_ignored:
                ignored_face_ids.append(fid)
            else:
                face_ids.append(fid)
                embeddings.append(emb)

        # 3. Purge ignored faces from DB
        if ignored_face_ids:
            logger.info(f"Auto-ignoring {len(ignored_face_ids)} face(s) matching ignored lists.")
            db.remove_face_batch(ignored_face_ids)

        if len(embeddings) >= min_samples:
            # DBSCAN Labels: -1 for noise, >=0 for clusters
            labels = face_proc.cluster_faces(embeddings, eps=eps, min_samples=min_samples)
            
            update_batch = []
            for face_id, label in zip(face_ids, labels):
                if label != -1:
                    update_batch.append((int(label), face_id))
            
            if update_batch:
                with db.get_connection() as conn:
                    conn.executemany("UPDATE faces SET cluster_id = ? WHERE face_id = ?", update_batch)
                    # Create placeholder cluster names if they don't exist
                    unique_labels = set([u[0] for u in update_batch])
                    for l in unique_labels:
                        conn.execute("INSERT OR IGNORE INTO clusters (cluster_id, custom_name) VALUES (?, ?)", 
                                    (l, f"Person {l+1}"))
                    conn.commit()

class FaceResetWorker(QThread):
    """
    Worker for non-blocking face data reset. 
    Stops ongoing analysis threads and clears face-related tables.
    """
    phase_status = Signal(str)
    progress_val = Signal(int)
    face_data_reset_finished = Signal(bool, str)

    def __init__(self, db, folder_path=None, face_worker=None, cluster_worker=None):
        super().__init__()
        self.db = db
        self.folder_path = folder_path
        self.face_worker = face_worker
        self.cluster_worker = cluster_worker

    def run(self):
        try:
            self.phase_status.emit("Stopping ongoing face analysis...")
            self.progress_val.emit(10)
            
            # Safely stop existing workers if they are still running
            if self.face_worker and self.face_worker.isRunning():
                self.face_worker.stop()
                self.face_worker.wait()
            
            if self.cluster_worker and self.cluster_worker.isRunning():
                self.cluster_worker.stop()
                self.cluster_worker.wait()
            
            # VRAM Cleanup (Pre-reset)
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.info("GPU cache cleared (Pre-face-reset).")
            except ImportError:
                pass

            self.phase_status.emit("Clearing face data from database...")
            self.progress_val.emit(40)
            
            # Perform reset (calls clear_face_data with WAL checkpoint)
            self.db.clear_face_data(self.folder_path)
            
            # VRAM Management: Standard GPU cleanup after reset
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            self.progress_val.emit(100)
            self.face_data_reset_finished.emit(True, "Face data reset complete.")
        except Exception as e:
            logger.exception("FaceReset Error:")
            self.face_data_reset_finished.emit(False, str(e))

class CleanupWorker(QThread):
    progress_val = Signal(int)
    finished = Signal(int)

    def __init__(self, groups, db, root_folder=None):
        super().__init__()
        self.groups = groups
        self.db = db
        self.root_folder = root_folder
        from processor.image_processor import ImageProcessor
        from processor.feature_extractor import FeatureExtractor
        from processor.duplicate_manager import DuplicateManager
        self.img_proc = ImageProcessor()
        self.feat_ext = FeatureExtractor()
        self.duplicate_mgr = DuplicateManager(self.db, self.img_proc, self.feat_ext)

    def run(self):
        count = 0
        for i, group in enumerate(self.groups):
            # Priority Logic: Keep files with EXIF/Location and larger size.
            # Sort order:
            # 1. has_exif_date or has_location (1 if yes, 0 if no) -> Descending (1 first)
            # 2. size -> Descending
            group.sort(key=lambda x: (
                1 if (x["metadata"].get("has_exif_date") or x["metadata"].get("has_location")) else 0,
                x["metadata"].get("size", 0)
            ), reverse=True)
            
            # Keep the first, ensure it's not marked as in trash in the database
            to_keep = group[0]
            if to_keep.get("is_in_trash"):
                try:
                    # Restore prioritized version physically and in database
                    new_path = self.duplicate_mgr.restore_file_from_trash(to_keep["file_path"])
                    # Update local path for consistency if it was moved
                    to_keep["file_path"] = new_path
                except Exception as e:
                    logger.error(f"Failed to restore duplicate match {to_keep['file_path']}: {e}")

            # Mark others for trash
            to_delete = group[1:]
            for item in to_delete:
                path = item["file_path"]
                norm_path = os.path.abspath(os.path.normpath(path))
                try:
                    # Move to local project trash folder
                    if os.path.exists(norm_path):
                        new_path = move_file_to_local_trash(norm_path, self.root_folder)
                    else:
                        new_path = path

                    self.duplicate_mgr.mark_file_as_trashed(path, new_path, item)
                    
                    count += 1
                except Exception as e:
                    # Log failure but continue with others
                    logger.error(f"Failed to cleanup/delete duplicate {norm_path}: {e}")

            
            self.progress_val.emit(i + 1)
        
        self.finished.emit(count)

class DataLoaderWorker(QThread):
    finished = Signal(list, bool) # media, has_more
    error = Signal(str)

    def __init__(self, db, filter_params, limit, offset, include_trash, root_folder, discovery_filter):
        super().__init__()
        self.db = db
        self.filter_params = filter_params
        self.limit = limit
        self.offset = offset
        self.include_trash = include_trash
        self.root_folder = root_folder
        self.discovery_filter = discovery_filter

    def run(self):
        try:
            f = self.filter_params
            media = self.db.get_media_paged(f["cluster_id"], f["year"], f["month"], f["location"],
                                          limit=self.limit, offset=self.offset,
                                          include_trash=self.include_trash,
                                          root_folder=self.root_folder,
                                          discovery_filter=self.discovery_filter)
            has_more = (len(media) >= self.limit)
            self.finished.emit(media, has_more)
        except Exception as e:
            logger.error(f"DataLoaderWorker Error: {e}")
            self.error.emit(str(e))

class SearchWorker(QThread):
    finished = Signal(list)
    progress_val = Signal(int)
    progress_range = Signal(int, int)
    phase_status = Signal(str)
    error = Signal(str)

    def __init__(self, db, include_trash=False, threshold=0.6):
        super().__init__()
        self.db = db
        self.include_trash = include_trash
        self.threshold = threshold
        from processor.image_processor import ImageProcessor
        from processor.feature_extractor import FeatureExtractor
        self.img_proc = ImageProcessor()
        self.feat_ext = FeatureExtractor()

    def run(self):
        try:
            from processor.duplicate_manager import DuplicateManager
            manager = DuplicateManager(self.db, self.img_proc, self.feat_ext)

            # First, check if we already have unified duplicates in the DB
            groups = manager.db.get_duplicate_groups()
            if groups:
                self.phase_status.emit(f"Found {len(groups)} groups from cache...")
                self.finished.emit(groups)
                return

            def p_callback(msg, val):
                self.phase_status.emit(msg)
                self.progress_val.emit(val)

            groups = manager.find_structural_duplicates(
                threshold=self.threshold,
                include_trash=self.include_trash,
                progress_callback=p_callback
            )

            self.phase_status.emit("Finalizing results...")
            self.finished.emit(groups)

        except Exception as e:
            import traceback
            logger.error(f"Search Error Traceback: {traceback.format_exc()}")
            self.error.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoArrange - Professional Photo Organizer")
        self.setMinimumSize(1200, 850)
        
        self.db = Database()
        self.current_folder = None
        
        # Load settings from DB with defaults
        self.face_det_thresh = int(self.db.get_setting("face_det_thresh", 35))
        self.face_min_samples = int(self.db.get_setting("face_min_samples", 2))
        self.face_cluster_eps = int(self.db.get_setting("face_cluster_eps", 42))
        self.face_merge_threshold = int(self.db.get_setting("face_merge_threshold", 55))
        
        self.threshold = int(self.db.get_setting("threshold", 5))
        self.dup_threshold = int(self.db.get_setting("dup_threshold", 6))
        self.dup_threshold_stage2 = int(self.db.get_setting("dup_threshold_stage2", 95))
        self.force_reanalyze = self.db.get_setting("force_reanalyze", "False") == "True"
        self.include_trash = self.db.get_setting("include_trash", "False") == "True"

        # Verification & Suggestion settings (Defaults for confirmation area)
        self.face_suggestion_thresh = self.face_merge_threshold / 100.0  # e.g., 0.55
        self.face_auto_merge_thresh = 0.85                               # Strict auto-merge
        self.pending_merges = []

        # Pagination & Rendering state
        self.current_filter = {"cluster_id": None, "year": None, "month": None, "location": None}
        self.page_size = 100
        self.current_offset = 0
        self.is_loading_more = False
        self.has_more = True
        self.all_people = [] # Cache of (cid, name)
        
        # Duplicate Grouping & Header State
        self.last_hash = None
        self.last_loc = None
        self.last_date = None
        self.hash_to_id = {}
        self.next_group_id = 1

        self.init_ui()

        self.apply_theme()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Main Header ---
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(40)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 2, 15, 2)
        header_layout.setSpacing(15)
        
        # Left Cluster: Navigation & Info
        title_icon = QLabel("📸")
        title_icon.setStyleSheet("font-size: 18px;")
        header_layout.addWidget(title_icon)
        
        title_label = QLabel("PhotoArrange")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #3D5AFE; margin-right: 15px;")
        header_layout.addWidget(title_label)
        self.btn_select = QPushButton("📂 Open Folder")
        self.btn_select.setFixedWidth(140)
        self.btn_select.setObjectName("flat")
        self.btn_select.setToolTip("スキャン対象のフォルダを選択します。")
        self.btn_select.clicked.connect(self.select_folder)
        header_layout.addWidget(self.btn_select)
        
        header_layout.addStretch()

        # AI Operations Menu Button
        self.btn_ai_ops = QPushButton("✨ AI機能")
        self.btn_ai_ops.setFixedWidth(110)
        self.btn_ai_ops.setObjectName("primary")
        self.btn_ai_ops.setToolTip("AIを用いた重複分析や顔認識を実行します。")
        
        ai_menu = QMenu(self)
        
        self.act_dup_analysis = QAction("➕ AI分析 & 重複発見", self)
        self.act_dup_analysis.setToolTip("ライブラリを再走査し、新しいファイルに対してAI特徴量抽出と重複分析を行います。")
        self.act_dup_analysis.triggered.connect(self.run_duplicate_analysis)
        ai_menu.addAction(self.act_dup_analysis)
        
        self.act_dup_regroup = QAction("🔄 AIグループ化のみ", self)
        self.act_dup_regroup.setToolTip("すでに取得済みのAI特徴量を使って、新しいしきい値でグループ分けだけをやり直します。")
        self.act_dup_regroup.triggered.connect(self.run_duplicate_regrouping)
        ai_menu.addAction(self.act_dup_regroup)
        
        ai_menu.addSeparator()
        
        self.act_face_analysis = QAction("👤 顔認識の実行", self)
        self.act_face_analysis.setToolTip("写真内の顔を検出し、特徴量を抽出します。")
        self.act_face_analysis.triggered.connect(self.run_face_analysis)
        self.act_face_analysis.setEnabled(False)
        ai_menu.addAction(self.act_face_analysis)
        
        self.act_face_clustering = QAction("👥 人物グループ化", self)
        self.act_face_clustering.setToolTip("すでに取得済みの顔情報を使って、グループ分けだけをやり直します。")
        self.act_face_clustering.triggered.connect(self.run_face_clustering)
        self.act_face_clustering.setEnabled(False)
        ai_menu.addAction(self.act_face_clustering)

        ai_menu.addSeparator()
        
        self.act_force_toggle = QAction("🎯 強制再解析を有効にする", self)
        self.act_force_toggle.setCheckable(True)
        self.act_force_toggle.setChecked(self.force_reanalyze)
        self.act_force_toggle.toggled.connect(self.update_force_reanalyze)
        ai_menu.addAction(self.act_force_toggle)

        self.btn_ai_ops.setMenu(ai_menu)
        header_layout.addWidget(self.btn_ai_ops)
        
        header_layout.addStretch()
        
        # Right Cluster: Management & Settings
        self.btn_faces = QPushButton("👤 顔・人物")
        self.btn_faces.setObjectName("flat")
        self.btn_faces.setFixedWidth(120)
        self.btn_faces.setCheckable(True)
        self.btn_faces.setToolTip("顔写真の管理と整理画面を切り替えます。")
        self.btn_faces.clicked.connect(self.toggle_face_manager)
        header_layout.addWidget(self.btn_faces)

        header_layout.addStretch()

        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setObjectName("flat")
        self.btn_settings.setFixedWidth(40)
        self.btn_settings.setToolTip("アプリケーションの設定（閾値など）を変更します。")
        self.btn_settings.clicked.connect(self.show_settings)
        header_layout.addWidget(self.btn_settings)

        main_layout.addWidget(header)

        # Body - Stacked Widget for View Switching
        from PySide6.QtWidgets import QStackedWidget
        self.central_stack = QStackedWidget()
        
        # View 0: Library View (Tree + Grid)
        self.library_view = QSplitter(Qt.Horizontal)
        
        # Left: Tree
        self.tree_view = MediaTreeView()
        self.tree_view.loadRequest.connect(self.on_tree_load_request)
        self.tree_view.clicked.connect(self.on_tree_selection)
        self.tree_view.renameRequested.connect(self.on_rename_person)
        self.library_view.addWidget(self.tree_view)
        # Right side: Grid Area with Sub-Header
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)
        
        # Sub-Header (Contextual actions for selection)
        self.sub_header = QFrame()
        self.sub_header.setObjectName("sub_header")
        self.sub_header.setFixedHeight(36)
        sub_layout = QHBoxLayout(self.sub_header)
        sub_layout.setContentsMargins(10, 2, 10, 2)
        sub_layout.setSpacing(10)
        
        self.btn_select_all = QPushButton("✅ Select All")
        self.btn_select_all.setObjectName("flat")
        self.btn_select_all.setFixedWidth(110)
        self.btn_select_all.clicked.connect(self.select_all_visible)
        sub_layout.addWidget(self.btn_select_all)
        
        self.btn_deselect_all = QPushButton("❌ Deselect All")
        self.btn_deselect_all.setObjectName("flat")
        self.btn_deselect_all.setFixedWidth(120)
        self.btn_deselect_all.clicked.connect(self.deselect_all_visible)
        sub_layout.addWidget(self.btn_deselect_all)
        
        self.btn_clear_tags = QPushButton("🏷️ Clear Tags")
        self.btn_clear_tags.setObjectName("flat")
        self.btn_clear_tags.setFixedWidth(110)
        self.btn_clear_tags.setEnabled(False)
        self.btn_clear_tags.clicked.connect(self.clear_selected_tags)
        sub_layout.addWidget(self.btn_clear_tags)
        
        # Duplicates Discovery Method Filter (v3.1.0 Simplified AI-Native)
        self.combo_dup_filter = QComboBox()
        self.combo_dup_filter.addItems([
            "すべて（統合表示）",
            "MD5（完全一致）", 
            "AI（視覚的類似性）"
        ])
        self.combo_dup_filter.setFixedWidth(200)
        self.combo_dup_filter.setVisible(False)
        self.combo_dup_filter.currentIndexChanged.connect(self.on_dup_filter_changed)
        sub_layout.addWidget(self.combo_dup_filter)
        
        sub_layout.addStretch()
        
        self.btn_cleanup = QPushButton("🧹 Cleanup Duplicates")
        self.btn_cleanup.setObjectName("danger")
        self.btn_cleanup.setFixedWidth(180)
        self.btn_cleanup.setVisible(False)
        self.btn_cleanup.clicked.connect(self.cleanup_duplicates)
        sub_layout.addWidget(self.btn_cleanup)

        self.btn_release_from_group = QPushButton("🔗 重複から除外")
        self.btn_release_from_group.setFixedWidth(140)
        self.btn_release_from_group.setEnabled(False)
        self.btn_release_from_group.setToolTip("選択された写真を重複グループから除外します。")
        self.btn_release_from_group.clicked.connect(self.release_selected_from_groups)
        sub_layout.addWidget(self.btn_release_from_group)
        
        self.btn_delete_selected = QPushButton("🗑️ Delete")
        self.btn_delete_selected.setObjectName("danger")
        self.btn_delete_selected.setFixedWidth(100)
        self.btn_delete_selected.setEnabled(False)
        self.btn_delete_selected.clicked.connect(self.delete_selected)
        sub_layout.addWidget(self.btn_delete_selected)
        
        grid_layout.addWidget(self.sub_header)
        
        self.grid_view = ThumbnailGrid()
        self.grid_view.item_double_clicked.connect(self.open_file)
        self.grid_view.tag_clicked.connect(self.manage_tag)
        self.grid_view.context_menu_requested.connect(self.show_thumbnail_context_menu)
        self.grid_view.request_more_data.connect(self.load_next_page)
        self.grid_view.selection_changed.connect(self.update_selection_ui)

        grid_layout.addWidget(self.grid_view)


        self.library_view.addWidget(grid_container)
        self.library_view.setStretchFactor(1, 4)
        
        self.central_stack.addWidget(self.library_view)
        
        # View 1: Face Manager View
        self.face_manager_view = FaceManagerView(self.db)
        self.face_manager_view.refresh_requested.connect(self.initialize_tree)
        self.central_stack.addWidget(self.face_manager_view)
        
        main_layout.addWidget(self.central_stack)

        # Footer - Status Bar Initialization
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("background-color: #1A1D2E; border-top: 1px solid #2D324A; color: #64748B;")
        
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)
        
        # Persistent info label for view context (like duplicate stats)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #3D5AFE; font-weight: bold; margin-left: 20px;")
        self.status_bar.addWidget(self.info_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    def apply_theme(self):
        self.setStyleSheet(get_style_sheet())

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.current_folder = folder
            self.status_label.setText(f"Synchronizing database with: {folder}...")
            
            # Start Automatic Sync
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.set_buttons_enabled(False)

            include_trash = self.include_trash
            self.sync_worker = FileSyncWorker(folder, self.db, include_trash_folders=include_trash)
            self.sync_worker.progress_val.connect(self.progress_bar.setValue)
            self.sync_worker.phase_status.connect(self.status_label.setText)
            self.sync_worker.finished_all.connect(self.on_sync_finished)
            self.sync_worker.start()

    def on_sync_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.set_buttons_enabled(True)
        if success:
            self.initialize_tree()
            self.show_images_paged()
            self.face_manager_view.refresh_sidebar()
            self.status_label.setText(f"Sync complete. Ready.")
        else:
            QMessageBox.critical(self, "Sync Error", f"Failed to sync folder: {message}")
            self.status_label.setText("Sync failed.")

    def set_buttons_enabled(self, enabled):
        # Header Controls
        self.btn_select.setEnabled(enabled)
        self.btn_ai_ops.setEnabled(enabled)
        
        # Actions inside AI Menu
        self.act_dup_analysis.setEnabled(enabled)
        self.act_dup_regroup.setEnabled(enabled)
        # Face analysis is always enabled once folder is selected
        self.act_face_analysis.setEnabled(enabled)
        self.act_face_clustering.setEnabled(enabled)

        # Management Controls
        self.btn_faces.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        
        # Sub-Header Contextual Controls
        self.btn_select_all.setEnabled(enabled)
        self.btn_deselect_all.setEnabled(enabled)
        self.btn_clear_tags.setEnabled(enabled)
        self.btn_cleanup.setEnabled(enabled)
        self.btn_delete_selected.setEnabled(enabled)

    def run_duplicate_analysis(self):
        if not self.current_folder: return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.set_buttons_enabled(False)

        force = self.force_reanalyze
        include_trash = self.include_trash

        # Map UI threshold (1-15) to L2 distance (0.1-1.5)
        # 6 on slider = 0.6 L2 (standard)
        l2_thresh = self.dup_threshold / 10.0
        # Stage 2: 95 on slider = 0.95 patch similarity
        stage2_thresh = self.dup_threshold_stage2 / 100.0

        self.analysis_worker = DuplicateAnalysisWorker(self.current_folder, self.db,
                                                       include_trash_folders=include_trash,
                                                       force_reanalyze=force,
                                                       threshold=l2_thresh,
                                                       stage2_threshold=stage2_thresh)
        self.analysis_worker.progress_val.connect(self.progress_bar.setValue)
        self.analysis_worker.phase_status.connect(self.status_label.setText)
        self.analysis_worker.finished_all.connect(self.on_analysis_finished)
        self.analysis_worker.start()

    def run_duplicate_regrouping(self):
        if not self.current_folder: return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.set_buttons_enabled(False)

        include_trash = self.include_trash

        # Map UI threshold (1-15) to L2 distance (0.1-1.5)
        l2_thresh = self.dup_threshold / 10.0
        # Stage 2: 95 on slider = 0.95 patch similarity
        stage2_thresh = self.dup_threshold_stage2 / 100.0

        self.regroup_worker = DuplicateRegroupingWorker(self.current_folder, self.db,
                                                       include_trash_folders=include_trash,
                                                       threshold=l2_thresh,
                                                       stage2_threshold=stage2_thresh)
        self.regroup_worker.progress_val.connect(self.progress_bar.setValue)
        self.regroup_worker.phase_status.connect(self.status_label.setText)
        self.regroup_worker.finished_all.connect(self.on_analysis_finished)
        self.regroup_worker.start()
    def run_face_analysis(self):
        if not self.current_folder: return

        logger.info(f"Face Recognition started. Force Re-analyze: {self.force_reanalyze}")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        QApplication.processEvents() # Force UI update before AI init
        self.set_buttons_enabled(False)

        # Stop existing worker safely if it's still running
        if hasattr(self, 'face_worker') and self.face_worker.isRunning():
            logger.info("Stopping existing face analysis worker before restart...")
            self.face_worker.stop()
            self.face_worker.wait()

        force = self.force_reanalyze
        include_trash = self.include_trash

        try:
            self.face_worker = FaceRecognitionWorker(
                self.current_folder, self.db,
                include_trash_folders=include_trash,
                force_reanalyze=force,
                min_samples=self.face_min_samples,
                eps=self.face_cluster_eps / 100.0,
                det_thresh=self.face_det_thresh / 100.0
            )
            self.face_worker.progress_val.connect(self.progress_bar.setValue)
            self.face_worker.phase_status.connect(self.status_label.setText)
            self.face_worker.finished_all.connect(self.on_analysis_finished)
            self.face_worker.start()
            logger.info("Face Recognition Worker thread started.")
        except Exception as e:
            logger.exception(f"Failed to start Face Recognition Worker: {e}")
            self.set_buttons_enabled(True)
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"Error starting face analysis: {e}")

    def run_face_clustering(self):
        if not self.current_folder: return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.set_buttons_enabled(False)
        
        self.cluster_worker = FaceClusteringWorker(
            self.current_folder, self.db, 
            include_trash_folders=self.include_trash,
            min_samples=self.face_min_samples,
            eps=self.face_cluster_eps / 100.0,
            det_thresh=self.face_det_thresh / 100.0
        )
        self.cluster_worker.progress_val.connect(self.progress_bar.setValue)
        self.cluster_worker.phase_status.connect(self.status_label.setText)
        self.cluster_worker.finished_all.connect(self.on_analysis_finished)
        self.cluster_worker.start()

    def initialize_tree(self):
        clusters = self.db.get_clusters()
        categories = []
        for cid, name in clusters:
            display_name = name if name else f"Person {cid}"
            categories.append((display_name, cid))
        self.tree_view.initialize_categories(categories)

    def on_tree_load_request(self, item, level, params):
        include_trash = self.include_trash
        if level == "years":
            cid = params.get("cluster_id")
            years = self.db.get_years(cid, include_trash=include_trash)
            self.tree_view.add_sub_items(item, years, "years")
        elif level == "months":
            cid = params.get("cluster_id")
            year = params.get("year")
            months = self.db.get_months(cid, year, include_trash=include_trash)
            self.tree_view.add_sub_items(item, months, "months")
        elif level == "locations":
            cid = params.get("cluster_id")
            year = params.get("year")
            month = params.get("month")
            locs = self.db.get_locations(cid, year, month, include_trash=include_trash)
            self.tree_view.add_sub_items(item, locs, "locations")

    def on_tree_selection(self, index):
        item = self.tree_view.model.itemFromIndex(index)
        if not item:
            return
        itype = item.data(Qt.UserRole + 2)
        
        cid = None
        year = None
        month = None
        location = None

        if itype == "category":
            cid = item.data(Qt.UserRole)
        elif itype == "years":
            p = item.parent()
            cid = p.data(Qt.UserRole) if p else None
            year = item.data(Qt.UserRole + 4)
        elif itype == "months":
            data = item.data(Qt.UserRole + 1)
            if data and len(data) >= 3:
                cid, year, month = data[:3]
        elif itype == "locations":
            data = item.data(Qt.UserRole + 1)
            if data and len(data) >= 4:
                cid, year, month, location = data[:4]
        
        self.current_filter = {"cluster_id": cid, "year": year, "month": month, "location": location}
        self.btn_cleanup.setVisible(cid == -2)
        self.combo_dup_filter.setVisible(cid == -2)
        
        status_text = ""
        info_text = ""
        
        if cid == -1: 
            status_text = "Showing Media with No Faces Detected"
        elif cid == -2: 
            d_filter = self.get_current_discovery_filter()
            stats = self.db.get_duplicate_stats(include_trash=self.include_trash,
                                              root_folder=self.current_folder,
                                              discovery_filter=d_filter)
            group_cnt, total_files, counts = stats
            status_text = "Showing Duplicate Groups"
            
            # Map internal method names to user-friendly labels
            labels = {
                'exact': 'MD5 (Exact)', 
                'ai_local': 'AI (Image)', 
                'ai_local_video': 'AI (Video)',
                'ai_video_global': 'AI (Global)'
            }
            
            # Create a summary of discovery methods found in these groups
            active_methods = []
            for k, v in counts.items():
                if v > 0:
                    display_name = labels.get(k, k)
                    active_methods.append(f"{display_name}: {v}")
            
            info_text = f"📊 Duplicate Clusters: {group_cnt} | 🎞️ Total Files: {total_files}"
            if active_methods:
                info_text += " | " + " | ".join(active_methods)
            
            if group_cnt == 0:
                info_text = "✨ All duplicates cleared! Your library is clean."
        elif cid == -3: 
            status_text = "Showing Corrupted/Broken Media"
        
        if status_text: self.status_label.setText(status_text)
        self.info_label.setText(info_text)

        
        self.show_images_paged()

    def get_current_discovery_filter(self):
        idx = self.combo_dup_filter.currentIndex()
        if idx == 0: return None # All
        mapping = {1: 'exact', 2: 'ai_local'}
        return mapping.get(idx)

    def on_dup_filter_changed(self, index):
        # Refresh current view
        self.on_tree_selection(self.tree_view.currentIndex())


    def show_images_paged(self):
        # Cancel any pending load and WAIT for it to stop to avoid "clogging"
        if hasattr(self, 'data_loader') and self.data_loader.isRunning():
            self.data_loader.terminate()
            self.data_loader.wait()

        self.current_offset = 0
        self.grid_view.clear()
        QApplication.processEvents()  # Force model reset and UI update
        self.has_more = True
        self.is_loading_more = False # Reset flag to allow first page load
        
        # Reset grouping state for new views
        self.last_hash = None
        self.last_loc = None
        self.last_date = None
        self.hash_to_id = {}
        self.next_group_id = 1
        
        self.load_next_page()


    def load_next_page(self):
        # Prevent parallel loading
        if self.is_loading_more or not self.has_more:
            return

        self.is_loading_more = True
        self.status_label.setText("Loading more media...")

        f = self.current_filter
        include_trash = self.include_trash
        d_filter = self.get_current_discovery_filter()
        
        self.data_loader = DataLoaderWorker(self.db, f, self.page_size, self.current_offset,
                                          include_trash, self.current_folder, d_filter)
        self.data_loader.finished.connect(self.on_data_loaded)
        self.data_loader.error.connect(self.on_data_error)
        self.data_loader.start()

    def on_data_error(self, err):
        self.is_loading_more = False
        self.status_label.setText(f"Error loading: {err}")

    def on_data_loaded(self, media, has_more):
        self.has_more = has_more
        self.status_label.setText("") # Clear loading status
        
        try:
            if not media:
                if self.current_offset == 0:
                    self.info_label.setText("No media found.")
                else:
                    self.status_label.setText("No more items to load.")
                return
            else:
                # Prepare data for model
                display_data = []
                f = self.current_filter
                is_dupe_view = (f.get("cluster_id") == -2)
                
                from processor.image_processor import ImageProcessor
                img_proc = ImageProcessor()
                
                for item in media:
                    # Use DB cached thumbnail path or calculate fallback
                    if not item.get("thumbnail_path"):
                        item["thumbnail_path"] = img_proc.get_thumbnail_path(item["file_path"])
                    
                    raw_h = item.get("group_id")
                    # Normalize hash for robust UI grouping (strip/lowercase)
                    current_h = raw_h.strip().lower() if raw_h else None
                    is_duplicate = item.get("is_duplicate", False)
                    meta = item.get("metadata", {})
                    
                    # Assign Group IDs to all duplicates (even in non-dupe view for badges)
                    if is_duplicate and current_h:
                        if current_h not in self.hash_to_id:
                            self.hash_to_id[current_h] = self.next_group_id
                            self.next_group_id += 1
                        item["ui_group_id"] = self.hash_to_id[current_h]
                    
                    # Identify Location Label (Prefer normalized fields from DB)
                    country = item.get("country") or meta.get("country", "")
                    pref = item.get("prefecture") or meta.get("prefecture", "")
                    city = item.get("city") or meta.get("city", "")
                    
                    # Format: "Prefecture, City" or "Country, City"
                    if country in ["Japan", "日本", "JP"]:
                        loc_label = f"{pref}, {city}" if pref and city else (pref or city)
                    else:
                        loc_label = f"{country}, {city}" if country and city else (country or city)
                    
                    if not loc_label or loc_label.strip() == ",": loc_label = "Unknown Location"
                    
                    # Extract YYYY-MM-DD from capture_date for day-based grouping
                    cap_date = item.get("capture_date") or ""
                    current_date_str = cap_date.split(' ')[0] if cap_date else "Unknown Date"

                    # Inject Headers based on view type
                    if is_dupe_view:
                        # Robust check for hashes (ensuring no NULL grouping spills)
                        if current_h and current_h != "none" and current_h != self.last_hash:
                            display_data.append({
                                "is_header": True,
                                "ui_group_id": item.get("ui_group_id"),
                                "group_id": current_h
                            })
                            self.last_hash = current_h
                    else:
                        # Non-duplicate view: show location + date headers
                        # Trigger a new header if either location OR date changes
                        if not f.get("location") and (loc_label != self.last_loc or current_date_str != self.last_date):
                            display_data.append({
                                "is_header": True,
                                "location_header": loc_label,
                                "date_header": current_date_str
                            })
                            self.last_loc = loc_label
                            self.last_date = current_date_str
                    
                    display_data.append(item)

                self.grid_view.append_data(display_data)
                self.current_offset += len(media)
                self.status_label.setText(f"Loaded {self.current_offset} items")

        finally:
            self.is_loading_more = False

        # [Aggressive Prefetching] Moved out of try-finally to avoid being blocked by is_loading_more
        if self.current_offset == self.page_size and self.has_more:
            self.load_next_page()

    
    def cleanup_duplicates(self):
        # UI Feedback for search phase
        self.btn_cleanup.setEnabled(False)
        self.status_label.setText("Searching for duplicates...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # Indeterminate phase
        
        include_trash = self.include_trash
        # Use scaled threshold (L2 0.1 - 1.5)
        l2_thresh = self.dup_threshold / 10.0
        self.search_worker = SearchWorker(self.db, include_trash=include_trash, threshold=l2_thresh)
        self.search_worker.progress_val.connect(self.progress_bar.setValue)
        self.search_worker.progress_range.connect(self.progress_bar.setRange)
        self.search_worker.phase_status.connect(self.status_label.setText)
        self.search_worker.finished.connect(self.on_search_finished)
        self.search_worker.error.connect(self.on_search_error)
        self.search_worker.start()

    def on_search_error(self, err_msg):
        self.btn_cleanup.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Search failed.")
        QMessageBox.critical(self, "Error", f"Search failed: {err_msg}")

    def on_search_finished(self, groups):
        self.btn_cleanup.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("")

        if not groups:
            QMessageBox.information(self, "Cleanup", "No duplicate groups found.")
            return
            
        confirm = QMessageBox.question(self, "Confirm Cleanup", 
                                     f"Found {len(groups)} groups. Delete smaller versions of all duplicates?\n"
                                     "(Files will be moved to Recycle Bin)",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes: return

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(groups))
        self.progress_bar.setValue(0)
        
        self.cleanup_worker = CleanupWorker(groups, self.db, root_folder=self.current_folder)
        self.cleanup_worker.progress_val.connect(self.progress_bar.setValue)
        self.cleanup_worker.finished.connect(self.on_cleanup_finished)
        self.cleanup_worker.start()

    def on_cleanup_finished(self, count):
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Cleaned up {count} duplicates.")
        QMessageBox.information(self, "Cleanup Done", f"Successfully processed {count} files.\nMoved to trash and removed from library.")
        
        # Refresh everything to reflect deletions
        self.initialize_tree() # Refresh categories (sidebar counts)
        self.on_tree_selection(self.tree_view.currentIndex()) # Force full reload of current view
        
        self.is_loading_more = False
        # self.btn_load_more.setEnabled(True) # This button doesn't exist anymore

    def release_selected_from_groups(self):
        """Removes selected files from their respective duplicate groups."""
        selected = self.grid_view.get_selected_files()
        if not selected:
            return

        reply = QMessageBox.question(
            self, "重複から除外",
            f"選択された{len(selected)}枚の写真を重複グループから除外しますか？\n（ファイル自体は削除されません）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                # 1. Store current selection context to restore after tree refresh
                current_cid = self.current_filter.get("cluster_id")
                
                # 2. Database update
                self.db.release_files_from_groups(selected)
                
                # 3. Clear visual selection
                self.grid_view.select_all(False)
                
                # 4. Refresh sidebar tree (This wipes current index in the view)
                self.initialize_tree()
                
                # 5. Restore tree selection and refresh grid
                if current_cid is not None:
                    target_item = self.tree_view.find_category_item(current_cid)
                    if target_item:
                        self.tree_view.setCurrentIndex(target_item.index())
                        self.on_tree_selection(target_item.index())
                    else:
                        self.show_images_paged()
                else:
                    self.show_images_paged()
                    
            finally:
                QApplication.restoreOverrideCursor()
        # self.btn_load_more.setText("Load More...") # This button doesn't exist anymore

    def on_analysis_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.set_buttons_enabled(True)
        
        # Capture current selection cluster_id to restore view context
        selected_cid = None
        selected_indexes = self.tree_view.selectedIndexes()
        if selected_indexes:
            item = self.tree_view.model.itemFromIndex(selected_indexes[0])
            if item:
                # Resolve to top-level category if sub-item is selected
                curr = item
                while curr.parent():
                    curr = curr.parent()
                selected_cid = curr.data(Qt.UserRole)
        
        # Refresh tree categories from DB
        self.initialize_tree()
        
        # Restore selection and refresh info labels/grid
        if selected_cid is not None:
            target_item = self.tree_view.find_category_item(selected_cid)
            if target_item:
                self.tree_view.setCurrentIndex(target_item.index())
                self.on_tree_selection(target_item.index())
            else:
                self.show_images_paged()
        else:
            self.show_images_paged()

        if success:
            QMessageBox.information(self, "Complete", message)
        else:
            QMessageBox.critical(self, "Error", message)

    def on_rename_person(self, old_name, new_name):
        # Find cluster_id
        clusters = self.db.get_clusters()
        for cid, name in clusters:
            if (name if name else f"Person {cid}") == old_name:
                self.db.upsert_cluster(cid, new_name)
                self.initialize_tree()
                break

    def open_file(self, file_path):
        import subprocess
        if os.name == 'nt':
            os.startfile(file_path)
        else:
            subprocess.call(['open', file_path])

    def show_settings(self):
        dialog = SettingsDialog(
            current_threshold=self.threshold,
            face_det_thresh=self.face_det_thresh,
            face_min_samples=self.face_min_samples,
            face_cluster_eps=self.face_cluster_eps,
            face_merge_threshold=self.face_merge_threshold,
            current_dup_threshold=self.dup_threshold,
            current_dup_threshold_stage2=self.dup_threshold_stage2,
            force_reanalyze=self.force_reanalyze,
            include_trash=self.include_trash,
            parent=self
        )
        dialog.face_det_thresh_changed.connect(self.update_face_det_thresh)
        dialog.face_min_samples_changed.connect(self.update_face_min_samples)
        dialog.face_cluster_eps_changed.connect(self.update_face_cluster_eps)
        dialog.face_merge_threshold_changed.connect(self.update_face_merge_threshold)
        
        dialog.settings_changed.connect(self.update_threshold)
        dialog.dup_threshold_changed.connect(self.update_dup_threshold)
        dialog.dup_threshold_stage2_changed.connect(self.update_dup_threshold_stage2)
        dialog.force_reanalyze_changed.connect(self.update_force_reanalyze)
        dialog.include_trash_changed.connect(self.update_include_trash)
        dialog.data_reset.connect(self.reset_all)
        dialog.face_data_reset_requested.connect(self.run_face_data_reset)
        dialog.exec()

    def toggle_face_manager(self, checked):
        """Switches between Library View and Face Manager View."""
        if checked:
            self.central_stack.setCurrentIndex(1)
            self.btn_faces.setText("🖼️ ライブラリ")
            self.btn_faces.setStyleSheet("background-color: #3D5AFE; color: white;")
            self.face_manager_view.refresh_sidebar()
        else:
            self.central_stack.setCurrentIndex(0)
            self.btn_faces.setText("👤 顔・人物")
            self.btn_faces.setStyleSheet("")
            self.show_images_paged()
            self.initialize_tree()



    def update_face_det_thresh(self, val):
        self.face_det_thresh = val
        self.db.save_setting("face_det_thresh", val)

    def update_face_min_samples(self, val):
        self.face_min_samples = val
        self.db.save_setting("face_min_samples", val)

    def update_face_cluster_eps(self, val):
        self.face_cluster_eps = val
        self.db.save_setting("face_cluster_eps", val)

    def update_face_merge_threshold(self, val):
        self.face_merge_threshold = val
        self.db.save_setting("face_merge_threshold", val)

    def update_threshold(self, val):
        self.threshold = val
        self.db.save_setting("threshold", val)

    def update_dup_threshold(self, val):
        self.dup_threshold = val
        self.db.save_setting("dup_threshold", val)

    def update_dup_threshold_stage2(self, val):
        self.dup_threshold_stage2 = val
        self.db.save_setting("dup_threshold_stage2", val)

    def update_force_reanalyze(self, val):
        self.force_reanalyze = val
        self.db.save_setting("force_reanalyze", str(val))
        if hasattr(self, 'act_force_toggle'):
            self.act_force_toggle.blockSignals(True)
            self.act_force_toggle.setChecked(val)
            self.act_force_toggle.blockSignals(False)

    def update_include_trash(self, val):
        self.include_trash = val
        self.db.save_setting("include_trash", str(val))
        # If folder already selected, refresh tree to show/hide trashed items
        if self.current_folder:
            self.initialize_tree()
            self.show_images_paged()

    def run_face_data_reset(self):
        """Initiates the asynchronous face data reset process."""
        if not self.current_folder: 
            # Even without a folder, we might want to clear global data
            pass

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.set_buttons_enabled(False)
        
        # Instantiate the more robust FaceResetWorker
        self.reset_worker = FaceResetWorker(
            self.db, 
            folder_path=None, # Global reset as requested by Settings
            face_worker=getattr(self, 'face_worker', None),
            cluster_worker=getattr(self, 'cluster_worker', None)
        )
        self.reset_worker.progress_val.connect(self.progress_bar.setValue)
        self.reset_worker.phase_status.connect(self.status_label.setText)
        self.reset_worker.face_data_reset_finished.connect(self.on_face_data_reset_finished)
        self.reset_worker.start()

    def on_face_data_reset_finished(self, success, message):
        """Handles completion of the face data reset and refreshes the UI."""
        self.progress_bar.setVisible(False)
        self.set_buttons_enabled(True)
        
        if success:
            # 1. Clear internal caches for faces/clusters
            self.pending_merges = []
            
            # 2. Refresh the person/category tree (Sidebar)
            self.initialize_tree()
            
            # 3. Refresh the current view (Grid & Badges)
            self.show_images_paged()
            
            # 4. Refresh the face manager view if active
            if self.central_stack.currentIndex() == 1:
                self.face_manager_view.refresh_sidebar()
            
            QMessageBox.information(self, "Reset Complete", message)
        else:
            self.status_label.setText("Reset failed.")
            QMessageBox.critical(self, "Reset Error", message)

    def reset_all(self):
        # 1. Stop all active workers to prevent DB locks and resource leakage
        workers = [
            'sync_worker', 'analysis_worker', 'regroup_worker', 
            'face_worker', 'cluster_worker', 'search_worker', 'data_loader'
        ]
        for w_name in workers:
            if hasattr(self, w_name):
                worker = getattr(self, w_name)
                if worker and worker.isRunning():
                    logger.info(f"Stopping {w_name} for reset...")
                    worker.terminate()
                    worker.wait()

        # 2. VRAM Cleanup (Pre-reset)
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("GPU cache cleared (Pre-reset).")
        except ImportError:
            pass

        # 3. Database & UI State Reset
        self.db.clear_all_data()
        
        # Reset internal UI caches
        self.all_people = []
        self.pending_merges = []
        self.hash_to_id = {}
        self.next_group_id = 1
        self.last_hash = None
        self.last_loc = None
        self.last_date = None
        
        # Clear Views
        self.grid_view.clear()
        self.initialize_tree()
        
        # 4. VRAM Cleanup (Post-reset)
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("GPU cache cleared (Post-reset).")
        except ImportError:
            pass

        self.status_label.setText("All cache cleared and resources released.")

    def delete_selected(self):
        selected = self.grid_view.get_selected_files()
        if not selected:
            QMessageBox.information(self, "No Selection", "Please check at least one photo first.")
            return
        
        confirm = QMessageBox.question(self, "Delete Files", 
                                     f"Move {len(selected)} selected files to Recycle Bin?",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            count = 0
            for path in selected:
                try:
                    norm = os.path.abspath(os.path.normpath(path))
                    if os.path.exists(norm):
                        # Move to local trash
                        new_path = move_file_to_local_trash(norm, self.current_folder)
                        
                        # Update DB immediately with new path and trash status
                        # Find the metadata to re-sync
                        media_info = self.db.get_media(path)
                        if media_info:
                            # V3.2 row schema (file_path, last_modified, metadata_json, group_id, ...)
                            self.db.delete_media(path)
                            self.db.add_media_batch([(
                                new_path, media_info[1], media_info[2], media_info[3],
                                media_info[4], media_info[5], media_info[6], media_info[7],
                                media_info[8], media_info[9], media_info[10], media_info[11],
                                media_info[12], media_info[13], 1, media_info[15],
                                media_info[16], media_info[17]
                            )])
                        
                    count += 1
                except Exception as e:
                    logger.error(f"Batch Delete Error: {e}")
            
            QApplication.restoreOverrideCursor()
            QMessageBox.information(self, "Deletion Complete", f"{count} files moved to .trash folder.")
            self.show_images_paged()
            self.initialize_tree()

    def select_all_visible(self):
        self.grid_view.select_all(True)

    def deselect_all_visible(self):
        self.grid_view.select_all(False)

    def update_selection_ui(self, count):
        """Update buttons based on the number of selected items."""
        has_selection = count > 0
        self.btn_delete_selected.setEnabled(has_selection)
        self.btn_release_from_group.setEnabled(has_selection)
        self.btn_clear_tags.setEnabled(has_selection)
        
        if has_selection:
            self.btn_delete_selected.setText(f"🗑️ Delete ({count})")
        else:
            self.btn_delete_selected.setText("🗑️ Delete")

    def manage_tag(self, file_path, cluster_id, name):
        # Fetch actual face_id for this tag (it might be one of several)
        faces = self.db.get_faces_for_file(file_path)
        # Find the face_id matching the clicked cluster_id
        target_face_id = None
        for f in faces:
            if str(f[1]) == str(cluster_id):
                target_face_id = f[0]
                break
        
        if target_face_id is None: return

        menu = QMenu(self)
        
        # Rename Person
        rename_action = QAction(f"✏️ Rename '{name}'", self)
        rename_action.triggered.connect(lambda: self.rename_specific_person(cluster_id, name))
        menu.addAction(rename_action)
        
        # Change to another person
        change_menu = menu.addMenu("🔄 Move to Another Person")
        all_clusters = self.db.get_all_clusters()
        for cid, cname in all_clusters:
            if str(cid) == str(cluster_id): continue
            act = QAction(cname if cname else f"Person {cid}", self)
            act.triggered.connect(lambda checked=False, f=target_face_id, c=cid: self.change_face_cluster(f, c))
            change_menu.addAction(act)
        
        # Remove Tag
        remove_action = QAction("❌ Remove Person Tag from This File", self)
        remove_action.triggered.connect(lambda: self.remove_specific_tag(target_face_id))
        menu.addAction(remove_action)
        
        menu.exec(self.cursor().pos())

    def rename_specific_person(self, cluster_id, old_name):
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "Rename Person", f"New name for {old_name}:", text=old_name)
        if ok and new_name and new_name != old_name:
            self.db.upsert_cluster(cluster_id, new_name)
            self.initialize_tree()
            self.show_images_paged()

    def change_face_cluster(self, face_id, new_cluster_id):
        self.db.update_face_cluster(face_id, new_cluster_id)
        self.show_images_paged()

    def remove_specific_tag(self, face_id):
        self.db.remove_face(face_id)
        self.show_images_paged()

    def on_release_duplicate_group(self, group_id):
        confirm = QMessageBox.question(
            self,
            "重複グループの解除",
            "このグループの重複関係を解除しますか？\n解除されたファイルは個別のファイルとして扱われ、自動クリーンアップの対象から外れます。",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            self.db.release_duplicate_group(group_id)
            
            # Clear selection
            self.grid_view.select_all(False)
            
            # Re-fetch sidebar stats if needed
            self.initialize_tree()
            
            # Force grid refresh directly instead of relying on tree index (which might have changed)
            self.show_images_paged()

    def show_thumbnail_context_menu(self, file_path, global_pos):
        menu = QMenu(self)
        
        # Add Person Tag
        add_tag_menu = menu.addMenu("➕ Add Person Tag")
        
        new_person_action = QAction("✨ New Person...", self)
        new_person_action.triggered.connect(lambda: self.add_person_to_file(file_path, None))
        add_tag_menu.addAction(new_person_action)
        add_tag_menu.addSeparator()
        
        all_clusters = self.db.get_clusters() # Only non-ignored
        for cid, name in all_clusters:
            display_name = name if name else f"Person {cid}"
            act = QAction(display_name, self)
            act.triggered.connect(lambda checked=False, f=file_path, c=cid: self.add_person_to_file(f, c))
            add_tag_menu.addAction(act)
        
        menu.addSeparator()
        
        # Open File
        open_action = QAction("📂 Open File", self)
        open_action.triggered.connect(lambda: self.open_file(file_path))
        menu.addAction(open_action)
        
        # Delete File
        delete_action = QAction("🗑️ Delete File", self)
        delete_action.triggered.connect(lambda: self.delete_single_file(file_path))
        menu.addAction(delete_action)
        
        menu.addSeparator()
        
        # Clear Tags from this file
        clear_tags_action = QAction("🏷️ Clear ALL Tags from Photo", self)
        clear_tags_action.triggered.connect(lambda: self.clear_file_tags(file_path))
        menu.addAction(clear_tags_action)
        
        menu.exec(global_pos)


    def add_person_to_file(self, file_path, cluster_id):
        """Triggers the asynchronous person association/registration process."""
        action_type = None
        params = {"file_path": file_path}

        if cluster_id is None:
            # New Person registration
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "New Person", "Enter Name for the Person found in this photo:")
            if ok and name.strip():
                action_type = PersonAction.REGISTER_NEW
                params["name"] = name.strip()
            else:
                return
        else:
            # Association with existing person
            action_type = PersonAction.ASSOCIATE_EXISTING
            params["cluster_id"] = cluster_id

        # In a real app, we need the specific face_id. 
        # Since this UI (context menu on file) doesn't know which face the user implies,
        # we pick the first one or prompt. For Milestone 3 completeness, 
        # we'll associate THE FIRST face found in that file if not specified.
        faces = self.db.get_faces_for_file(file_path)
        if not faces:
            QMessageBox.warning(self, "No Faces", "No faces were detected in this photo yet. Please run Face Analysis first.")
            return
        
        params["face_id"] = faces[0][0] # face_id of the first face

        # Start background worker
        self.person_worker = PersonManagementWorker(self.db, action_type, params)
        self.person_worker.task_finished.connect(self.on_person_action_finished)
        self.person_worker.refresh_requested.connect(self.on_person_refresh_requested)
        self.person_worker.start()
        
        self.status_label.setText("Updating person association...")

    @Slot(bool, str)
    def on_person_action_finished(self, success, message):
        if not success:
            QMessageBox.critical(self, "Person Management Error", f"Operation failed: {message}")
        self.status_label.setText(message)

    @Slot()
    def on_person_refresh_requested(self):
        """Refreshes sidebar counts and current grid view after data modification."""
        self.initialize_tree()
        # Full refresh of current grid
        self.show_images_paged()
        # Also notify Face Manager if it exists
        if hasattr(self, 'face_manager_view'):
            self.face_manager_view.refresh_sidebar()

    def delete_single_file(self, file_path):
        confirm = QMessageBox.question(self, "Delete File", "Move this file to .trash folder?",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            try:
                norm = os.path.abspath(os.path.normpath(file_path))
                if os.path.exists(norm):
                    new_path = move_file_to_local_trash(norm, self.current_folder)
                    
                    # Row: (file_path, mtime, meta, hash, ...)
                    media_info = self.db.get_media(file_path)
                    if media_info:
                        self.db.delete_media(file_path)
                        self.db.add_media_batch([(
                            new_path, media_info[1], media_info[2], media_info[3],
                            media_info[4], media_info[5], media_info[6], media_info[7],
                            media_info[8], media_info[9], media_info[10], media_info[11],
                            media_info[12], media_info[13], 1, media_info[15],
                            media_info[16], media_info[17]
                        )])
                
                self.show_images_paged()
                self.initialize_tree() # Refresh sidebar immediately
            except Exception as e:
                logger.error(f"Delete Error: {e}")

    def clear_file_tags(self, file_path):
        confirm = QMessageBox.warning(self, "Clear Tags", "Remove ALL person tags from this photo?",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.db.clear_faces_for_file(file_path)
            self.show_images_paged()

    def clear_selected_tags(self):
        selected = [m["file_path"] for m in self.grid_view.media_model.get_selected_media()]
        if not selected:
            QMessageBox.information(self, "No Selection", "Please select photos to clear tags from.")
            return
            
        confirm = QMessageBox.warning(self, "Clear Selected Tags", 
                                     f"Remove ALL person tags from {len(selected)} selected photos?",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            for f in selected:
                self.db.clear_faces_for_file(f)
            self.show_images_paged()
            self.initialize_tree() # Refresh sidebar in case "No Faces Detected" changes
            # Clear checkboxes
            self.grid_view.media_model.clear_selection()




if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("PhotoArrange")
        
        # Set dark theme
        app.setStyle("Fusion")
        
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"FATAL APP CRASH:\n{error_msg}")
        
        # Try to show a crash dialog if PySide is still alive
        try:
            from PySide6.QtWidgets import QMessageBox
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Fatal Error")
            msg.setText("The application encountered a fatal error and must close.")
            msg.setInformativeText(str(e))
            msg.setDetailedText(error_msg)
            msg.exec()
        except:
            pass
        sys.exit(1)





