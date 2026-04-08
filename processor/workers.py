import json
import logging
import os
import queue
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Thread
from typing import Any, Optional

import numpy as np
import torch
from PySide6.QtCore import Signal
from PySide6.QtGui import QImage
from sklearn.cluster import DBSCAN

from core.base_worker import BaseWorker
from core.database import Database
from core.models import ClusterInfo, FaceCountsResult, FaceInfo, MediaRecord
from core.utils import (
    Profiler,
    get_face_cache_dir,
    get_short_path_name,
    move_file_to_local_trash,
    normalize_path,
)

from .duplicate_manager import DuplicateManager
from .face_processor import FaceProcessor
from .feature_extractor import FeatureExtractor
from .geo_processor import GeoProcessor
from .image_processor import ImageProcessor
from .model_manager import ModelManager

logger = logging.getLogger("PhotoArrange")


@dataclass(frozen=True)
class MediaLoadResult:
    media: list[MediaRecord]
    has_more: bool
    last_capture_date: Optional[str] = None
    last_file_path: Optional[str] = None


@dataclass(frozen=True)
class LibrarySidebarResult:
    root_counts: dict[str, int]
    persons: list[ClusterInfo]


@dataclass(frozen=True)
class FaceLoadResult:
    category_id: int
    faces: list[FaceInfo]
    has_more: bool
    last_capture_date: Optional[str] = None
    last_face_id: Optional[int] = None


@dataclass(frozen=True)
class FaceCropResult:
    face_id: int
    image: QImage


@dataclass(frozen=True)
class SidebarLoadResult:
    counts: FaceCountsResult
    persons: list[ClusterInfo]


@dataclass(frozen=True)
class TreeDataLoadResult:
    item: Any  # The tree item to update
    data: list
    level: str
    success: bool = True
    message: str = ""


TRASH_NAMES = {"$RECYCLE.BIN", ".TRASH", "TRASH", ".TRASH-1000", "TRASH-1000"}


class FileSyncWorker(BaseWorker):
    """Fast synchronization between disk and DB."""

    def __init__(self, folder_path: str, db: Database, include_trash_folders: bool = False) -> None:
        super().__init__()
        self.folder_path = normalize_path(folder_path)
        self.db = db
        self.include_trash_folders = include_trash_folders
        self.img_proc = ImageProcessor()

    def run(self) -> None:
        with Profiler(f"FileSyncWorker.run ({os.path.basename(self.folder_path)})"):
            try:
                self._cleanup_orphans()
                if self.is_cancelled:
                    return
                self._add_new_files()
                self.finished_task.emit(True, "Sync complete.")
            except Exception as e:
                logger.exception("FileSync Error:")
                self.finished_task.emit(False, str(e))

    def _cleanup_orphans(self) -> None:
        """Global Case-Normalization & Orphan Cleanup."""
        self.phase_status.emit("Cleaning up library...")
        all_db_paths = self.db.get_all_media_paths()
        if not all_db_paths:
            return

        self.phase_status.emit(f"Verifying {len(all_db_paths)} library items...")
        self.progress_val.emit(0)

        norm_map = self._get_normalized_path_map(all_db_paths)
        if self.is_cancelled:
            return

        real_orphans, merges_to_perform = self._identify_orphans_and_merges(norm_map)
        if self.is_cancelled:
            return

        self._execute_cleanup_ops(real_orphans, merges_to_perform)
        self.progress_val.emit(100)

    def _get_normalized_path_map(self, all_db_paths: Iterable[str]) -> dict[str, list[str]]:
        norm_map: dict[str, list[str]] = {}
        for p in all_db_paths:
            n = normalize_path(p)
            if n not in norm_map:
                norm_map[n] = []
            norm_map[n].append(p)
        return norm_map

    def _identify_orphans_and_merges(
        self, norm_map: dict[str, list[str]]
    ) -> tuple[list[str], list[tuple[str, list[str]]]]:
        unique_norm_paths = list(norm_map.keys())
        real_orphans: list[str] = []
        merges: list[tuple[str, list[str]]] = []

        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [
                executor.submit(lambda p: (p, os.path.exists(p)), np) for np in unique_norm_paths
            ]
            for i, future in enumerate(futures):
                if self.is_cancelled:
                    return [], []
                norm_p, exists = future.result()
                db_paths = norm_map[norm_p]
                if not exists:
                    real_orphans.extend(db_paths)
                elif len(db_paths) > 1:
                    db_paths.sort(reverse=True)
                    merges.append((db_paths[0], db_paths[1:]))

                if i % 100 == 0:
                    self.progress_val.emit(int((i / len(unique_norm_paths)) * 100))
        return real_orphans, merges

    def _execute_cleanup_ops(
        self, real_orphans: list[str], merges: list[tuple[str, list[str]]]
    ) -> None:
        if merges:
            self.phase_status.emit(f"Merging {len(merges)} path variants...")
            self.db.merge_duplicate_paths_batch(merges)
        if real_orphans:
            self.phase_status.emit(f"Pruning {len(real_orphans)} ghost records...")
            self.db.delete_media_batch(real_orphans)

    def _add_new_files(self) -> None:
        self.phase_status.emit("Scanning current folder...")
        disk_files = self._scan_disk_files()
        if self.is_cancelled:
            return

        db_paths = set(self.db.get_media_paths_in_folder(self.folder_path))
        disk_paths = set(disk_files)

        orphans = list(db_paths - disk_paths)
        if orphans and not self.is_cancelled:
            self.phase_status.emit(f"Removing {len(orphans)} missing items...")
            self.db.delete_media_batch(orphans)

        new_paths = list(disk_paths - db_paths)
        if not new_paths or self.is_cancelled:
            return

        self.phase_status.emit(f"Adding {len(new_paths)} items & generating thumbnails...")
        self._generate_thumbnails_and_add_records(new_paths)

    def _generate_thumbnails_and_add_records(self, new_paths: list[str]) -> None:
        def thumb_task(p: str) -> None:
            if self.is_cancelled:
                return
            try:
                self.img_proc.generate_thumbnail(p)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=8) as executor:
            for i in range(0, len(new_paths), 500):
                if self.is_cancelled:
                    break
                chunk = new_paths[i : i + 500]
                executor.map(thumb_task, chunk)
                db_batch = []
                for p in chunk:
                    try:
                        mtime = os.path.getmtime(p)
                        is_in_trash = any(tn in p.upper() for tn in TRASH_NAMES)
                        db_batch.append(
                            MediaRecord(
                                file_path=p,
                                last_modified=mtime,
                                metadata={},
                                thumbnail_path=self.img_proc.get_thumbnail_path(p),
                                is_in_trash=is_in_trash,
                                file_hash=self.img_proc.get_file_hash(p),
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error prepping DB record for {p}: {e}")
                self.db.add_media_batch(db_batch)
                self.progress_val.emit(int((i + len(chunk)) / len(new_paths) * 100))

    def _scan_disk_files(self) -> list[str]:
        files: list[str] = []
        for root, dirs, filenames in os.walk(self.folder_path):
            if self.is_cancelled:
                return []
            if not self.include_trash_folders:
                dirs[:] = [
                    d
                    for d in dirs
                    if d.upper() not in TRASH_NAMES
                    and not d.startswith(".")
                    and d not in (".git", "__pycache__")
                ]
            else:
                dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in filenames:
                if f.startswith(".") and not self.include_trash_folders:
                    continue
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".mp4", ".avi", ".mov")):
                    files.append(normalize_path(os.path.join(root, f)))
        return files


@dataclass(frozen=True)
class AnalysisResult:
    status: str  # "CACHED", "NEW", or "ERROR"
    file_path: str
    record: Optional[MediaRecord] = None
    ai_tensor: Optional[torch.Tensor] = None
    video_vec: Optional[np.ndarray] = None


class DuplicateAnalysisWorker(BaseWorker):
    """Specialized in finding duplicates. Extracts global embeddings."""

    def __init__(
        self,
        folder_path: str,
        db: Database,
        include_trash_folders: bool = False,
        force_reanalyze: bool = False,
        threshold: float = 0.6,
        stage2_threshold: float = 0.95,
    ) -> None:
        super().__init__()
        self.folder_path = normalize_path(folder_path)
        self.db, self.include_trash_folders = db, include_trash_folders
        self.force_reanalyze, self.threshold, self.stage2_threshold = (
            force_reanalyze,
            threshold,
            stage2_threshold,
        )
        self.manager, self.img_proc, self.geo_proc = (
            ModelManager(),
            ImageProcessor(),
            GeoProcessor(),
        )
        self.feat_ext = FeatureExtractor()
        self.duplicate_mgr = DuplicateManager(self.db, self.img_proc, self.feat_ext)

    def stop(self) -> None:
        super().stop()
        torch.cuda.empty_cache()

    def run(self) -> None:
        with Profiler(f"DuplicateAnalysisWorker.run ({os.path.basename(self.folder_path)})"):
            try:
                self.phase_status.emit("Starting duplicate analysis...")
                self.db.clear_ai_duplicate_groups(self.folder_path)
                files = self._scan_files()
                if not files:
                    self.finished_task.emit(True, "No files to analyze.")
                    return
                self._run_analysis_pipeline(files)
                self.manager.empty_cache()
                torch.cuda.empty_cache()
                self.finished_task.emit(True, "Duplicate analysis complete.")
            except Exception as e:
                logger.exception("DuplicateAnalysis Error:")
                self.finished_task.emit(False, str(e))

    def _run_analysis_pipeline(self, files: list[str]) -> None:
        total = len(files)
        prepped_queue: queue.Queue = queue.Queue(maxsize=1024)
        processed, start_time = 0, time.time()

        def producer():
            with ThreadPoolExecutor(max_workers=8) as executor:
                for res in executor.map(self._process_single, files):
                    if self.is_cancelled:
                        break
                    if res:
                        prepped_queue.put(res)
            prepped_queue.put("DONE")

        Thread(target=producer, daemon=True).start()
        seen_done = False
        while not self.is_cancelled:
            batch, seen_done = self._extract_analysis_batch(prepped_queue, seen_done)
            if not batch and seen_done:
                break
            self._handle_analysis_batch(batch, processed, total)
            processed += len(batch)
            if processed % 500 == 0:
                torch.cuda.empty_cache()
            self._report_progress(processed, total, start_time)

        if not self.is_cancelled:
            self._run_structural_analysis()

    def _extract_analysis_batch(
        self, prepped_queue: queue.Queue, seen_done: bool
    ) -> tuple[list[AnalysisResult], bool]:
        batch: list[AnalysisResult] = []
        while len(batch) < 256 and not self.is_cancelled:
            try:
                item = prepped_queue.get(timeout=1.0)
                if item == "DONE":
                    return batch, True
                batch.append(item)
            except queue.Empty:
                if seen_done:
                    break
        return batch, seen_done

    def _handle_analysis_batch(
        self, batch: list[AnalysisResult], processed: int, total: int
    ) -> None:
        new_items = [res for res in batch if res.status == "NEW"]
        if not new_items:
            return

        # GPU Batch Inference
        tensors = [res.ai_tensor for res in new_items if res.ai_tensor is not None]
        valid_indices = [idx for idx, res in enumerate(new_items) if res.ai_tensor is not None]

        vectors: list[Optional[np.ndarray]] = [res.video_vec for res in new_items]

        if tensors:
            with Profiler("DuplicateAnalysis: GPU Batch Inference"):
                try:
                    self.phase_status.emit(f"AI Hash/Embedding {processed}/{total} [GPU ACTIVE]")
                    batch_vectors = self.feat_ext.extract_features_from_tensors(tensors)
                    for v_idx, vec in enumerate(batch_vectors):
                        vectors[valid_indices[v_idx]] = vec
                except Exception as e:
                    logger.error(f"GPU Batch Inference Error: {e}")

        # DB Persistence
        db_batch = []
        for idx, res in enumerate(new_items):
            v_blob = vectors[idx].tobytes() if vectors[idx] is not None else None
            db_batch.append(replace(res.record, vector_blob=v_blob))

        self.db.add_media_batch(db_batch)

    def _run_structural_analysis(self) -> None:
        self.phase_status.emit("Global Structural AI Analysis...")
        self.db.clear_ai_duplicate_groups()
        groups = self.duplicate_mgr.find_structural_duplicates(
            threshold=self.threshold,
            stage2_threshold=self.stage2_threshold,
            include_trash=self.include_trash_folders,
            progress_callback=lambda m, v: (
                self.phase_status.emit(m),
                self.progress_val.emit(60 + int(v * 0.4)),
            ),
        )
        if groups:
            self.duplicate_mgr.unify_duplicate_hashes(groups)

    def _process_single(self, file_path: str) -> Optional[AnalysisResult]:
        if self.is_cancelled:
            return None
        try:
            mtime = os.path.getmtime(file_path)
            cached = self.db.get_media(file_path)
            if self._is_cache_valid(cached, mtime):
                return AnalysisResult(status="CACHED", file_path=file_path)

            # Metadata & Thumbnail
            thumb_path = self.img_proc.get_thumbnail_path(file_path)
            if not os.path.exists(thumb_path):
                self.img_proc.generate_thumbnail(file_path)

            is_v = file_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
            meta = (
                self.img_proc.get_video_metadata(file_path)
                if is_v
                else self.img_proc.get_metadata(file_path)
            )

            # Prepare AI Inputs
            ai_tensor, video_vec = self._prepare_ai_inputs(file_path, thumb_path, is_v)

            # Geo Location
            country, pref, city = self._resolve_location(meta, cached)

            record = MediaRecord(
                file_path=file_path,
                last_modified=mtime,
                metadata=meta,
                country=country,
                prefecture=pref,
                city=city,
                year=meta.get("year", 0),
                month=meta.get("month", 0),
                thumbnail_path=thumb_path,
                is_corrupted=bool(meta.get("corrupted", False)),
                is_in_trash=any(tn in file_path.upper() for tn in TRASH_NAMES),
                capture_date=meta.get("date_taken", ""),
                file_hash=self.img_proc.get_file_hash(file_path),
            )
            return AnalysisResult(
                status="NEW",
                file_path=file_path,
                record=record,
                ai_tensor=ai_tensor,
                video_vec=video_vec,
            )

        except Exception as e:
            logger.error(f"Analysis Error for {file_path}: {e}")
            error_record = MediaRecord(file_path=file_path, is_corrupted=True)
            return AnalysisResult(status="NEW", file_path=file_path, record=error_record)

    def _is_cache_valid(self, cached: Optional[MediaRecord], mtime: float) -> bool:
        return (
            cached is not None
            and cached.last_modified == mtime
            and not self.force_reanalyze
            and os.path.exists(cached.thumbnail_path or "")
            and bool(cached.file_hash)
            and bool(cached.vector_blob)
        )

    def _prepare_ai_inputs(
        self, file_path: str, thumb_path: str, is_video: bool
    ) -> tuple[Optional[torch.Tensor], Optional[np.ndarray]]:
        ai_tensor, video_vec = None, None
        if is_video:
            frames = self.img_proc.extract_video_frames(file_path, num_frames=5)
            if frames:
                video_vec = self.feat_ext.extract_features_from_video([f[0] for f in frames])
        else:
            ai_tensor = self.feat_ext.prepare_tensor(thumb_path)
            if ai_tensor is not None:
                ai_tensor = ai_tensor.pin_memory()
        return ai_tensor, video_vec

    def _resolve_location(
        self, meta: dict, cached: Optional[MediaRecord]
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        lat, lon = meta.get("lat", 0), meta.get("lon", 0)
        if (lat != 0 or lon != 0) and (not cached or cached.metadata.get("lat") != lat):
            loc = self.geo_proc.get_location(lat, lon)
            if loc:
                return loc.get("country"), loc.get("prefecture"), loc.get("city")
        return None, None, None

    def _report_progress(self, processed: int, total: int, start_time: float) -> None:
        elapsed = time.time() - start_time
        fps = processed / elapsed if elapsed > 0 else 0
        self.phase_status.emit(f"Analyzing {processed}/{total} ({fps:.1f} fps)")
        self.progress_val.emit(int(processed / total * 60))

    def _scan_files(self) -> list[str]:
        files: list[str] = []
        for root, dirs, filenames in os.walk(self.folder_path):
            if self.is_cancelled:
                return []
            dirs[:] = [
                d
                for d in dirs
                if (
                    self.include_trash_folders
                    or (d.upper() not in TRASH_NAMES and not d.startswith("."))
                )
                and d not in (".git", "__pycache__")
            ]
            for f in filenames:
                if f.startswith(".") and not self.include_trash_folders:
                    continue
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".mp4", ".avi", ".mov")):
                    files.append(normalize_path(os.path.join(root, f)))
        # Deduplicate to prevent race conditions during parallel processing
        return sorted(list(set(files)))


class DuplicateRegroupingWorker(BaseWorker):
    """Re-clusters existing AI vectors."""

    def __init__(
        self,
        db: Database,
        include_trash: bool = False,
        threshold: float = 0.6,
        stage2_threshold: float = 0.95,
    ) -> None:
        super().__init__()
        self.db, self.include_trash, self.threshold, self.stage2_threshold = (
            db,
            include_trash,
            threshold,
            stage2_threshold,
        )
        self.duplicate_mgr = DuplicateManager(self.db, ImageProcessor(), FeatureExtractor())

    def run(self) -> None:
        try:
            self.phase_status.emit("Starting AI re-grouping...")
            self.db.clear_ai_duplicate_groups()
            groups = self.duplicate_mgr.find_structural_duplicates(
                threshold=self.threshold,
                stage2_threshold=self.stage2_threshold,
                include_trash=self.include_trash,
                progress_callback=lambda m, v: (
                    self.phase_status.emit(m),
                    self.progress_val.emit(int(v)),
                ),
            )
            if groups:
                self.duplicate_mgr.unify_duplicate_hashes(groups)
            ModelManager().empty_cache()
            torch.cuda.empty_cache()
            self.finished_task.emit(True, "AI re-grouping complete.")
        except Exception as e:
            logger.exception("Regrouping Error:")
            self.finished_task.emit(False, str(e))


@dataclass(frozen=True)
class FacePrepResult:
    status: str  # "CACHED", "NEW", or "ERROR"
    file_path: str
    preprocessed_img: Optional[Any] = None
    is_video: bool = False


class FaceRecognitionWorker(BaseWorker):
    """AI inference for faces."""

    def __init__(
        self,
        folder_path: str,
        db: Database,
        include_trash_folders: bool = False,
        force_reanalyze: bool = False,
        min_samples: int = 2,
        eps: float = 0.42,
        det_thresh: float = 0.35,
    ) -> None:
        super().__init__()
        self.folder_path = normalize_path(folder_path)
        self.db, self.include_trash_folders, self.force_reanalyze = (
            db,
            include_trash_folders,
            force_reanalyze,
        )
        self.min_samples, self.eps = min_samples, eps
        self.manager, self.img_proc = ModelManager(), ImageProcessor()
        self.face_proc = FaceProcessor(det_thresh=det_thresh)

    def stop(self) -> None:
        super().stop()
        torch.cuda.empty_cache()

    def run(self) -> None:
        with Profiler(f"FaceRecognitionWorker.run ({os.path.basename(self.folder_path)})"):
            try:
                self.phase_status.emit("Starting AI Face Recognition...")
                files = self._scan_files()
                if not files:
                    self.finished_task.emit(True, "No files.")
                    return
                self._run_recognition_pipeline(files)

                # TASK FIX: Run deduplication cleanup pass automatically after recognition
                self.phase_status.emit("Cleaning up duplicates...")
                deduper = FaceDeduplicationWorker(self.db, self.folder_path)
                deduper.run()

                self.manager.empty_cache()
                torch.cuda.empty_cache()
                self.finished_task.emit(True, "Face recognition complete.")
            except Exception as e:
                logger.exception("FaceRecognition Error:")
                self.finished_task.emit(False, str(e))

    def _run_recognition_pipeline(self, files: list[str]) -> None:
        total = len(files)
        q: queue.Queue = queue.Queue(maxsize=32)
        self._start_recognition_producer(files, q)
        processed, start_time = 0, time.time()
        while not self.is_cancelled:
            batch = self._get_recognition_batch(q)
            if not batch:
                break
            new_items = [b for b in batch if b.status == "NEW"]
            if new_items:
                self._process_batch(new_items)
            processed += len(batch)
            if processed % 500 == 0:
                torch.cuda.empty_cache()
            self._report_progress(processed, total, start_time)

        if not self.is_cancelled:
            self.phase_status.emit("Clustering faces...")
            clustering_logic(self.db, self.face_proc, self.folder_path, self.min_samples, self.eps)

    def _start_recognition_producer(self, files: list[str], q: queue.Queue) -> None:
        def producer():
            with ThreadPoolExecutor(max_workers=8) as ex:
                for i in range(0, len(files), 4):
                    if self.is_cancelled:
                        break
                    try:
                        for res in ex.map(self._process_single, files[i : i + 4]):
                            if res:
                                q.put(res)
                    except Exception as e:
                        logger.error(f"Producer Error: {e}")
            q.put("DONE")

        Thread(target=producer, daemon=True).start()

    def _get_recognition_batch(self, q: queue.Queue) -> list[FacePrepResult]:
        batch: list[FacePrepResult] = []
        while len(batch) < 16 and not self.is_cancelled:
            try:
                item = q.get(timeout=1.0)
                if item == "DONE":
                    break
                batch.append(item)
            except queue.Empty:
                continue
        return batch

    def _process_single(self, file_path: str) -> Optional[FacePrepResult]:
        if self.is_cancelled:
            return None
        try:
            mtime = os.path.getmtime(file_path)
            cached = self.db.get_media(file_path)

            if self._is_cache_valid(cached, mtime, file_path):
                return FacePrepResult(status="CACHED", file_path=file_path)

            is_v = file_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
            p_img = (
                self.img_proc.extract_video_frames(file_path, 3)
                if is_v
                else self.face_proc.preprocess_image(file_path)
            )
            self.img_proc.generate_thumbnail(file_path)
            return FacePrepResult(
                status="NEW", file_path=file_path, preprocessed_img=p_img, is_video=is_v
            )
        except Exception as e:
            logger.error(f"Face Prep Error {file_path}: {e}")
            return None

    def _is_cache_valid(self, cached: Optional[MediaRecord], mtime: float, file_path: str) -> bool:
        if not cached or cached.last_modified != mtime or self.force_reanalyze:
            return False

        with self.db.get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM faces WHERE file_path=? LIMIT 1", (file_path,))
            has_faces = cur.fetchone()

        thumb_path = self.img_proc.get_thumbnail_path(file_path)
        return bool(has_faces) and os.path.exists(thumb_path)

    def _process_batch(self, new_items: list[FacePrepResult]) -> None:
        all_imgs, mapping = [], []
        for item in new_items:
            p_img = item.preprocessed_img
            if item.is_video and isinstance(p_img, list):
                valid = [x for x in p_img if x[0] is not None]
                all_imgs.extend([x[0] for x in valid])
                mapping.append((item.file_path, [x[1] for x in valid]))
            elif p_img is not None:
                all_imgs.append(p_img)
                mapping.append((item.file_path, [0]))

        if not all_imgs:
            return

        raw_results = self.face_proc.detect_faces_batch(all_imgs)
        cache_dir = get_face_cache_dir()

        with self.db.get_connection() as conn:
            curr = 0
            for f_path, indices in mapping:
                if self.force_reanalyze:
                    conn.execute("DELETE FROM faces WHERE file_path=?", (f_path,))

                # Collect all detections for THIS file to perform temporal deduplication
                file_faces = []
                for idx in indices:
                    img_cv = all_imgs[curr]
                    for face in raw_results[curr]:
                        face["img_cv"] = img_cv
                        face["frame_index"] = idx
                        file_faces.append(face)
                    curr += 1

                # Perform Inter-frame Deduplication (Similarity > 0.88)
                deduplicated = self._deduplicate_temporal(file_faces)

                for face in deduplicated:
                    cur = conn.execute(
                        "INSERT INTO faces (file_path, vector_blob, bbox_json, frame_index) VALUES (?,?,?,?)",
                        (
                            f_path,
                            face["embedding"].tobytes(),
                            json.dumps(face["bbox"]),
                            face["frame_index"],
                        ),
                    )
                    self._save_face_crop(cur.lastrowid, face["img_cv"], face["bbox"], cache_dir)

            conn.commit()

    def _deduplicate_temporal(
        self, faces: list[dict[str, Any]], sim_threshold: float = 0.88
    ) -> list[dict[str, Any]]:
        """
        Merges faces of the same person across multiple frames of the same file.
        Uses embedding cosine similarity.
        """
        if not faces:
            return []

        # Sort by confidence score
        sorted_faces = sorted(faces, key=lambda x: x["det_score"], reverse=True)
        keep = []

        for i in range(len(sorted_faces)):
            is_dup = False
            emb1 = sorted_faces[i]["embedding"]
            norm1 = np.linalg.norm(emb1)
            if norm1 == 0:
                continue
            v1 = emb1 / norm1

            for j in range(len(keep)):
                emb2 = keep[j]["embedding"]
                norm2 = np.linalg.norm(emb2)
                if norm2 == 0:
                    continue
                v2 = emb2 / norm2

                similarity = np.dot(v1, v2)
                if similarity > sim_threshold:
                    logger.info(
                        f"Temporal Deduplication: Dropping face in frame {sorted_faces[i]['frame_index']} (Sim={similarity:.4f} with {k['frame_index']})"
                    )
                    is_dup = True
                    break

            if not is_dup:
                keep.append(sorted_faces[i])

        return keep

    def _save_face_crop(
        self, face_id: int, img: np.ndarray, bbox: list[float], cache_dir: str
    ) -> None:
        try:
            ih, iw = img.shape[:2]
            x1, y1, x2, y2 = bbox
            w, h = x2 - x1, y2 - y1
            px1, py1 = max(0, x1 - w * 0.3), max(0, y1 - h * 0.3)
            px2, py2 = min(iw, x2 + w * 0.3), min(ih, y2 + h * 0.3)
            crop = img[int(py1) : int(py2), int(px1) : int(px2)]
            if crop.size > 0:
                import cv2

                crop = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
                cv2.imencode(".jpg", crop)[1].tofile(os.path.join(cache_dir, f"face_{face_id}.jpg"))
        except Exception as e:
            logger.warning(f"Crop Error {face_id}: {e}")

    def _report_progress(self, processed: int, total: int, start_time: float) -> None:
        self.progress_val.emit(int(processed / total * 95))
        elapsed = time.time() - start_time
        speed = processed / elapsed if elapsed > 0 else 0
        self.phase_status.emit(f"AI Detecting faces {processed}/{total} ({speed:.1f} fps)")

    def _scan_files(self) -> list[str]:
        files: list[str] = []
        for root, dirs, filenames in os.walk(self.folder_path):
            if self.is_cancelled:
                return []
            dirs[:] = [
                d
                for d in dirs
                if (
                    self.include_trash_folders
                    or (d.upper() not in TRASH_NAMES and not d.startswith("."))
                )
                and d not in (".git", "__pycache__")
            ]
            for f in filenames:
                if f.startswith(".") and not self.include_trash_folders:
                    continue
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".mp4", ".avi", ".mov")):
                    files.append(normalize_path(os.path.join(root, f)))
        # Deduplicate to prevent race conditions during parallel processing
        return sorted(list(set(files)))


class FaceClusteringWorker(BaseWorker):
    def __init__(
        self,
        folder_path: str,
        db: Database,
        include_trash_folders: bool = False,
        min_samples: int = 2,
        eps: float = 0.42,
        det_thresh: float = 0.35,
    ) -> None:
        super().__init__()
        self.folder_path = normalize_path(folder_path)
        self.db, self.min_samples, self.eps = db, min_samples, eps
        self.face_proc = FaceProcessor(det_thresh=det_thresh)

    def run(self) -> None:
        try:
            self.phase_status.emit("Starting standalone clustering...")
            clustering_logic(self.db, self.face_proc, self.folder_path, self.min_samples, self.eps)
            self.progress_val.emit(100)
            torch.cuda.empty_cache()
            self.finished_task.emit(True, "Complete.")
        except Exception as e:
            logger.exception("Clustering Error:")
            self.finished_task.emit(False, str(e))


class FaceResetWorker(BaseWorker):
    def __init__(
        self,
        db: Database,
        folder_path: Optional[str] = None,
        active_workers: Optional[list[BaseWorker]] = None,
    ) -> None:
        super().__init__()
        self.db, self.folder_path, self.active_workers = db, folder_path, active_workers or []

    def run(self) -> None:
        try:
            for w in self.active_workers:
                if isinstance(w, (FaceRecognitionWorker, FaceClusteringWorker)) and w.isRunning():
                    w.stop()
                    w.wait()
            ModelManager().empty_cache()
            torch.cuda.empty_cache()
            self.db.clear_face_data(self.folder_path)
            self.progress_val.emit(100)
            self.finished_task.emit(True, "Reset Complete.")
        except Exception as e:
            logger.exception("Reset Error:")
            self.finished_task.emit(False, str(e))


class DataLoaderWorker(BaseWorker):
    finished = Signal(object)
    chunk_ready = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        db: Database,
        filter_params: dict[str, Any],
        limit: int,
        offset: int,
        include_trash: bool,
        root_folder: str,
        discovery_filter: Optional[str],
        last_capture_date: Optional[str] = None,
        last_file_path: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.db, self.filter_params, self.limit, self.offset = db, filter_params, limit, offset
        self.include_trash, self.root_folder, self.discovery_filter = (
            include_trash,
            root_folder,
            discovery_filter,
        )
        self.last_capture_date, self.last_file_path = last_capture_date, last_file_path

    def run(self) -> None:
        with Profiler(f"DataLoaderWorker.run (limit={self.limit})"):
            try:
                f = self.filter_params
                media = self.db.get_media_paged(
                    f.get("cluster_id"),
                    f.get("year"),
                    f.get("month"),
                    f.get("location"),
                    limit=self.limit,
                    offset=self.offset,
                    include_trash=self.include_trash,
                    root_folder=self.root_folder,
                    discovery_filter=self.discovery_filter,
                    last_capture_date=self.last_capture_date,
                    last_file_path=self.last_file_path,
                )
                total = len(media)
                if total > 0:
                    c1 = min(10, total)
                    self.chunk_ready.emit(media[0:c1])
                    time.sleep(0.01)
                    if total > c1:
                        c2 = min(30, total - c1)
                        self.chunk_ready.emit(media[c1 : c1 + c2])
                        time.sleep(0.01)
                        for i in range(c1 + c2, total, 50):
                            if self.is_cancelled:
                                return
                            self.chunk_ready.emit(media[i : i + 50])
                            time.sleep(0.005)
                self.finished.emit(
                    MediaLoadResult(
                        media=media,
                        has_more=(total >= self.limit),
                        last_capture_date=media[-1].capture_date if media else None,
                        last_file_path=media[-1].file_path if media else None,
                    )
                )
            except Exception as e:
                logger.error(f"DataLoader Error: {e}")
                self.error.emit(str(e))


class LibrarySidebarWorker(BaseWorker):
    result_ready = Signal(object)
    finished = Signal()

    def __init__(self, db: Database, current_folder: Optional[str] = None) -> None:
        super().__init__()
        self.db, self.current_folder = db, current_folder

    def run(self) -> None:
        try:
            # 1. Fetch fast counts first to update high-level categories (All, Duplicates, etc.)
            counts = self.db.media_repo.get_root_category_counts(self.current_folder)

            # 2. Fetch fast person list (names only, no counts) for initial display
            p_fast = self.db.face_repo.get_person_list_fast()

            # 3. Emit initial result with real root counts but fast person list
            self.result_ready.emit(LibrarySidebarResult(root_counts=counts, persons=p_fast))

            # 4. Fetch full person list with counts (optimized query)
            p_full = self.db.face_repo.get_person_list_with_counts()

            # 5. Emit final result to update person counts in the UI
            self.result_ready.emit(LibrarySidebarResult(root_counts=counts, persons=p_full))
        except Exception as e:
            logger.error(f"Sidebar Error: {e}")
        finally:
            self.finished.emit()


class SidebarLoadWorker(BaseWorker):
    result_ready = Signal(object)

    def __init__(self, repo: Any) -> None:
        super().__init__()
        self.repo = repo

    def run(self) -> None:
        with Profiler("SidebarLoadWorker.run"):
            try:
                p_fast = self.repo.get_person_list_fast()
                if p_fast:
                    self.result_ready.emit(
                        SidebarLoadResult(counts=FaceCountsResult(0, 0, {}), persons=p_fast)
                    )
                res = SidebarLoadResult(
                    counts=self.repo.get_face_counts(), persons=self.repo.get_clusters()
                )
                self.result_ready.emit(res)
                self.finished_task.emit(True, "Loaded.")
            except Exception as e:
                logger.exception("SidebarLoad Error:")
                self.finished_task.emit(False, str(e))


class FaceLoadWorker(BaseWorker):
    result_ready = Signal(object)
    chunk_ready = Signal(object)

    def __init__(
        self,
        repo: Any,
        category_id: int,
        limit: int = 200,
        last_capture_date: Optional[str] = None,
        last_face_id: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.repo, self.category_id, self.limit = repo, category_id, limit
        self.last_capture_date, self.last_face_id = last_capture_date, last_face_id

    def run(self) -> None:
        with Profiler(f"FaceLoad (cat={self.category_id})"):
            try:
                cat_str = "person"
                p_id = self.category_id
                if p_id == -1:
                    cat_str = "unknown"
                elif p_id == -2:
                    cat_str = "ignored"
                faces = self.repo.get_faces_by_category(
                    cat_str,
                    person_id=p_id,
                    limit=self.limit,
                    last_capture_date=self.last_capture_date,
                    last_face_id=self.last_face_id,
                )
                if self.is_cancelled:
                    return
                for i in range(0, len(faces), 20):
                    if self.is_cancelled:
                        return
                    self.chunk_ready.emit(faces[i : i + 20])
                    time.sleep(0.01)
                last_face = faces[-1] if faces else None
                self.result_ready.emit(
                    FaceLoadResult(
                        category_id=self.category_id,
                        faces=faces,
                        has_more=(len(faces) >= self.limit),
                        last_capture_date=last_face.capture_date if last_face else None,
                        last_face_id=last_face.face_id if last_face else None,
                    )
                )
                self.finished_task.emit(True, f"Loaded {len(faces)} faces.")
            except Exception as e:
                logger.exception("FaceLoad Error:")
                self.finished_task.emit(False, str(e))


class FaceCropWorker(BaseWorker):
    batch_finished = Signal(object)

    def __init__(self, face_list: list[FaceInfo]) -> None:
        super().__init__()
        self.face_list = face_list
        self.cache_dir = get_face_cache_dir()

    def run(self) -> None:
        results: list[FaceCropResult] = []
        with Profiler(f"FaceCrop (items={len(self.face_list)})"):
            for face in self.face_list:
                if self.is_cancelled:
                    break
                path = os.path.join(self.cache_dir, f"face_{face.face_id}.jpg")
                # EXPLOSIVE SPEED: Load existing images in background thread. QImage load is thread-safe.
                qimg = QImage(path) if os.path.exists(path) else self.get_or_generate_crop(face)

                if qimg and not qimg.isNull():
                    results.append(FaceCropResult(face_id=face.face_id, image=qimg))
                if len(results) >= 20:
                    self.batch_finished.emit(results)
                    results = []
                    self.msleep(10)
            if results:
                self.batch_finished.emit(results)
            self.finished_task.emit(True, "Done.")

    def get_or_generate_crop(self, face: FaceInfo) -> Optional[QImage]:
        import cv2
        from PIL import Image, ImageOps

        f_path = normalize_path(face.file_path)
        if not face.bbox or not os.path.exists(f_path):
            return None
        try:
            if f_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                short_path = get_short_path_name(f_path)
                cap = cv2.VideoCapture(short_path)
                if not cap.isOpened():
                    logger.warning(f"FaceCropWorker: Could not open video {f_path}")
                    return None

                target_frame = int(face.frame_index or 0)
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                ok, frame = cap.read()

                # Robust fallback for seeking issues on some Windows codecs
                if not ok:
                    # Try seeking again or reading from start if it's frame 0
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    ok, frame = cap.read()

                cap.release()
                if not ok:
                    logger.warning(
                        f"FaceCropWorker: Failed to read frame {target_frame} from {f_path}"
                    )
                    return None
                img = frame
            else:
                with Image.open(f_path) as p:
                    img = cv2.cvtColor(
                        np.array(ImageOps.exif_transpose(p).convert("RGB")), cv2.COLOR_RGB2BGR
                    )
            ih, iw = img.shape[:2]
            x1, y1, x2, y2 = face.bbox
            w, h = x2 - x1, y2 - y1
            nx1, ny1 = max(0, x1 - w * 0.3), max(0, y1 - h * 0.3)
            nx2, ny2 = min(iw, x2 + w * 0.3), min(ih, y2 + h * 0.3)
            crop = img[int(ny1) : int(ny2), int(nx1) : int(nx2)]
            if crop.size == 0:
                return None
            crop = cv2.resize(crop, (150, 150), interpolation=cv2.INTER_AREA)
            cache_path = os.path.join(self.cache_dir, f"face_{face.face_id}.jpg")

            # Unicode-safe imwrite for Windows
            _, buffer = cv2.imencode(".jpg", crop)
            buffer.tofile(cache_path)

            # Return newly created image
            return QImage(cache_path)
        except Exception as e:
            logger.error(f"Crop Error for {face.face_id} from {face.file_path}: {e}")
        return None


class CleanupWorker(BaseWorker):
    finished = Signal(int)

    def __init__(
        self, groups: list[list[MediaRecord]], db: Database, root_folder: Optional[str] = None
    ) -> None:
        super().__init__()
        self.groups, self.db, self.root_folder = groups, db, root_folder
        self.duplicate_mgr = DuplicateManager(self.db, ImageProcessor(), FeatureExtractor())

    def run(self) -> None:
        with Profiler(f"Cleanup (groups={len(self.groups)})"):
            count = 0
            for i, group in enumerate(self.groups):
                if self.is_cancelled:
                    break
                group.sort(
                    key=lambda x: (
                        1
                        if (x.metadata.get("has_exif_date") or x.metadata.get("has_location"))
                        else 0,
                        x.metadata.get("size", 0),
                    ),
                    reverse=True,
                )
                if group[0].is_in_trash:
                    self.duplicate_mgr.restore_file_from_trash(group[0].file_path)
                for item in group[1:]:
                    try:
                        norm = normalize_path(item.file_path)
                        new_path = (
                            move_file_to_local_trash(norm, self.root_folder)
                            if os.path.exists(norm)
                            else item.file_path
                        )
                        self.duplicate_mgr.mark_file_as_trashed(item.file_path, new_path, item)
                        count += 1
                    except Exception as e:
                        logger.error(f"Cleanup Error {item.file_path}: {e}")
                self.progress_val.emit(i + 1)
            self.finished.emit(count)


class SearchWorker(BaseWorker):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, db: Database, include_trash: bool = False, threshold: float = 0.6) -> None:
        super().__init__()
        self.db, self.include_trash, self.threshold = db, include_trash, threshold

    def run(self) -> None:
        try:
            manager = DuplicateManager(self.db, ImageProcessor(), FeatureExtractor())
            groups = manager.db.get_duplicate_groups()
            if not groups:
                groups = manager.find_structural_duplicates(
                    threshold=self.threshold,
                    include_trash=self.include_trash,
                    progress_callback=lambda m, v: (
                        self.phase_status.emit(m),
                        self.progress_val.emit(v),
                    ),
                )
            self.finished.emit(groups)
        except Exception as e:
            logger.error(f"Search Error: {e}")
            self.error.emit(str(e))


class PersonAction:
    REGISTER_NEW, ASSOCIATE_EXISTING, IGNORE_FACE = (
        "register_new",
        "associate_existing",
        "ignore_face",
    )
    IGNORE_CLUSTER, UNIGNORE_CLUSTER, RENAME_PERSON = (
        "ignore_cluster",
        "unignore_cluster",
        "rename_person",
    )
    DELETE_PERSON, DELETE_EMPTY_PERSONS = "delete_person", "delete_empty_persons"
    UNREGISTER = "unregister"


class PersonManagementWorker(BaseWorker):
    refresh_requested = Signal()

    def __init__(self, db: Database, action_type: str, params: dict[str, Any]) -> None:
        super().__init__()
        self.db, self.action_type, self.params = db, action_type, params

    def run(self) -> None:
        with Profiler(f"PersonMgmt ({self.action_type})"):
            try:
                if self.is_cancelled:
                    return
                h = {
                    PersonAction.REGISTER_NEW: self._handle_register_new,
                    PersonAction.ASSOCIATE_EXISTING: self._handle_associate_existing,
                    PersonAction.IGNORE_FACE: self._handle_ignore_face,
                    PersonAction.IGNORE_CLUSTER: self._handle_ignore_cluster,
                    PersonAction.UNIGNORE_CLUSTER: self._handle_unignore_cluster,
                    PersonAction.RENAME_PERSON: self._handle_rename_person,
                    PersonAction.DELETE_PERSON: self._handle_delete_person,
                    PersonAction.DELETE_EMPTY_PERSONS: self._handle_delete_empty_persons,
                    PersonAction.UNREGISTER: self._handle_unregister,
                }
                if self.action_type in h:
                    h[self.action_type]()
                else:
                    raise ValueError(f"Unknown action {self.action_type}")
                self.finished_task.emit(True, "Success.")
                self.refresh_requested.emit()
            except Exception as e:
                logger.exception("PersonMgmt Error:")
                self.finished_task.emit(False, str(e))

    def _handle_rename_person(self) -> None:
        self.db.face_repo.upsert_cluster(self.params["cluster_id"], self.params["name"].strip())

    def _handle_register_new(self) -> None:
        with Profiler("PersonMgmt._handle_register_new"):
            cid = self.db.face_repo.create_cluster_manual(self.params["name"])
            self.db.update_faces_association_batch(self.params["face_ids"], cid, False)

    def _handle_associate_existing(self) -> None:
        face_ids = self.params.get("face_ids", [])
        cluster_id = self.params.get("cluster_id")
        logger.info(f"PersonMgmt: Associating {len(face_ids)} faces with cluster_id={cluster_id}")
        with Profiler("PersonMgmt._handle_associate_existing"):
            self.db.update_faces_association_batch(face_ids, cluster_id, False)

    def _handle_ignore_face(self) -> None:
        self.db.update_faces_association_batch(self.params["face_ids"], None, True)

    def _handle_unignore_cluster(self) -> None:
        self.db.face_repo.set_cluster_ignored(self.params["cluster_id"], False)

    def _handle_ignore_cluster(self) -> None:
        self.db.face_repo.set_cluster_ignored(self.params["cluster_id"], True)

    def _handle_delete_person(self) -> None:
        self.db.face_repo.delete_cluster(self.params["cluster_id"])

    def _handle_delete_empty_persons(self) -> None:
        self.db.face_repo.delete_empty_clusters()

    def _handle_unregister(self) -> None:
        self.db.face_repo.update_faces_association_batch(self.params["face_ids"], None, False)


def clustering_logic(
    db: Database,
    face_proc: FaceProcessor,
    folder_path: str,
    min_samples: int = 2,
    eps: float = 0.42,
) -> None:
    # Use proper LIKE escaping for Windows paths
    pat = normalize_path(folder_path)
    if not pat.endswith(os.sep):
        pat += os.sep
    pat = pat.replace("|", "||").replace("_", "|_").replace("%", "|%") + "%"

    ignored_vectors = db.get_ignored_vectors()
    with db.get_connection() as conn:
        all_f = conn.execute(
            "SELECT face_id, vector_blob FROM faces WHERE file_path LIKE ? ESCAPE '|'", (pat,)
        ).fetchall()
        if not all_f:
            return

        fids, embs, ifids = _filter_ignored_faces(all_f, ignored_vectors, eps)

        if ifids:
            db.face_repo.remove_face_batch(ifids)
        if len(embs) >= min_samples:
            _execute_clustering(db, face_proc, fids, embs, eps, min_samples)


def _filter_ignored_faces(
    all_f, ignored_vectors, eps
) -> tuple[list[int], list[np.ndarray], list[int]]:
    fids, embs, ifids = [], [], []
    for fid, vblob in all_f:
        emb = np.frombuffer(vblob, dtype=np.float32)
        is_i = False
        if ignored_vectors:
            ne = emb / (np.linalg.norm(emb) + 1e-6)
            for iv in ignored_vectors:
                ni = iv / (np.linalg.norm(iv) + 1e-6)
                if (1.0 - np.dot(ne, ni)) < eps:
                    is_i = True
                    break
        if is_i:
            ifids.append(fid)
        else:
            fids.append(fid)
            embs.append(emb)
    return fids, embs, ifids


def _execute_clustering(db, face_proc, fids, embs, eps, min_samples) -> None:
    labels = face_proc.cluster_faces(embs, eps=eps, min_samples=min_samples)
    upd = [(int(label), fid) for fid, label in zip(fids, labels) if label != -1]
    if upd:
        db.face_repo.update_faces_cluster_batch(upd)
        db.face_repo.create_clusters_batch(list(set(u[0] for u in upd)))


class BatchFileDeleteWorker(BaseWorker):
    def __init__(self, db: Database, file_paths: list[str], current_folder: str) -> None:
        super().__init__()
        self.db, self.file_paths, self.current_folder = db, file_paths, current_folder

    def run(self) -> None:
        with Profiler(f"BatchDelete (count={len(self.file_paths)})"):
            try:
                count, trashed = 0, []
                for p in self.file_paths:
                    if self.is_cancelled:
                        break
                    try:
                        norm = normalize_path(p)
                        if not os.path.exists(norm):
                            continue
                        new_p = move_file_to_local_trash(norm, self.current_folder)
                        mi = self.db.media_repo.get_media(p)
                        if mi:
                            self.db.media_repo.delete_media(p)
                            trashed.append(replace(mi, file_path=new_p, is_in_trash=True))
                        count += 1
                    except Exception as e:
                        logger.error(f"Del Error {p}: {e}")
                if trashed:
                    self.db.media_repo.add_media_batch(trashed)
                self.finished_task.emit(True, "Success.")
            except Exception as e:
                logger.exception("BatchDelete Error:")
                self.finished_task.emit(False, str(e))


class BatchFileReleaseWorker(BaseWorker):
    def __init__(self, db: Database, file_paths: list[str]) -> None:
        super().__init__()
        self.db, self.file_paths = db, file_paths

    def run(self) -> None:
        with Profiler(f"BatchRelease (count={len(self.file_paths)})"):
            try:
                self.db.media_repo.release_files_from_groups(self.file_paths)
                self.finished_task.emit(True, "Success.")
            except Exception as e:
                logger.exception("BatchRelease Error:")
                self.finished_task.emit(False, str(e))


class PersonOptimizationWorker(BaseWorker):
    """
    Analyzes a person's registered faces to find 'Appearance Stages' and 'Outliers'.
    Uses DBSCAN for internal sub-clustering (Similarity Chain concept).
    """

    result_ready = Signal(dict)

    def __init__(self, db: Database, target_person_id: int):
        super().__init__()
        self.db = db
        self.target_person_id = target_person_id

    def run(self) -> None:
        with Profiler(f"PersonOptimization (person={self.target_person_id})"):
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT face_id, vector_blob FROM faces WHERE cluster_id = ? AND vector_blob IS NOT NULL",
                        (self.target_person_id,),
                    )
                    rows = cursor.fetchall()

                if not rows:
                    self.finished_task.emit(False, "No embeddings found for this person.")
                    return

                face_ids, embs = self._prepare_embeddings(rows)

                if not embs:
                    self.finished_task.emit(False, "Failed to decode embeddings.")
                    return

                # Perform DBSCAN to find sub-clusters (Appearance stages)
                data = np.array(embs)
                clustering = DBSCAN(eps=0.40, min_samples=2, metric="cosine").fit(data)
                labels = clustering.labels_

                outlier_indices = np.where(labels == -1)[0]
                outlier_fids = [face_ids[idx] for idx in outlier_indices]
                stages_count = len(set(labels)) - (1 if -1 in labels else 0)

                # Fetch full FaceInfo for outliers in background to avoid main-thread UI freeze
                outlier_faces = []
                if outlier_fids:
                    outlier_faces = self.db.face_repo.get_faces_by_ids(outlier_fids)

                result = {
                    "total_count": len(face_ids),
                    "outlier_count": len(outlier_fids),
                    "outlier_ids": outlier_fids,
                    "outlier_faces": outlier_faces,
                    "stages_count": stages_count,
                    "cluster_id": self.target_person_id,
                }

                self.result_ready.emit(result)
                self.finished_task.emit(True, f"Found {len(outlier_fids)} outliers.")

            except Exception as e:
                logger.exception("PersonOptimizationWorker Fatal Error:")
                self.finished_task.emit(False, str(e))

    def _prepare_embeddings(self, rows) -> tuple[list[int], list[np.ndarray]]:
        face_ids, embs = [], []
        for r in rows:
            try:
                emb = np.frombuffer(r[1], dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    face_ids.append(r[0])
                    embs.append(emb / norm)
            except (ValueError, TypeError):
                continue
        return face_ids, embs


class DatabaseSyncWorker(BaseWorker):
    """
    Handles heavy database migrations and denormalization in the background
    to prevent blocking the UI thread on startup.
    """

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db

    def run(self) -> None:
        try:
            logger.info("DatabaseSyncWorker: Starting background synchronization...")
            self.db.sync_capture_dates()
            self.finished_task.emit(True, "Database synchronization complete.")
        except Exception as e:
            logger.exception("DatabaseSyncWorker Fatal Error:")
            self.finished_task.emit(False, str(e))


class LibraryThumbnailWorker(BaseWorker):
    """
    Standardized thumbnail pre-loader for the Library view.
    Loads disk-based thumbnails into QImage memory objects to adhere to the
    'No Disk I/O in Paint' rule (Explosive Speed architecture).
    """

    batch_finished = Signal(object)  # Emits list[tuple[str, QImage]]

    def __init__(self, media_list: list[MediaRecord]) -> None:
        super().__init__()
        self.media_list = media_list

    def run(self) -> None:
        results: list[tuple[str, QImage]] = []
        with Profiler(f"LibraryThumbnailPreload (items={len(self.media_list)})"):
            for m in self.media_list:
                if self.is_cancelled:
                    break
                path = m.thumbnail_path
                if path and os.path.exists(path):
                    qimg = QImage(path)
                    if not qimg.isNull():
                        results.append((m.file_path, qimg))

                if len(results) >= 25:
                    self.batch_finished.emit(results)
                    results = []
                    self.msleep(10)  # Yield to UI thread

            if results:
                self.batch_finished.emit(results)
            self.finished_task.emit(True, "Library pre-loading complete.")


class FaceSortWorker(BaseWorker):
    """
    Background worker for calculating face similarities and sorting items.
    Prevents UI thread blocking during expensive vector operations.
    """

    results_ready = Signal(list)  # (face_id, similarity)

    def __init__(self, db: Database, face_ids: list[int], cluster_id: int):
        super().__init__()
        self.db = db
        self.face_ids = face_ids
        self.cluster_id = cluster_id

    def run(self) -> None:
        with Profiler(f"FaceSortWorker.run (items={len(self.face_ids)})"):
            try:
                # 1. Calculate Centroid
                centroid = self._calculate_centroid()
                if centroid is None:
                    self.finished_task.emit(False, "Could not calculate centroid.")
                    return

                # 2. Fetch target vectors in batch
                vectors = self.db.face_repo.get_face_vectors_batch(self.face_ids)

                # 3. Calculate similarities
                results = []
                for fid, emb in vectors.items():
                    norm = np.linalg.norm(emb)
                    sim = float(np.dot(emb / norm, centroid)) if norm > 0 else 0.0
                    results.append((fid, sim))

                self.results_ready.emit(results)
                self.finished_task.emit(True, "Sort items ready.")
            except Exception as e:
                logger.exception("FaceSortWorker Error:")
                self.finished_task.emit(False, str(e))

    def _calculate_centroid(self) -> Optional[np.ndarray]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT vector_blob FROM faces WHERE cluster_id = ? AND vector_blob IS NOT NULL",
                (self.cluster_id,),
            ).fetchall()
            if not rows:
                return None
            embs = []
            for r in rows:
                emb = np.frombuffer(r[0], dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    embs.append(emb / norm)
            if not embs:
                return None
            mean_vec = np.mean(embs, axis=0)
            norm_c = np.linalg.norm(mean_vec)
            return (mean_vec / norm_c) if norm_c > 0 else mean_vec


class FaceDeduplicationWorker(BaseWorker):
    """
    Cleans up existing duplicate face records in the database.
    Applies spatial NMS for photos and temporal similarity check for videos.
    """

    def __init__(self, db: Database, folder_path: Optional[str] = None):
        super().__init__()
        self.db = db
        self.folder_path = normalize_path(folder_path) if folder_path else None
        self.face_proc = FaceProcessor()
        self.cache_dir = get_face_cache_dir()

    def run(self) -> None:
        with Profiler(f"FaceDeduplicationWorker.run ({self.folder_path or 'Global'})"):
            try:
                # 1. Identify files with multiple faces
                files_to_check = self._get_files_with_multiple_faces()
                if not files_to_check:
                    self.finished_task.emit(True, "No duplicates found.")
                    return

                total = len(files_to_check)
                removed_count = 0

                for i, f_path in enumerate(files_to_check):
                    if self.is_cancelled:
                        break

                    self.phase_status.emit(
                        f"Deduplicating {i + 1}/{total}: {os.path.basename(f_path)}"
                    )
                    removed_count += self._deduplicate_file(f_path)
                    self.progress_val.emit(int((i + 1) / total * 100))

                self.finished_task.emit(
                    True, f"Deduplication complete. Removed {removed_count} redundant faces."
                )
            except Exception as e:
                logger.exception("FaceDeduplication Error:")
                self.finished_task.emit(False, str(e))

    def _get_files_with_multiple_faces(self) -> list[str]:
        with self.db.get_connection() as conn:
            # Task Fix: Use robust path pattern matching for Windows
            if self.folder_path:
                norm_pat = self.folder_path
                if not norm_pat.endswith(os.sep):
                    norm_pat += os.sep

                # Escape for LIKE
                esc_pat = norm_pat.replace("|", "||").replace("_", "|_").replace("%", "|%") + "%"
                query = "SELECT file_path, COUNT(*) as c FROM faces WHERE file_path LIKE ? ESCAPE '|' GROUP BY file_path HAVING c > 1"
                rows = conn.execute(query, (esc_pat,)).fetchall()
            else:
                query = "SELECT file_path, COUNT(*) as c FROM faces GROUP BY file_path HAVING c > 1"
                rows = conn.execute(query).fetchall()

            return [r[0] for r in rows]

    def _deduplicate_file(self, f_path: str) -> int:
        """Analyzes and removes duplicates for a single file."""
        faces = self.db.face_repo.get_faces_for_file(f_path)
        if len(faces) <= 1:
            return 0

        # Load embeddings (necessary for temporal/identity check)
        face_ids = [f.face_id for f in faces]
        vectors = self.db.face_repo.get_face_vectors_batch(face_ids)

        # Prepare data for deduplication logic
        face_data = []
        for f in faces:
            if f.face_id in vectors:
                face_data.append(
                    {
                        "face_id": f.face_id,
                        "embedding": vectors[f.face_id],
                        "bbox": f.bbox,
                        "frame_index": f.frame_index,
                        "det_score": 1.0,  # Placeholder as it's not in DB
                    }
                )

        if not face_data:
            return 0

        is_video = f_path.lower().endswith((".mp4", ".avi", ".mov", ".mkv"))
        keep_ids = set()

        if is_video:
            # For videos, we use embedding similarity across frames
            # Reusing the logic from FaceRecognitionWorker but adapted
            sorted_faces = sorted(face_data, key=lambda x: x["face_id"])
            keep_list = []
            for f in sorted_faces:
                is_dup = False
                v1 = f["embedding"] / (np.linalg.norm(f["embedding"]) + 1e-6)
                for k in keep_list:
                    v2 = k["embedding"] / (np.linalg.norm(k["embedding"]) + 1e-6)
                    if np.dot(v1, v2) > 0.88:
                        is_dup = True
                        break
                if not is_dup:
                    keep_list.append(f)
            keep_ids = {f["face_id"] for f in keep_list}
        else:
            # For photos, we use IoU within the same frame (0)
            sorted_faces = sorted(face_data, key=lambda x: x["face_id"])
            keep_list = []
            for f in sorted_faces:
                is_dup = False
                for k in keep_list:
                    if self.face_proc.compute_iou(f["bbox"], k["bbox"]) > 0.45:
                        is_dup = True
                        break
                if not is_dup:
                    keep_list.append(f)
            keep_ids = {f["face_id"] for f in keep_list}

        # Identify IDs to remove
        remove_ids = [f["face_id"] for f in face_data if f["face_id"] not in keep_ids]
        if remove_ids:
            self.db.face_repo.remove_face_batch(remove_ids)
            # Remove cache files
            for rid in remove_ids:
                c_path = os.path.join(self.cache_dir, f"face_{rid}.jpg")
                if os.path.exists(c_path):
                    try:
                        os.unlink(c_path)
                    except:
                        pass

        return len(remove_ids)


class TreeDataLoadWorker(BaseWorker):
    """Asynchronously fetches sub-items (years, months, locations) for tree nodes."""

    data_ready = Signal(object)  # TreeDataLoadResult

    def __init__(
        self, db: Database, item: Any, level: str, params: dict, include_trash: bool = False
    ):
        super().__init__()
        self.db = db
        self.item = item
        self.level = level
        self.params = params
        self.include_trash = include_trash

    def run(self) -> None:
        try:
            start_time = time.perf_counter()
            data = []
            if self.level == "years":
                data = self.db.media_repo.get_years(
                    self.params.get("cluster_id"), include_trash=self.include_trash
                )
            elif self.level == "months":
                data = self.db.media_repo.get_months(
                    self.params.get("cluster_id"),
                    self.params.get("year"),
                    include_trash=self.include_trash,
                )
            elif self.level == "locations":
                data = self.db.media_repo.get_locations(
                    self.params.get("cluster_id"),
                    self.params.get("year"),
                    self.params.get("month"),
                    include_trash=self.include_trash,
                )

            if self.is_cancelled:
                return

            elapsed = time.perf_counter() - start_time
            logger.info(f"PROFILER: TreeDataLoadWorker ({self.level}) took {elapsed:.4f}s")
            self.data_ready.emit(TreeDataLoadResult(item=self.item, data=data, level=self.level))

        except Exception as e:
            logger.exception(f"TreeDataLoadWorker ({self.level}) Error:")
            self.data_ready.emit(
                TreeDataLoadResult(
                    item=self.item, data=[], level=self.level, success=False, message=str(e)
                )
            )
