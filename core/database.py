import logging
import os
import sqlite3
from typing import Any, Iterable, Optional

import numpy as np

from .models import ClusterInfo, FaceCountsResult, FaceInfo, MediaRecord
from .repositories.face_repository import FaceRepository
from .repositories.media_repository import MediaRepository
from .repositories.setting_repository import SettingRepository
from .utils import Profiler, get_app_data_dir

logger = logging.getLogger("PhotoArrange")


class Database:
    """
    Facade class for backward compatibility.
    Delegates calls to specialized repositories.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            self.db_path = os.path.join(get_app_data_dir(), "media_cache.db")
        else:
            self.db_path = db_path

        self.media_repo = MediaRepository(self.db_path)
        self.face_repo = FaceRepository(self.db_path)
        self.settings_repo = SettingRepository(self.db_path)

        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_db(self) -> None:
        """Ensures schema is initialized with performance tracking."""
        with Profiler("Database.init_db"):
            with self.get_connection() as conn:
                logger.info(f"Database: Initializing schema at {self.db_path}")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS media (
                        file_path TEXT PRIMARY KEY COLLATE NOCASE,
                        last_modified REAL,
                        metadata_json TEXT,
                        group_id TEXT,
                        location_id INTEGER,
                        thumbnail_path TEXT,
                        is_corrupted INTEGER DEFAULT 0,
                        is_in_trash INTEGER DEFAULT 0,
                        capture_date TEXT,
                        file_hash TEXT COLLATE NOCASE,
                        year INTEGER,
                        month INTEGER
                    )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS faces (
                    face_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL COLLATE NOCASE,
                    vector_blob BLOB NOT NULL,
                    bbox_json TEXT,
                    cluster_id INTEGER,
                    is_ignored INTEGER DEFAULT 0,
                    frame_index INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id INTEGER PRIMARY KEY,
                    custom_name TEXT,
                    is_ignored INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS locations (
                    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    country TEXT,
                    prefecture TEXT,
                    city TEXT,
                    UNIQUE(country, prefecture, city)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS duplicate_groups (
                    group_id TEXT PRIMARY KEY,
                    discovery_method TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_features (
                    file_path TEXT PRIMARY KEY COLLATE NOCASE,
                    vector_blob BLOB,
                    salient_blob BLOB
                )
            """)

            # Speed optimizations: Add indexes for frequently queried columns
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_is_in_trash ON media(is_in_trash)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_group_id ON media(group_id) WHERE group_id IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_date_composite ON media(year, month, capture_date)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_capture_date ON media(capture_date)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_trash_corrupt ON media(is_in_trash, is_corrupted)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces(cluster_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_path ON faces(file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_ignored ON faces(is_ignored)")
            conn.commit()

    # --- Delegated Media Methods ---
    def get_media(self, file_path: str) -> Optional[MediaRecord]:
        return self.media_repo.get_media(file_path)

    def add_media_batch(self, records: Iterable[MediaRecord]) -> None:
        self.media_repo.add_media_batch(records)

    def get_media_paged(self, *args: Any, **kwargs: Any) -> list[MediaRecord]:
        return self.media_repo.get_media_paged(*args, **kwargs)

    def delete_media(self, file_path: str) -> None:
        self.media_repo.delete_media(file_path)

    def get_all_media_paths(self) -> list[str]:
        return self.media_repo.get_all_media_paths()

    def get_media_paths_in_folder(self, folder_path: str) -> list[str]:
        return self.media_repo.get_media_paths_in_folder(folder_path)

    def get_years(self, *args: Any, **kwargs: Any) -> list[int]:
        return self.media_repo.get_years(*args, **kwargs)

    def get_months(self, *args: Any, **kwargs: Any) -> list[int]:
        return self.media_repo.get_months(*args, **kwargs)

    def get_locations(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.media_repo.get_locations(*args, **kwargs)

    def get_duplicate_stats(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.media_repo.get_duplicate_stats(*args, **kwargs)

    def merge_duplicate_paths_batch(self, pairs: list[tuple[str, list[str]]]) -> None:
        self.media_repo.merge_duplicate_paths_batch(pairs)

    def delete_media_batch(self, file_paths: list[str]) -> None:
        self.media_repo.delete_media_batch(file_paths)

    def release_files_from_groups(self, file_paths: list[str]) -> None:
        self.media_repo.release_files_from_groups(file_paths)

    def clear_ai_duplicate_groups(self, folder_path: Optional[str] = None) -> None:
        self.media_repo.clear_ai_duplicate_groups(folder_path)

    def get_duplicate_groups(self) -> list[list[MediaRecord]]:
        return self.media_repo.get_duplicates()

    # --- Delegated Face Methods ---
    def remove_face_batch(self, face_ids: list[int]) -> None:
        self.face_repo.remove_face_batch(face_ids)

    def update_faces_association_batch(
        self, face_ids: list[int], person_id: Optional[int], is_ignored: bool = False
    ) -> None:
        self.face_repo.update_faces_association_batch(face_ids, person_id, is_ignored)

    def update_faces_cluster_batch(self, update_batch: list[tuple[int, int]]) -> None:
        self.face_repo.update_faces_cluster_batch(update_batch)

    def create_clusters_batch(self, cluster_ids: list[int]) -> None:
        self.face_repo.create_clusters_batch(cluster_ids)

    def unify_duplicate_hashes(self, groups: list[list[MediaRecord]]) -> None:
        self.media_repo.unify_duplicate_hashes(groups)

    def update_salient_features_batch(self, features: list[tuple[str, bytes, bytes]]) -> None:
        self.media_repo.update_salient_features_batch(features)

    def get_salient_feature(self, file_path: str) -> Optional[tuple[bytes, bytes]]:
        return self.media_repo.get_salient_feature(file_path)

    # --- Delegated Face Methods (Face Manager Context) ---
    def get_face_counts(self) -> FaceCountsResult:
        return self.face_repo.get_face_counts()

    def get_clusters(self, include_ignored: bool = False) -> list[ClusterInfo]:
        return self.face_repo.get_clusters(include_ignored)

    def upsert_cluster(
        self, cluster_id: int, name: Optional[str] = None, is_ignored: Optional[bool] = None
    ) -> None:
        self.face_repo.upsert_cluster(cluster_id, name, is_ignored)

    def create_cluster_manual(self, name: str) -> int:
        return self.face_repo.create_cluster_manual(name)

    def get_faces_by_category(self, *args: Any, **kwargs: Any) -> list[FaceInfo]:
        return self.face_repo.get_faces_by_category(*args, **kwargs)

    def update_face_cluster(self, face_id: int, cluster_id: int) -> None:
        self.face_repo.update_face_cluster(face_id, cluster_id)

    def update_face_association(
        self, face_id: int, person_id: Optional[int], is_ignored: bool = False
    ) -> None:
        self.face_repo.update_face_association(face_id, person_id, is_ignored)

    def set_cluster_ignored(self, cluster_id: int, is_ignored: bool) -> None:
        self.face_repo.set_cluster_ignored(cluster_id, is_ignored)

    def remove_face(self, face_id: int) -> None:
        self.face_repo.remove_face(face_id)

    def delete_cluster(self, cluster_id: int) -> None:
        self.face_repo.delete_cluster(cluster_id)

    def delete_empty_clusters(self) -> None:
        self.face_repo.delete_empty_clusters()

    def clear_face_data(self, folder_path: Optional[str] = None) -> None:
        self.face_repo.clear_face_data(folder_path)

    def clear_all_data(self) -> None:
        self.face_repo.clear_all_data()

    def get_ignored_vectors(self) -> list[np.ndarray]:
        return self.face_repo.get_ignored_vectors()

    def get_faces_for_file(self, file_path: str) -> list[FaceInfo]:
        return self.face_repo.get_faces_for_file(file_path)

    def clear_faces_for_file(self, file_path: str) -> None:
        self.face_repo.clear_faces_for_file(file_path)

    # --- Delegated Setting Methods ---
    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.settings_repo.get_setting(key, default)

    def save_setting(self, key: str, value: Any) -> None:
        self.settings_repo.save_setting(key, value)
