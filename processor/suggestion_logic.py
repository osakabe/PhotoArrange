import logging
import json
import numpy as np
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("PhotoArrange")

class FaceSuggestionWorker(QThread):
    """
    Finds "Unknown" faces that are similar to a specific person's average embedding.
    Uses cosine similarity for matching (v2.3).
    """
    suggestions_ready = Signal(list) # list of face_info
    finished = Signal()

    def __init__(self, db, target_person_id, threshold=0.6, limit=100):
        super().__init__()
        self.db = db
        self.target_person_id = target_person_id
        self.threshold = threshold
        self.limit = limit
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        try:
            # 1. Get average embedding for the target person
            target_embedding = self._get_person_centroid()
            if target_embedding is None:
                logger.warning(f"FaceSuggestionWorker: No embeddings for person {self.target_person_id}")
                self.finished.emit()
                return

            # 2. Fetch ALL unknown faces using a direct query to include vector_blob and capture_date
            # We bypass the standard get_faces_by_category for performance.
            matches = []
            with self.db.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT f.face_id, f.file_path, f.bbox_json, f.cluster_id, f.is_ignored, m.capture_date, f.frame_index, f.vector_blob
                    FROM faces f
                    LEFT JOIN media m ON f.file_path = m.file_path
                    WHERE (f.cluster_id IS NULL OR f.cluster_id = -1) AND f.is_ignored = 0 AND f.vector_blob IS NOT NULL
                    LIMIT 10000
                ''')
                rows = cursor.fetchall()

                logger.info(f"FaceSuggestionWorker: Comparing {len(rows)} unknowns against centroid.")

                for row in rows:
                    if not self.is_running:
                        break

                    try:
                        # Decode vector from BLOB
                        emb = np.frombuffer(row[7], dtype=np.float32)
                        # Normalize for cosine similarity
                        norm = np.linalg.norm(emb)
                        if norm > 0:
                            emb = emb / norm
                        
                        similarity = float(np.dot(target_embedding, emb))
                        if similarity >= self.threshold:
                            face_info = {
                                "face_id": row[0],
                                "file_path": row[1],
                                "bbox": json.loads(row[2]) if row[2] else None,
                                "cluster_id": row[3],
                                "is_ignored": bool(row[4]),
                                "capture_date": row[5],
                                "frame_index": row[6],
                                "similarity": similarity
                            }
                            matches.append(face_info)
                    except Exception as e:
                        continue

            # 3. Sort by similarity and take limit
            matches.sort(key=lambda x: x["similarity"], reverse=True)
            self.suggestions_ready.emit(matches[:self.limit])
            self.finished.emit()

        except Exception as e:
            logger.exception(f"FaceSuggestionWorker ERROR: {e}")
            self.finished.emit()

    def _get_person_centroid(self):
        """Calculates the mean embedding of all faces in the cluster."""
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT vector_blob FROM faces WHERE cluster_id = ? AND vector_blob IS NOT NULL", (self.target_person_id,))
            rows = cursor.fetchall()
            
            if not rows: return None
            
            embs = []
            for r in rows:
                try:
                    # Decode from BLOB instead of JSON
                    emb = np.frombuffer(r[0], dtype=np.float32)
                    # Normalize individual embedding
                    norm = np.linalg.norm(emb)
                    if norm > 0: embs.append(emb / norm)
                except: continue
            
            if not embs: return None
            
            centroid = np.mean(embs, axis=0)
            # Re-normalize centroid for correct cosine similarity
            norm_c = np.linalg.norm(centroid)
            return (centroid / norm_c) if norm_c > 0 else None
