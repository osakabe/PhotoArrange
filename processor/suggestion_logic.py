import json
import logging
import time

import numpy as np
from PySide6.QtCore import Signal
from sklearn.cluster import DBSCAN

from core.base_worker import BaseWorker
from core.utils import Profiler

logger = logging.getLogger("PhotoArrange")


class FaceSuggestionWorker(BaseWorker):
    """
    Finds "Unknown" faces that are similar to a specific person's average embedding.
    Uses cosine similarity for matching (v2.3).
    """

    suggestions_ready = Signal(list)  # list of face_info

    def __init__(self, db, target_person_id, threshold=0.6):
        super().__init__()
        self.db = db
        self.target_person_id = target_person_id
        self.threshold = threshold

    def run(self):
        worker_start = time.perf_counter()
        with Profiler(f"FaceSuggestionWorker.run (person={self.target_person_id})"):
            try:
                # 1. Extract Representative Appearance Stages (Multi-Centroids)
                stages = self._extract_person_stages()
                if not stages:
                    logger.warning(
                        f"FaceSuggestionWorker: No embeddings for person {self.target_person_id}"
                    )
                    self.finished_task.emit(False, "No embeddings found for person.")
                    return

                # Get threshold from settings (fallback to 0.55 if not set)
                # Setting stored is integer (10-90), e.g., 55 -> 0.55 distance
                raw_thresh = self.db.settings_repo.get_setting("face_merge_threshold", 55)
                try:
                    sug_radius = float(raw_thresh) / 100.0
                except (ValueError, TypeError):
                    sug_radius = 0.55

                logger.info(
                    f"FaceSuggestionWorker: Using radius threshold {sug_radius:.2f} (from setting {raw_thresh})"
                )

                # Collect all stage centroids for comparison
                stage_centroids = [s[0] for s in stages]
                # Combined radii: A candidate matches if it's within ANY stage's radius
                radii = [max(s[1], sug_radius) for s in stages]

                # --- STAGE 1: LIGHT FETCH (Candidate Selection) ---
                with Profiler("Suggestion.Stage1_DB_Fetch"):
                    with self.db.get_connection() as conn:
                        # Optimized: Only fetch ID and Vector Blob. Avoid expensive JOIN with media at this stage.
                        cursor = conn.execute("""
                            SELECT face_id, vector_blob
                            FROM faces
                            WHERE (cluster_id IS NULL OR cluster_id = -1) AND is_ignored = 0 AND vector_blob IS NOT NULL
                            LIMIT 30000
                        """)
                        rows = cursor.fetchall()

                if not rows:
                    self.suggestions_ready.emit([])
                    self.finished_task.emit(True, "No unknown faces to suggest.")
                    return

                if self.is_cancelled:
                    return

                # --- STAGE 2: VECTORIZED TENSOR PREP ---
                with Profiler("Suggestion.Stage2_VectorPrep"):
                    face_ids = []
                    blobs = []
                    for r in rows:
                        face_ids.append(r[0])
                        blobs.append(r[1])

                    matrix = np.array(
                        [np.frombuffer(b, dtype=np.float32) for b in blobs], dtype=np.float32
                    )

                    # Normalize candidate vectors for cosine similarity
                    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0  # Avoid div by zero
                    matrix = matrix / norms

                # --- STAGE 3: VECTORIZED MATCHING (Multi-Centroid) ---
                with Profiler(f"Suggestion.Stage3_Matching (stages={len(stages)})"):
                    # Calculate distance to EACH stage centroid
                    all_stage_dists = []
                    for c_emb in stage_centroids:
                        all_stage_dists.append(np.linalg.norm(matrix - c_emb, axis=1))

                    # Target distance is the minimum distance to ANY stage
                    dists = np.min(all_stage_dists, axis=0)

                    # A face matches if it is winthin the radius of its closest stage
                    # For simplicity, we can use a global threshold or individual radii.
                    # Here we use individual radii for the matched stage.
                    closest_stage_idx = np.argmin(all_stage_dists, axis=0)
                    matched_radii = np.array([radii[idx] for idx in closest_stage_idx])

                    matches_mask = dists <= matched_radii
                    match_indices = np.where(matches_mask)[0]

                if len(match_indices) == 0:
                    self.suggestions_ready.emit([])
                    self.finished_task.emit(True, "No matches found.")
                    return

                if self.is_cancelled:
                    return

                # --- STAGE 4: SORTING & STREAMING HYDRATION ---
                sorted_indices = match_indices[np.argsort(dists[match_indices])]
                top_indices = sorted_indices[:1000]  # Still cap at 1000 for safety

                hydration_start = time.perf_counter()
                total_matches_found = 0

                # Stream in chunks of 50
                for i in range(0, len(top_indices), 50):
                    if self.is_cancelled:
                        break

                    chunk_indices = top_indices[i : i + 50]
                    chunk_face_ids = [face_ids[idx] for idx in chunk_indices]
                    id_list_str = ",".join(map(str, chunk_face_ids))

                    chunk_results = []
                    with self.db.get_connection() as conn:
                        h_query = f"""
                            SELECT f.face_id, f.file_path, f.bbox_json, f.cluster_id, f.is_ignored, m.capture_date, f.frame_index, f.vector_blob
                            FROM faces f
                            LEFT JOIN media m ON f.file_path = m.file_path
                            WHERE f.face_id IN ({id_list_str})
                        """
                        h_rows = conn.execute(h_query).fetchall()

                        # Create dicts for UI
                        for h_row in h_rows:
                            # Re-calculate similarity for this specific row against its BEST stage
                            emb = np.frombuffer(h_row[7], dtype=np.float32)
                            norm = np.linalg.norm(emb)
                            norm_emb = emb / norm if norm > 0 else emb

                            # Find best matching stage for this face
                            stage_sims = [float(np.dot(norm_emb, c)) for c in stage_centroids]
                            best_sim = max(stage_sims)
                            best_dist = float(
                                np.linalg.norm(norm_emb - stage_centroids[np.argmax(stage_sims)])
                            )

                            chunk_results.append(
                                {
                                    "face_id": h_row[0],
                                    "file_path": h_row[1],
                                    "bbox": json.loads(h_row[2]) if h_row[2] else None,
                                    "cluster_id": h_row[3],
                                    "is_ignored": bool(h_row[4]),
                                    "capture_date": h_row[5],
                                    "frame_index": h_row[6],
                                    "similarity": best_sim,
                                    "distance": best_dist,
                                }
                            )

                    # Sort chunk by similarity to maintain consistency if DB returned out of order
                    chunk_results.sort(key=lambda x: x["similarity"], reverse=True)

                    if chunk_results:
                        self.suggestions_ready.emit(chunk_results)
                        total_matches_found += len(chunk_results)
                        time.sleep(0.005)  # Yield to UI thread

                hydration_duration = time.perf_counter() - hydration_start
                logger.info(
                    f"PROFILER: Suggestion Hydration (Streaming) took {hydration_duration:.4f}s for {total_matches_found} results."
                )

                total_duration = time.perf_counter() - worker_start
                logger.info(f"PROFILER: Total Suggestion Worker Time: {total_duration:.4f}s.")
                self.finished_task.emit(True, f"Found {total_matches_found} suggestions.")

            except Exception as e:
                logger.exception("FaceSuggestionWorker Fatal Error:")
                self.finished_task.emit(False, str(e))

    def _extract_person_stages(self) -> list[tuple[np.ndarray, float]]:
        """
        Groups the target person's photos into appearance 'Stages' using DBSCAN
        and returns a list of (centroid_embedding, radius) for each stage.
        """
        with Profiler("Suggestion._extract_person_stages"):
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT vector_blob FROM faces WHERE cluster_id = ? AND vector_blob IS NOT NULL",
                    (self.target_person_id,),
                )
                rows = cursor.fetchall()
                if not rows:
                    return []

                embs = []
                for r in rows:
                    try:
                        emb = np.frombuffer(r[0], dtype=np.float32)
                        norm = np.linalg.norm(emb)
                        if norm > 0:
                            embs.append(emb / norm)
                    except:
                        continue

                if not embs:
                    return []

                # If very few photos, treat as a single stage
                if len(embs) < 5:
                    mean_vec = np.mean(embs, axis=0)
                    norm_c = np.linalg.norm(mean_vec)
                    centroid = (mean_vec / norm_c) if norm_c > 0 else mean_vec
                    max_d = max([np.linalg.norm(centroid - e) for e in embs]) if embs else 0.45
                    return [(centroid, max_d)]

                # Use DBSCAN to find appearance-based sub-clusters (Stages)
                # eps=0.38 is balanced to find meaningful sub-clusters (stages)
                data = np.array(embs)
                clustering = DBSCAN(eps=0.38, min_samples=2, metric="cosine").fit(data)
                labels = clustering.labels_

                stages = []
                # Process each identified cluster
                unique_labels = set(labels)
                for label in unique_labels:
                    if label == -1:  # Outliers
                        continue

                    indices = np.where(labels == label)[0]
                    cluster_embs = data[indices]

                    # Sub-centroid
                    mean_vec = np.mean(cluster_embs, axis=0)
                    norm_c = np.linalg.norm(mean_vec)
                    centroid = (mean_vec / norm_c) if norm_c > 0 else mean_vec

                    # Radius for this stage
                    max_d = max([np.linalg.norm(centroid - e) for e in cluster_embs])
                    stages.append((centroid, max_d))

                # Check for outliers: if no clusters found or if people have very few photos in stages
                if not stages:
                    # Fallback to single mean
                    mean_vec = np.mean(embs, axis=0)
                    norm_c = np.linalg.norm(mean_vec)
                    centroid = (mean_vec / norm_c) if norm_c > 0 else mean_vec
                    max_d = max([np.linalg.norm(centroid - e) for e in embs])
                    return [(centroid, max_d)]

                logger.info(
                    f"Appearance Stages Extracted for Person {self.target_person_id}: {len(stages)} stages."
                )
                return stages
