import json
import logging
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import Signal
from sklearn.cluster import DBSCAN

from core.base_worker import BaseWorker
from core.utils import Profiler

logger = logging.getLogger("PhotoArrange")


class GeneralSuggestionWorker(BaseWorker):
    """
    Analyzes UNKNOWN faces to suggest:
    1. IGNORE_IDENTICAL: Similar to existing ignored faces.
    2. IGNORE_RARE: Appearing <= 2 times.
    3. SUGGEST_NEW: Appearing >= face_min_samples times and not matched to anyone.
    """

    suggestions_ready = Signal(list)  # list of dict for FaceInfo hydration

    # Class-level cache for centroids to survive worker re-instantiation
    _known_cache = None  # list of (centroid, radius)
    _ignored_cache = None  # list of (centroid, radius)
    _cache_invalidated = True

    @classmethod
    def invalidate_cache(cls):
        """Called when database faces change to force re-clustering of centroids."""
        cls._cache_invalidated = True
        logger.info("GeneralSuggestionWorker: Cache invalidated.")

    def __init__(self, db, threshold: Optional[float] = None):
        super().__init__()
        self.db = db
        self.threshold = threshold

    def run(self):
        with Profiler("GeneralSuggestionWorker.run"):
            try:
                if self.is_cancelled:
                    return

                # 1. Fetch Unknown Faces
                with Profiler("GeneralSuggestion.UnknownFetch"):
                    with self.db.get_connection() as conn:
                        cursor = conn.execute("""
                            SELECT face_id, vector_blob, file_path, bbox_json, capture_date, frame_index
                            FROM faces
                            WHERE cluster_id = -1
                              AND is_ignored = 0
                              AND vector_blob IS NOT NULL
                            LIMIT 5000
                        """)
                        rows = cursor.fetchall()

                if not rows:
                    self.suggestions_ready.emit([])
                    self.finished_task.emit(True, "No unknown faces to analyze.")
                    return

                # 2. Vectorize & Normalize
                with Profiler("GeneralSuggestion.VectorPrep"):
                    blobs = [r[1] for r in rows]
                    matrix = np.array(
                        [np.frombuffer(b, dtype=np.float32) for b in blobs], dtype=np.float32
                    )

                    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    matrix = matrix / norms

                if self.is_cancelled:
                    return

                # 3. Fetch/Refresh Pattern Caches
                with Profiler("GeneralSuggestion.CacheRefresh"):
                    if (
                        GeneralSuggestionWorker._cache_invalidated
                        or GeneralSuggestionWorker._known_cache is None
                    ):
                        GeneralSuggestionWorker._ignored_cache = self._extract_ignored_stages()
                        GeneralSuggestionWorker._known_cache = self._extract_known_stages()
                        GeneralSuggestionWorker._cache_invalidated = False
                        logger.info("GeneralSuggestionWorker: Pattern caches refreshed.")
                    else:
                        logger.info("GeneralSuggestionWorker: Using cached patterns.")

                    ignored_stages = GeneralSuggestionWorker._ignored_cache
                    ignored_centroids = (
                        np.array([s[0] for s in ignored_stages]) if ignored_stages else None
                    )
                    ignored_radii = (
                        np.array([max(s[1], 0.45) for s in ignored_stages])
                        if ignored_stages
                        else None
                    )

                    known_stages = GeneralSuggestionWorker._known_cache
                    known_centroids = (
                        np.array([s[0] for s in known_stages]) if known_stages else None
                    )

                    if self.threshold is not None:
                        excl_radius = float(1.0 - self.threshold)
                    else:
                        raw_thresh = self.db.settings_repo.get_setting("face_merge_threshold", 55)
                        try:
                            excl_radius = float(raw_thresh) / 100.0
                        except Exception:
                            excl_radius = 0.55

                    known_radii = (
                        np.array([max(s[1], excl_radius) for s in known_stages])
                        if known_stages
                        else None
                    )

                # 4. Clustering Unknowns
                with Profiler("GeneralSuggestion.DBSCAN"):
                    clustering = DBSCAN(eps=0.38, min_samples=1, metric="cosine", n_jobs=None).fit(
                        matrix
                    )
                    labels = clustering.labels_

                unique_labels = set(labels)

                # 5. Analyze Clusters (Optimized with vectorized distance checks)
                min_samples = int(self.db.settings_repo.get_setting("face_min_samples", 3))
                results = []

                for label in unique_labels:
                    if self.is_cancelled:
                        break
                    if label == -1:
                        continue

                    idx = np.where(labels == label)[0]
                    count = len(idx)

                    cluster_matrix = matrix[idx]
                    mean_vec = np.mean(cluster_matrix, axis=0)
                    norm_v = np.linalg.norm(mean_vec)
                    centroid = (mean_vec / norm_v) if norm_v > 0 else mean_vec

                    # Check 0: Match against Registered Persons (Registered exclusion)
                    is_known_match = False
                    if known_centroids is not None:
                        # Vectorized distance to all known centroids
                        dists = np.linalg.norm(centroid - known_centroids, axis=1)
                        if np.any(dists <= known_radii):
                            is_known_match = True

                    if is_known_match:
                        continue

                    # Check 1: Match against Ignored Patterns
                    is_ignored_match = False
                    if ignored_centroids is not None:
                        dists = np.linalg.norm(centroid - ignored_centroids, axis=1)
                        if np.any(dists <= ignored_radii):
                            is_ignored_match = True

                    # Assign suggestion type
                    s_type = ""
                    s_label = ""
                    if is_ignored_match:
                        s_type = "IGNORE_IDENTICAL"
                        s_label = "無視リストと一致"
                    elif count <= 2:
                        s_type = "IGNORE_RARE"
                        s_label = f"低頻度 ({count}回)"
                    elif count >= min_samples:
                        s_type = "SUGGEST_NEW"
                        s_label = f"頻出 ({count}回) - 新規登録候補"
                    else:
                        # Cluster exists but doesn't meet NEW threshold
                        s_type = "SUGGEST_NEW"
                        s_label = f"出現回数 {count}回 - その他候補"

                    logger.info(
                        f"GeneralSuggestion: Cluster label {label} result: {s_type} ({count} faces)"
                    )

                    for row_idx in idx:
                        r = rows[row_idx]
                        results.append(
                            {
                                "face_id": r[0],
                                "file_path": r[2],
                                "bbox": json.loads(r[3]) if r[3] else None,
                                "capture_date": r[4],
                                "frame_index": r[5],
                                "suggestion_type": s_type,
                                "suggestion_label": s_label,
                            }
                        )

                # Sort and Emit
                results.sort(
                    key=lambda x: (x["suggestion_type"], x["capture_date"] or ""), reverse=True
                )

                if not self.is_cancelled:
                    if not results:
                        logger.info(
                            "GeneralSuggestionWorker: No results found after filtering/analysis."
                        )
                        self.suggestions_ready.emit([])
                    else:
                        final_results = results[:1000]
                        batch_size = 50
                        logger.info(
                            f"GeneralSuggestionWorker: Emitting {len(final_results)} suggestions in batches."
                        )
                        for i in range(0, len(final_results), batch_size):
                            if self.is_cancelled:
                                break
                            self.suggestions_ready.emit(final_results[i : i + batch_size])
                            if i + batch_size < len(final_results):
                                time.sleep(0.01)

                self.finished_task.emit(True, f"Found {len(results)} suggestion items.")

            except Exception as e:
                logger.exception("GeneralSuggestionWorker Error:")
                self.finished_task.emit(False, str(e))

    def _extract_ignored_stages(self) -> list[tuple[np.ndarray, float]]:
        """Groups ignored faces into patterns to compare against unknowns."""
        try:
            with Profiler("GeneralSuggestion.IgnoredFetch"):
                with self.db.get_connection() as conn:
                    cursor = conn.execute("""
                        SELECT vector_blob FROM faces
                        WHERE is_ignored = 1 AND vector_blob IS NOT NULL
                        LIMIT 3000
                    """)
                    rows = cursor.fetchall()

            if not rows:
                return []

            with Profiler("GeneralSuggestion.IgnoredPrep"):
                embs = []
                for r in rows:
                    emb = np.frombuffer(r[0], dtype=np.float32)
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        embs.append(emb / norm)

            if not embs:
                return []

            with Profiler("GeneralSuggestion.IgnoredClustering"):
                data = np.array(embs)
                # Use stricter eps for ignored patterns to avoid over-collapsing
                clustering = DBSCAN(eps=0.35, min_samples=3, metric="cosine", n_jobs=-1).fit(data)
                labels = clustering.labels_

            stages = []
            # ...
            for label in set(labels):
                if label == -1:
                    continue
                idx = np.where(labels == label)[0]
                cluster_embs = data[idx]
                mean_vec = np.mean(cluster_embs, axis=0)
                norm_v = np.linalg.norm(mean_vec)
                centroid = (mean_vec / norm_v) if norm_v > 0 else mean_vec
                # Vectorized distance calculation
                dists = np.linalg.norm(cluster_embs - centroid, axis=1)
                max_d = np.max(dists) if len(dists) > 0 else 0.4
                stages.append((centroid, max_d))

            return stages
        except Exception as e:
            logger.error(f"Error extracting ignored stages: {e}")
            return []

    def _extract_known_stages(self) -> list[tuple[np.ndarray, float]]:
        """Groups registered faces into patterns to skip them in unknown suggestions."""
        try:
            with Profiler("GeneralSuggestion.KnownFetch"):
                with self.db.get_connection() as conn:
                    # Fetch faces of all registered persons (cluster_id >= 0)
                    cursor = conn.execute("""
                        SELECT vector_blob, cluster_id FROM faces
                        WHERE cluster_id >= 0 AND is_ignored = 0 AND vector_blob IS NOT NULL
                        LIMIT 10000
                    """)
                    rows = cursor.fetchall()

            if not rows:
                return []

            with Profiler("GeneralSuggestion.KnownPrep"):
                data_by_cluster = {}
                for r in rows:
                    cid = r[1]
                    emb = np.frombuffer(r[0], dtype=np.float32)
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        if cid not in data_by_cluster:
                            data_by_cluster[cid] = []
                        data_by_cluster[cid].append(emb / norm)

            stages = []
            with Profiler("GeneralSuggestion.KnownClustering"):
                for cid, embs in data_by_cluster.items():
                    data = np.array(embs)
                    # OPTIMIZATION: Use conditional clustering.
                    # If person has few faces (< 50), simple mean is much faster than starting DBSCAN engine.
                    if len(data) < 50:
                        mean_vec = np.mean(data, axis=0)
                        norm_v = np.linalg.norm(mean_vec)
                        centroid = (mean_vec / norm_v) if norm_v > 0 else mean_vec
                        dists = np.linalg.norm(data - centroid, axis=1)
                        max_d = np.max(dists) if len(dists) > 0 else 0.4
                        stages.append((centroid, max_d))
                    else:
                        # Cluster each person to handle growth/aging (stages)
                        # n_jobs=None to avoid thread pool thrashing in many small jobs
                        clustering = DBSCAN(
                            eps=0.38, min_samples=3, metric="cosine", n_jobs=None
                        ).fit(data)
                        labels = clustering.labels_
                        for label in set(labels):
                            if label == -1:
                                continue  # Skip noise
                            idx = np.where(labels == label)[0]
                            cluster_embs = data[idx]
                            mean_vec = np.mean(cluster_embs, axis=0)
                            norm_v = np.linalg.norm(mean_vec)
                            centroid = (mean_vec / norm_v) if norm_v > 0 else mean_vec
                            d_vec = np.linalg.norm(cluster_embs - centroid, axis=1)
                            max_d = np.max(d_vec) if len(d_vec) > 0 else 0.4
                            stages.append((centroid, max_d))

            logger.info(
                f"GeneralSuggestion: Extracted {len(stages)} known pattern stages from {len(data_by_cluster)} persons."
            )
            return stages
        except Exception as e:
            logger.error(f"Error extracting known stages: {e}")
            return []
