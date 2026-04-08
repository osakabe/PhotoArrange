import json
import logging
from dataclasses import replace
from typing import Any, Iterable, Optional

import numpy as np

from ..models import ClusterInfo, FaceCountsResult, FaceInfo
from ..utils import Profiler, normalize_path
from .base import BaseRepository

logger = logging.getLogger("PhotoArrange")


class FaceRepository(BaseRepository):
    """
    Handles all operations related to detected faces, clusters (people), and tags.
    """

    def get_face_counts(self) -> FaceCountsResult:
        """Returns summary counts for categories and persons with a single optimized query."""
        with Profiler("FaceRepository.get_face_counts"):
            with self.get_connection() as conn:
                # Optimized query: avoids joining media for every face by checking if any media is corrupted first
                query = """
                    SELECT
                        f.cluster_id,
                        f.is_ignored,
                        COUNT(f.face_id) as face_count
                    FROM faces f
                    LEFT JOIN media m ON f.file_path = m.file_path COLLATE NOCASE
                    WHERE (m.is_corrupted = 0 OR m.is_corrupted IS NULL)
                      AND (m.is_in_trash = 0 OR m.is_in_trash IS NULL)
                    GROUP BY f.cluster_id, f.is_ignored
                """

                unknown_count = 0
                ignored_count = 0
                person_counts: dict[int, int] = {}

                rows = conn.execute(query).fetchall()
                for cid, is_ign, count in rows:
                    if is_ign == 1:
                        ignored_count += count
                    elif cid == -1:
                        unknown_count += count
                    else:
                        person_counts[cid] = count

                logger.info(
                    f"FaceRepository.get_face_counts: Aggregated {len(rows)} grouping rows."
                )
                return FaceCountsResult(
                    unknown=unknown_count, ignored=ignored_count, persons=person_counts
                )

    def get_clusters(self, include_ignored: bool = False) -> list[ClusterInfo]:
        """Returns all person clusters with their face counts."""
        with Profiler("FaceRepository.get_clusters"):
            with self.get_connection() as conn:
                where_clause = "f.cluster_id IS NOT NULL AND f.cluster_id != -1"
                if not include_ignored:
                    where_clause += (
                        " AND f.is_ignored = 0 AND (c.is_ignored IS NULL OR c.is_ignored = 0)"
                    )

                query = f"""
                    SELECT f.cluster_id, c.custom_name, c.is_ignored, COUNT(f.face_id) as face_count
                    FROM faces f
                    LEFT JOIN media m ON f.file_path = m.file_path COLLATE NOCASE
                    LEFT JOIN clusters c ON f.cluster_id = c.cluster_id
                    WHERE {where_clause}
                      AND (m.is_corrupted = 0 OR m.is_corrupted IS NULL)
                      AND (m.is_in_trash = 0 OR m.is_in_trash IS NULL)
                    GROUP BY f.cluster_id
                    HAVING face_count > 0
                """

                cursor = conn.execute(query)
                rows = cursor.fetchall()
                logger.info(f"FaceRepository.get_clusters: Found {len(rows)} potential clusters.")

                results = [
                    ClusterInfo(
                        cluster_id=r[0],
                        custom_name=r[1],
                        is_ignored=bool(r[2]),
                        face_count=r[3] if r[3] is not None else 0,
                    )
                    for r in rows
                ]
                results.sort(
                    key=lambda x: (x.custom_name is None, x.custom_name or "", x.cluster_id)
                )
                return results

    def get_person_list_with_counts(self, include_ignored: bool = False) -> list[ClusterInfo]:
        """Alias for get_clusters to ensure consistent count retrieval."""
        return self.get_clusters(include_ignored)

    def get_person_list_fast(self) -> list[ClusterInfo]:
        """Returns all registered persons/clusters quickly, without counting faces."""
        with Profiler("FaceRepository.get_person_list_fast"):
            with self.get_connection() as conn:
                query = (
                    "SELECT cluster_id, custom_name, is_ignored FROM clusters WHERE is_ignored = 0"
                )
                rows = conn.execute(query).fetchall()

                results = [
                    ClusterInfo(
                        cluster_id=r[0], custom_name=r[1], is_ignored=bool(r[2]), face_count=0
                    )
                    for r in rows
                ]
                results.sort(
                    key=lambda x: (x.custom_name is None, x.custom_name or "", x.cluster_id)
                )
                return results

    def upsert_cluster(
        self, cluster_id: int, name: Optional[str] = None, is_ignored: Optional[bool] = None
    ) -> None:
        with self.get_connection() as conn:
            if name is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO clusters (cluster_id, custom_name) VALUES (?, ?)",
                    (cluster_id, name),
                )
                conn.execute(
                    "UPDATE clusters SET custom_name = ? WHERE cluster_id = ?", (name, cluster_id)
                )
            if is_ignored is not None:
                conn.execute(
                    "UPDATE clusters SET is_ignored = ? WHERE cluster_id = ?",
                    (int(is_ignored), cluster_id),
                )
            conn.commit()

    def create_cluster_manual(self, name: str) -> int:
        """Creates a new cluster with a custom name and returns its ID."""
        with self.get_connection() as conn:
            # Handle existing names to prevent UNIQUE constraint failure
            cursor = conn.execute("SELECT cluster_id FROM clusters WHERE custom_name = ?", (name,))
            existing = cursor.fetchone()
            if existing:
                return int(existing[0])

            cursor = conn.execute("SELECT MAX(cluster_id) FROM clusters")
            row = cursor.fetchone()
            max_id = row[0] if row else None
            new_id = (max_id + 1) if max_id is not None and max_id >= 0 else 1000

            conn.execute(
                "INSERT OR IGNORE INTO clusters (cluster_id, custom_name) VALUES (?, ?)",
                (new_id, name),
            )
            conn.commit()
            return int(new_id)

    def get_faces_by_category(
        self,
        category: str,
        person_id: Optional[int] = None,
        limit: Optional[int] = None,
        last_capture_date: Optional[str] = None,
        last_face_id: Optional[int] = None,
    ) -> list[FaceInfo]:
        with Profiler(
            f"FaceRepository.get_faces_by_category ({category}, {person_id}, seek={bool(last_capture_date)})"
        ):
            with self.get_connection() as conn:
                query = """
                    SELECT f.face_id, f.file_path, f.bbox_json, f.cluster_id, f.is_ignored, f.capture_date, f.frame_index,
                           l.city, l.prefecture, l.country
                    FROM faces f
                    LEFT JOIN media m ON f.file_path = m.file_path
                    LEFT JOIN locations l ON m.location_id = l.location_id
                """
                clauses: list[str] = []
                params: list[Any] = []
                if category == "person":
                    clauses.append("f.cluster_id = ? AND f.is_ignored = 0")
                    params.append(person_id)
                elif category == "unknown":
                    clauses.append("f.cluster_id = -1 AND f.is_ignored = 0")
                elif category == "ignored":
                    clauses.append("f.is_ignored = 1")

                if last_capture_date:
                    if last_face_id:
                        clauses.append(
                            "(m.capture_date < ? OR (m.capture_date = ? AND f.face_id < ?))"
                        )
                        params.extend([last_capture_date, last_capture_date, last_face_id])
                    else:
                        clauses.append("m.capture_date < ?")
                        params.append(last_capture_date)

                if clauses:
                    query += " WHERE " + " AND ".join(clauses)

                query += " ORDER BY m.capture_date DESC, f.face_id DESC"

                if limit:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor = conn.execute(query, params)
                results = []
                for r in cursor.fetchall():
                    f = FaceInfo.from_db_row(r)
                    f = replace(f, metadata={"city": r[7], "prefecture": r[8], "country": r[9]})
                    results.append(f)
                return results

    def get_faces_by_ids(self, face_ids: list[int]) -> list[FaceInfo]:
        """Fetch multiple FaceInfo objects by their IDs in a single query."""
        if not face_ids:
            return []
        with Profiler(f"FaceRepository.get_faces_by_ids (count={len(face_ids)})"):
            with self.get_connection() as conn:
                id_list = ",".join(map(str, face_ids))
                query = f"""
                    SELECT f.face_id, f.file_path, f.bbox_json, f.cluster_id, f.is_ignored, m.capture_date, f.frame_index,
                           l.city, l.prefecture, l.country
                    FROM faces f
                    LEFT JOIN media m ON f.file_path = m.file_path
                    LEFT JOIN locations l ON m.location_id = l.location_id
                    WHERE f.face_id IN ({id_list})
                """
                cursor = conn.execute(query)
                results = []
                for r in cursor.fetchall():
                    f = FaceInfo.from_db_row(r)
                    f = replace(f, metadata={"city": r[7], "prefecture": r[8], "country": r[9]})
                    results.append(f)
                return results

    def get_face_vectors_batch(self, face_ids: list[int]) -> dict[int, np.ndarray]:
        """Fetch multiple face vectors in a single efficient query."""
        if not face_ids:
            return {}
        with Profiler(f"FaceRepository.get_face_vectors_batch (count={len(face_ids)})"):
            results: dict[int, np.ndarray] = {}
            with self.get_connection() as conn:
                id_list = ",".join(map(str, face_ids))
                query = f"SELECT face_id, vector_blob FROM faces WHERE face_id IN ({id_list})"
                cursor = conn.execute(query)
                for fid, blob in cursor.fetchall():
                    if blob:
                        results[fid] = np.frombuffer(blob, dtype=np.float32)
            return results

    def update_face_cluster(self, face_id: int, cluster_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute("UPDATE faces SET cluster_id = ? WHERE face_id = ?", (cluster_id, face_id))
            conn.commit()

    def update_face_association(
        self, face_id: int, person_id: Optional[int], is_ignored: bool = False
    ) -> None:
        """Updates association and ignored status for a face."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE faces SET cluster_id = ?, is_ignored = ? WHERE face_id = ?",
                (person_id, int(is_ignored), face_id),
            )
            conn.commit()

    def set_cluster_ignored(self, cluster_id: int, is_ignored: bool) -> None:
        """Marks a cluster and all its associated faces as ignored (or not)."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE clusters SET is_ignored = ? WHERE cluster_id = ?",
                (int(is_ignored), cluster_id),
            )
            conn.execute(
                "UPDATE faces SET is_ignored = ? WHERE cluster_id = ?",
                (int(is_ignored), cluster_id),
            )
            conn.commit()

    def remove_face(self, face_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute("DELETE FROM faces WHERE face_id = ?", (face_id,))
            conn.commit()

    def delete_cluster(self, cluster_id: int) -> None:
        """Deletes a cluster and dissociates its faces."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM clusters WHERE cluster_id = ?", (cluster_id,))
            conn.execute("UPDATE faces SET cluster_id = NULL WHERE cluster_id = ?", (cluster_id,))
            conn.commit()

    def delete_empty_clusters(self) -> None:
        """Removes clusters that have no associated faces."""
        with self.get_connection() as conn:
            conn.execute("""
                DELETE FROM clusters
                WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM faces WHERE cluster_id IS NOT NULL)
            """)
            conn.commit()

    def clear_face_data(self, folder_path: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            if folder_path:
                norm_path = normalize_path(folder_path)
                pattern = norm_path.replace("_", "[_]").replace("%", "[%]") + "%"
                conn.execute("DELETE FROM faces WHERE file_path LIKE ?", (pattern,))
            else:
                conn.execute("DELETE FROM faces")
                conn.execute("DELETE FROM clusters")
            conn.commit()

    def clear_all_data(self) -> None:
        with self.get_connection() as conn:
            conn.execute("DELETE FROM media")
            conn.execute("DELETE FROM media_features")
            conn.execute("DELETE FROM faces")
            conn.execute("DELETE FROM clusters")
            conn.execute("DELETE FROM duplicate_groups")
            conn.execute("DELETE FROM locations")
            conn.commit()

    def get_ignored_vectors(self) -> list[np.ndarray]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT vector_blob FROM faces WHERE is_ignored = 1")
            return [np.frombuffer(row[0], dtype=np.float32) for row in cursor.fetchall()]

    def remove_face_batch(self, face_ids: list[int]) -> None:
        if not face_ids:
            return
        with Profiler(f"FaceRepository.remove_face_batch (count={len(face_ids)})"):
            with self.get_connection() as conn:
                conn.executemany(
                    "DELETE FROM faces WHERE face_id = ?", [(fid,) for fid in face_ids]
                )
                conn.commit()

    def update_faces_association_batch(
        self, face_ids: list[int], person_id: Optional[int], is_ignored: bool = False
    ) -> Optional[int]:
        """Updates association and ignored status for multiple faces in a single transaction."""
        if not face_ids:
            return 0
        with Profiler(f"FaceRepository.update_faces_association_batch (count={len(face_ids)})"):
            with self.get_connection() as conn:
                cur = conn.executemany(
                    "UPDATE faces SET cluster_id = ?, is_ignored = ? WHERE face_id = ?",
                    [(person_id, int(is_ignored), fid) for fid in face_ids],
                )
                conn.commit()
                rowcount = cur.rowcount
                logger.info(
                    f"FaceRepository: Updated {rowcount} faces. (Target cluster={person_id}, IDs: {face_ids[:5]}...)"
                )
                return rowcount

    def update_faces_cluster_batch(self, update_batch: list[tuple[int, int]]) -> None:
        """Batch updates cluster_id for specific faces. update_batch = [(cluster_id, face_id), ...]"""
        if not update_batch:
            return
        with self.get_connection() as conn:
            conn.executemany("UPDATE faces SET cluster_id = ? WHERE face_id = ?", update_batch)
            conn.commit()

    def create_clusters_batch(self, cluster_ids: list[int]) -> None:
        """Ensures clusters exist for the given IDs with generic names."""
        if not cluster_ids:
            return
        with self.get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO clusters (cluster_id, custom_name) VALUES (?, ?)",
                [(cid, f"Person {cid + 1}") for cid in cluster_ids],
            )
            conn.commit()

    def get_faces_for_file(self, file_path: str) -> list[FaceInfo]:
        norm_path = normalize_path(file_path)
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT face_id, file_path, bbox_json, cluster_id, is_ignored, capture_date, frame_index FROM faces WHERE file_path = ?",
                (norm_path,),
            )
            return [FaceInfo.from_db_row(row) for row in cursor.fetchall()]

    def clear_faces_for_file(self, file_path: str) -> None:
        norm_path = normalize_path(file_path)
        with self.get_connection() as conn:
            conn.execute("DELETE FROM faces WHERE file_path = ?", (norm_path,))
            conn.commit()

    def add_faces_batch(self, faces: Iterable[FaceInfo]) -> None:
        """Inserts multiple face records."""
        with self.get_connection() as conn:
            data = [
                (
                    normalize_path(f.file_path),
                    f.vector_blob,
                    json.dumps(f.bbox),
                    f.cluster_id,
                    int(f.is_ignored),
                    f.capture_date,
                    f.frame_index,
                )
                for f in faces
            ]
            conn.executemany(
                """
                INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored, capture_date, frame_index)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                data,
            )
            conn.commit()

    def get_all_faces_for_clustering(self) -> list[FaceInfo]:
        """Fetches all faces with their vectors for the global clustering process."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT face_id, file_path, vector_blob, cluster_id FROM faces")
            return [FaceInfo.from_cluster_update_row(row) for row in cursor.fetchall()]

    def delete_faces_in_folder(self, folder_path: str) -> None:
        """Deletes all faces for files within a specific folder."""
        pattern = normalize_path(folder_path) + "%"
        with self.get_connection() as conn:
            conn.execute("DELETE FROM faces WHERE file_path LIKE ?", (pattern,))
            conn.commit()
