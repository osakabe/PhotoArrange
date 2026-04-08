import json
import logging
import os
import sqlite3
import time
from typing import Any, Iterable, Optional

from ..models import (
    DuplicateStats,
    LocationCount,
    MediaRecord,
    MonthCount,
    YearCount,
)
from ..utils import Profiler, normalize_path
from .base import BaseRepository

logger = logging.getLogger("PhotoArrange")


class MediaRepository(BaseRepository):
    """
    Handles all operations related to media files, duplicate groups, and locations.
    """

    def get_media(self, file_path: str) -> Optional[MediaRecord]:
        """Fetches a single media record with full info."""
        norm_path = normalize_path(file_path)
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT m.file_path, m.last_modified, m.metadata_json, m.group_id,
                       0 AS lat, 0 AS lon, 0 AS alt, l.country, l.prefecture, l.city,
                       SUBSTR(m.capture_date, 1, 4) AS yr,
                       CAST(SUBSTR(m.capture_date, 6, 2) AS INTEGER) AS mo,
                       m.thumbnail_path, m.is_corrupted, m.is_in_trash,
                       m.capture_date, m.file_hash, f.vector_blob
                FROM media m
                LEFT JOIN locations l ON m.location_id = l.location_id
                LEFT JOIN media_features f ON m.file_path = f.file_path
                WHERE m.file_path = ?
            """,
                (norm_path,),
            )
            row = cursor.fetchone()
            return MediaRecord.from_full_join(row) if row else None

    def add_media_batch(self, media_list: Iterable[MediaRecord]) -> None:
        """Adds or updates multiple media records in the database."""
        with Profiler("MediaRepository.add_media_batch"):
            with self.get_connection() as conn:
                for m in media_list:
                    norm_path = normalize_path(m.file_path)
                    location_id = self._get_or_create_location(
                        conn, m.country, m.prefecture, m.city
                    )

                    conn.execute(
                        """
                        INSERT INTO media (file_path, last_modified, metadata_json, group_id, location_id,
                                          thumbnail_path, is_corrupted, is_in_trash, capture_date, file_hash, year, month)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(file_path) DO UPDATE SET
                            last_modified = excluded.last_modified,
                            metadata_json = excluded.metadata_json,
                            group_id = COALESCE(excluded.group_id, media.group_id),
                            location_id = COALESCE(excluded.location_id, media.location_id),
                            thumbnail_path = COALESCE(excluded.thumbnail_path, media.thumbnail_path),
                            is_corrupted = excluded.is_corrupted,
                            is_in_trash = excluded.is_in_trash,
                            capture_date = excluded.capture_date,
                            file_hash = COALESCE(excluded.file_hash, media.file_hash),
                            year = excluded.year,
                            month = excluded.month
                    """,
                        (
                            norm_path,
                            m.last_modified,
                            json.dumps(m.metadata),
                            m.group_id,
                            location_id,
                            m.thumbnail_path,
                            int(m.is_corrupted),
                            int(m.is_in_trash),
                            m.capture_date,
                            m.file_hash,
                            m.year,
                            m.month,
                        ),
                    )

                    if m.vector_blob is not None:
                        conn.execute(
                            """
                            INSERT INTO media_features (file_path, vector_blob) VALUES (?, ?)
                            ON CONFLICT(file_path) DO UPDATE SET vector_blob = excluded.vector_blob
                            """,
                            (norm_path, m.vector_blob),
                        )

                conn.commit()

    def _get_or_create_location(
        self,
        conn: sqlite3.Connection,
        country: Optional[str],
        pref: Optional[str],
        city: Optional[str],
    ) -> Optional[int]:
        if not any([country, pref, city]):
            return None
        conn.execute(
            "INSERT OR IGNORE INTO locations (country, prefecture, city) VALUES (?, ?, ?)",
            (country, pref, city),
        )
        cursor = conn.execute(
            'SELECT location_id FROM locations WHERE COALESCE(country, "")=? AND COALESCE(prefecture, "")=? AND COALESCE(city, "")=?',
            (country or "", pref or "", city or ""),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_media_paged(
        self,
        cluster_id: Optional[int],
        year: Optional[int],
        month: Optional[int],
        location: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_trash: bool = False,
        root_folder: Optional[str] = None,
        discovery_filter: Optional[str] = None,
        last_capture_date: Optional[str] = None,
        last_file_path: Optional[str] = None,
    ) -> list[MediaRecord]:
        """Fetches a page of media records with complex filtering and seek-based paging."""
        with Profiler(
            f"MediaRepository.get_media_paged (limit={limit}, seek={bool(last_capture_date)})"
        ):
            core_from, clauses, params = self._prepare_paged_query_base(
                cluster_id,
                year,
                month,
                location,
                include_trash,
                root_folder,
                discovery_filter,
                last_capture_date,
                last_file_path,
            )

            is_dupe_sql = self._get_duplicate_check_sql(include_trash)
            query = f"""
                SELECT m.file_path, m.metadata_json, m.group_id, m.is_in_trash, {is_dupe_sql},
                (SELECT GROUP_CONCAT(DISTINCT COALESCE(f.cluster_id, -1) || ':' || COALESCE(c.custom_name, ''))
                 FROM faces f LEFT JOIN clusters c ON f.cluster_id = c.cluster_id
                 WHERE f.file_path = m.file_path AND (c.is_ignored IS NULL OR c.is_ignored = 0)) as person_tags,
                m.thumbnail_path, dg.discovery_method, l.city, l.prefecture, l.country, m.capture_date
                {core_from}
                LEFT JOIN locations l ON m.location_id = l.location_id
                LEFT JOIN duplicate_groups dg ON m.group_id = dg.group_id
            """
            if clauses:
                query += " WHERE " + " AND ".join(clauses)

            # Sorting & Paging
            query += self._get_paged_sorting_sql(cluster_id)
            query, params = self._apply_paged_limits(
                query, params, limit, offset, last_capture_date
            )

            with self.get_connection() as conn:
                cursor = conn.execute(query, params)
                return [MediaRecord.from_paged_list(r) for r in cursor.fetchall()]

    def _prepare_paged_query_base(
        self,
        cid: Optional[int],
        year: Optional[int],
        month: Optional[int],
        loc: Optional[str],
        trash: bool,
        root: Optional[str],
        disc: Optional[str],
        last_date: Optional[str],
        last_path: Optional[str],
    ) -> tuple[str, list[str], list[Any]]:
        """Builds the FROM clause and initial filter clauses."""
        if cid is not None and cid >= 0:
            core_from = "FROM media m"
            # Use EXISTS to avoid duplicates and allow SQLite to drive the query via media indexes (capture_date)
            # This is significantly faster for large libraries and paging.
            clauses, params = (
                [
                    "EXISTS (SELECT 1 FROM faces f_ex WHERE f_ex.file_path = m.file_path AND f_ex.cluster_id = ?)"
                ],
                [cid],
            )
            self._add_basic_filters(clauses, params, year, month, loc, trash, root)
        else:
            core_from = "FROM media m"
            clauses, params = self._get_filter_clause(cid, year, month, loc, trash, root)
            if cid == -2 and disc:
                self._add_discovery_filter(clauses, params, disc)

        # Apply Seek Paging
        if last_date:
            self._add_seek_paging_clause(clauses, params, last_date, last_path)

        return core_from, clauses, params

    def _add_basic_filters(
        self,
        clauses: list[str],
        params: list[Any],
        year: Optional[int],
        month: Optional[int],
        loc: Optional[str],
        trash: bool,
        root: Optional[str],
    ) -> None:
        if not trash:
            clauses.append("m.is_in_trash = 0")
        if root:
            clauses.append("m.file_path LIKE ? ESCAPE '|'")
            params.append(self._get_folder_pattern(root))
        if year:
            clauses.append("m.year = ?")
            params.append(int(year))
        if month:
            clauses.append("CAST(m.month AS INTEGER) = ?")
            params.append(int(month))
        if loc:
            clauses.append("(l.city = ? OR l.prefecture = ? OR l.country = ?)")
            params.extend([loc, loc, loc])

    def _add_discovery_filter(self, clauses: list[str], params: list[Any], disc: str) -> None:
        if disc == "ai_local":
            clauses.append("dg.discovery_method LIKE 'ai_%'")
        else:
            clauses.append("dg.discovery_method = ?")
            params.append(disc)

    def _add_seek_paging_clause(
        self, clauses: list[str], params: list[Any], last_date: str, last_path: Optional[str]
    ) -> None:
        if last_path:
            clauses.append("(m.capture_date < ? OR (m.capture_date = ? AND m.file_path < ?))")
            params.extend([last_date, last_date, last_path])
        else:
            clauses.append("m.capture_date < ?")
            params.append(last_date)

    def _get_duplicate_check_sql(self, include_trash: bool) -> str:
        scope_where = " WHERE m2.is_in_trash = 0" if not include_trash else ""
        return f"""
            CASE WHEN m.group_id IS NOT NULL THEN
                EXISTS (
                    SELECT 1 FROM media m2
                    WHERE m2.group_id = m.group_id
                    AND m2.file_path != m.file_path
                    {scope_where.replace("WHERE", "AND")}
                )
            ELSE 0 END as is_duplicate
        """

    def _get_paged_sorting_sql(self, cluster_id: Optional[int]) -> str:
        if cluster_id == -2:
            return " ORDER BY m.group_id, m.is_in_trash ASC, m.capture_date DESC, m.file_path DESC"
        return " ORDER BY m.capture_date DESC, m.file_path DESC"

    def _apply_paged_limits(
        self, query: str, params: list[Any], limit: int, offset: int, last_date: Optional[str]
    ) -> tuple[str, list[Any]]:
        if not last_date:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        else:
            query += " LIMIT ?"
            params.append(limit)
        return query, params

    def _get_folder_pattern(self, folder_path: str) -> str:
        base = normalize_path(folder_path)
        if not base.endswith(os.sep):
            base += os.sep
        return base.replace("|", "||").replace("_", "|_").replace("%", "|%") + "%"

    def _get_filter_clause(
        self,
        cluster_id: Optional[int],
        year: Optional[int],
        month: Optional[int],
        location: Optional[str],
        include_trash: bool,
        root_folder: Optional[str],
    ) -> tuple[list[str], list[Any]]:
        clauses, params = [], []
        if not include_trash:
            clauses.append("m.is_in_trash = 0")

        if cluster_id == -3:
            clauses.append("m.is_corrupted = 1")
        else:
            clauses.append("(m.is_corrupted = 0 OR m.is_corrupted IS NULL)")

        if root_folder:
            clauses.append("m.file_path LIKE ? ESCAPE '|'")
            params.append(self._get_folder_pattern(root_folder))

        self._apply_cluster_id_filter(clauses, params, cluster_id)
        self._apply_date_loc_filters(clauses, params, year, month, location)
        return clauses, params

    def _apply_cluster_id_filter(
        self, clauses: list[str], params: list[Any], cluster_id: Optional[int]
    ) -> None:
        if cluster_id is None:
            return
        if cluster_id == -1:
            clauses.append("NOT EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path)")
        elif cluster_id == -2:
            clauses.append("m.group_id IS NOT NULL")
        elif cluster_id == -3:
            pass  # Already handled above
        else:
            clauses.append(
                "EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path AND f.cluster_id = ?)"
            )
            params.append(cluster_id)

    def _apply_date_loc_filters(
        self,
        clauses: list[str],
        params: list[Any],
        year: Optional[int],
        month: Optional[int],
        location: Optional[str],
    ) -> None:
        if year:
            clauses.append("m.year = ?")
            params.append(int(year))
        if month:
            clauses.append("CAST(m.month AS INTEGER) = ?")
            params.append(int(month))
        if location:
            clauses.append("(l.city = ? OR l.prefecture = ? OR l.country = ?)")
            params.extend([location, location, location])

    def get_duplicates(self) -> list[list[MediaRecord]]:
        with Profiler("MediaRepository.get_duplicates"):
            with self.get_connection() as conn:
                hashes = [
                    row[0]
                    for row in conn.execute(
                        "SELECT m.group_id FROM media m JOIN duplicate_groups g ON m.group_id = g.group_id WHERE m.group_id IS NOT NULL AND m.group_id != '' AND m.file_hash IS NOT NULL AND m.file_hash != '' GROUP BY m.group_id HAVING COUNT(*) > 1"
                    ).fetchall()
                ]
                all_groups = []
                for h in hashes:
                    cursor = conn.execute(
                        """
                        SELECT m.file_path, m.last_modified, m.metadata_json, m.group_id, 0, 0, 0, l.country, l.prefecture, l.city,
                               SUBSTR(m.capture_date, 1, 4), CAST(SUBSTR(m.capture_date, 6, 2) AS INTEGER),
                               m.thumbnail_path, m.is_corrupted, m.is_in_trash, m.capture_date, m.file_hash, f.vector_blob
                        FROM media m LEFT JOIN locations l ON m.location_id = l.location_id LEFT JOIN media_features f ON m.file_path = f.file_path
                        WHERE m.group_id = ?
                    """,
                        (h,),
                    )
                    group = [MediaRecord.from_full_join(r) for r in cursor.fetchall()]
                    if group:
                        all_groups.append(group)
                return all_groups

    def delete_media(self, file_path: str) -> None:
        norm_path = normalize_path(file_path)
        with self.get_connection() as conn:
            conn.execute("DELETE FROM media WHERE file_path = ?", (norm_path,))
            conn.execute("DELETE FROM media_features WHERE file_path = ?", (norm_path,))
            conn.execute("DELETE FROM faces WHERE file_path = ?", (norm_path,))
            conn.commit()

    def get_all_media_paths(self) -> list[str]:
        with self.get_connection() as conn:
            return [str(row[0]) for row in conn.execute("SELECT file_path FROM media").fetchall()]

    def get_media_paths_in_folder(self, folder_path: str) -> list[str]:
        folder_pattern = self._get_folder_pattern(folder_path)
        with self.get_connection() as conn:
            return [
                str(row[0])
                for row in conn.execute(
                    "SELECT file_path FROM media WHERE file_path LIKE ? ESCAPE '|'",
                    (folder_pattern,),
                ).fetchall()
            ]

    def get_years(
        self, cluster_id: Optional[int] = None, include_trash: bool = False
    ) -> list[YearCount]:
        with self.get_connection() as conn:
            if cluster_id is not None and cluster_id >= 0:
                query = "SELECT m.year, COUNT(m.file_path) FROM media m WHERE (m.is_corrupted = 0 OR m.is_corrupted IS NULL) AND EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path AND f.cluster_id = ?)"
                params: list[Any] = [cluster_id]
                if not include_trash:
                    query += " AND (m.is_in_trash = 0 OR m.is_in_trash IS NULL)"
                query += " GROUP BY m.year HAVING m.year IS NOT NULL ORDER BY m.year DESC"
            else:
                query = "SELECT year, COUNT(m.file_path) FROM media m"
                clauses, params = self._get_filter_clause(
                    cluster_id, None, None, None, include_trash, None
                )
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " GROUP BY year HAVING year IS NOT NULL ORDER BY year DESC"
            return [
                YearCount(year=int(row[0]), count=row[1])
                for row in conn.execute(query, params).fetchall()
            ]

    def get_months(
        self, cluster_id: Optional[int], year: int, include_trash: bool = False
    ) -> list[MonthCount]:
        with self.get_connection() as conn:
            if cluster_id is not None and cluster_id >= 0:
                query = "SELECT CAST(m.month AS INTEGER) as month_int, COUNT(m.file_path) FROM media m WHERE m.year = ? AND (m.is_corrupted = 0 OR m.is_corrupted IS NULL) AND EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path AND f.cluster_id = ?)"
                params: list[Any] = [year, cluster_id]
                if not include_trash:
                    query += " AND (m.is_in_trash = 0 OR m.is_in_trash IS NULL)"
                query += " GROUP BY month_int HAVING month_int IS NOT NULL ORDER BY month_int ASC"
            else:
                query = "SELECT CAST(month AS INTEGER) as month_int, COUNT(file_path) FROM media m"
                clauses, params = self._get_filter_clause(
                    cluster_id, year, None, None, include_trash, None
                )
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " GROUP BY month_int HAVING month_int IS NOT NULL ORDER BY month_int ASC"
            return [
                MonthCount(month=row[0], count=row[1])
                for row in conn.execute(query, params).fetchall()
            ]

    def get_locations(
        self,
        cluster_id: Optional[int],
        year: Optional[int],
        month: Optional[int],
        include_trash: bool = False,
    ) -> list[LocationCount]:
        with self.get_connection() as conn:
            if cluster_id is not None and cluster_id >= 0:
                query = "SELECT l.city, COUNT(m.file_path) FROM media m JOIN locations l ON m.location_id = l.location_id WHERE (m.is_corrupted = 0 OR m.is_corrupted IS NULL) AND EXISTS (SELECT 1 FROM faces f WHERE f.file_path = m.file_path AND f.cluster_id = ?)"
                params: list[Any] = [cluster_id]
                if year:
                    query += " AND m.year = ?"
                    params.append(int(year))
                if month:
                    query += " AND CAST(m.month AS INTEGER) = ?"
                    params.append(int(month))
                if not include_trash:
                    query += " AND (m.is_in_trash = 0 OR m.is_in_trash IS NULL)"
                query += " GROUP BY l.city HAVING l.city IS NOT NULL ORDER BY l.city ASC"
            else:
                query = "SELECT l.city, COUNT(m.file_path) FROM media m JOIN locations l ON m.location_id = l.location_id"
                clauses, params = self._get_filter_clause(
                    cluster_id, year, month, None, include_trash, None
                )
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " GROUP BY l.city HAVING l.city IS NOT NULL ORDER BY l.city ASC"
            return [
                LocationCount(city=row[0], count=row[1])
                for row in conn.execute(query, params).fetchall()
            ]

    def get_root_category_counts(self, root_folder: Optional[str] = None) -> dict[str, int]:
        with Profiler("MediaRepository.get_root_category_counts"):
            with self.get_connection() as conn:
                pattern = None
                if root_folder:
                    pattern = self._get_folder_pattern(root_folder)

                # Use a single pass with LEFT JOIN to count everything at once.
                # JOINing against a DISTINCT subquery of faces is much faster than correlated NOT EXISTS.
                q = """
                    SELECT
                        COUNT(m.file_path) as total,
                        SUM(f.file_path IS NULL) as no_faces,
                        SUM(m.group_id IS NOT NULL) as duplicates,
                        SUM(m.is_corrupted = 1) as corrupted
                    FROM media m
                    LEFT JOIN (SELECT DISTINCT file_path FROM faces) f ON m.file_path = f.file_path
                    WHERE m.is_in_trash = 0
                """
                params: list[Any] = []
                if pattern:
                    q += " AND m.file_path LIKE ? ESCAPE '|'"
                    params.append(pattern)

                row = conn.execute(q, params).fetchone()
                return {
                    "all": row[0] or 0,
                    "no_faces": row[1] or 0,
                    "duplicates": row[2] or 0,
                    "corrupted": row[3] or 0,
                }

    def get_duplicate_stats(
        self,
        include_trash: bool = False,
        root_folder: Optional[str] = None,
        discovery_filter: Optional[str] = None,
    ) -> DuplicateStats:
        clauses, params = self._get_filter_clause(-2, None, None, None, include_trash, root_folder)
        if discovery_filter:
            if discovery_filter == "ai_local":
                clauses.append("dg.discovery_method LIKE 'ai_%'")
            else:
                clauses.append("dg.discovery_method = ?")
                params.append(discovery_filter)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"SELECT COUNT(DISTINCT m.group_id), COUNT(*) FROM media m LEFT JOIN duplicate_groups dg ON m.group_id = dg.group_id {where}"
        with self.get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return DuplicateStats(group_count=row[0] or 0, file_count=row[1] or 0)

    def merge_duplicate_paths_batch(self, merges: list[tuple[str, list[str]]]) -> None:
        with Profiler(f"MediaRepository.merge_duplicate_paths_batch (groups={len(merges)})"):
            with self.get_connection() as conn:
                for target, others in merges:
                    for old in others:
                        conn.execute(
                            "UPDATE faces SET file_path = ? WHERE file_path = ?", (target, old)
                        )
                        conn.execute(
                            "UPDATE media_features SET file_path = ? WHERE file_path = ?",
                            (target, old),
                        )
                        conn.execute("DELETE FROM media WHERE file_path = ?", (old,))
                conn.commit()

    def delete_media_batch(self, paths: list[str]) -> None:
        if not paths:
            return
        with Profiler(f"MediaRepository.delete_media_batch (count={len(paths)})"):
            normalized = [(normalize_path(p),) for p in paths]
            with self.get_connection() as conn:
                conn.executemany("DELETE FROM media WHERE file_path = ?", normalized)
                conn.executemany("DELETE FROM media_features WHERE file_path = ?", normalized)
                conn.executemany("DELETE FROM faces WHERE file_path = ?", normalized)
                conn.commit()

    def release_files_from_groups(self, paths: list[str]) -> None:
        if not paths:
            return
        with Profiler(f"MediaRepository.release_files_from_groups (count={len(paths)})"):
            normalized = [(normalize_path(p),) for p in paths]
            with self.get_connection() as conn:
                conn.executemany("UPDATE media SET group_id = NULL WHERE file_path = ?", normalized)
                conn.commit()

    def clear_ai_duplicate_groups(self, folder_path: Optional[str] = None) -> None:
        with Profiler("MediaRepository.clear_ai_duplicate_groups"):
            with self.get_connection() as conn:
                # We only clear groups that were discovered by AI methods to preserve manual organization
                ai_subquery = (
                    "SELECT group_id FROM duplicate_groups WHERE discovery_method LIKE 'ai_%'"
                )
                if folder_path:
                    pattern = self._get_folder_pattern(folder_path)
                    conn.execute(
                        f"UPDATE media SET group_id = NULL WHERE file_path LIKE ? ESCAPE '|' AND group_id IN ({ai_subquery})",
                        (pattern,),
                    )
                else:
                    conn.execute(
                        f"UPDATE media SET group_id = NULL WHERE group_id IN ({ai_subquery})"
                    )
                    conn.execute("DELETE FROM duplicate_groups WHERE discovery_method LIKE 'ai_%'")
                conn.commit()

    def unify_duplicate_hashes(self, groups: list[list[MediaRecord]]) -> None:
        with Profiler(f"MediaRepository.unify_duplicate_hashes (groups={len(groups)})"):
            with self.get_connection() as conn:
                for members in groups:
                    if not members:
                        continue
                    # members is list[MediaRecord]
                    h = members[0].file_hash or f"ai_{int(time.time() * 1000)}"
                    method = members[0].discovery_method or "ai_local"

                    conn.execute(
                        "INSERT OR IGNORE INTO duplicate_groups (group_id, discovery_method) VALUES (?, ?)",
                        (h, method),
                    )
                    # Bulk update members of THIS specific group
                    updates = [(h, normalize_path(m.file_path)) for m in members]
                    conn.executemany("UPDATE media SET group_id = ? WHERE file_path = ?", updates)
                conn.commit()

    def update_salient_features_batch(self, data: list[tuple[str, bytes, bytes]]) -> None:
        if not data:
            return
        with Profiler(f"MediaRepository.update_features_batch (count={len(data)})"):
            normalized_data = [
                (normalize_path(path), v_blob, s_blob) for path, v_blob, s_blob in data
            ]
            with self.get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO media_features (file_path, vector_blob, salient_blob) VALUES (?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        vector_blob = COALESCE(excluded.vector_blob, media_features.vector_blob),
                        salient_blob = COALESCE(excluded.salient_blob, media_features.salient_blob)
                    """,
                    normalized_data,
                )
                conn.commit()

    def get_salient_feature(self, path: str) -> Optional[tuple[bytes, bytes]]:
        norm = normalize_path(path)
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT vector_blob, salient_blob FROM media_features WHERE file_path = ?", (norm,)
            ).fetchone()
            return (row[0], row[1]) if row else None
