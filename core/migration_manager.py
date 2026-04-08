import logging
import sqlite3

from .utils import Profiler

logger = logging.getLogger("PhotoArrange")


class DatabaseMigrationManager:
    """
    Handles database schema updates, denormalization, and index optimization.
    Decoupled from the main Database class to ensure architectural integrity.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def run_migrations(self) -> None:
        """Standardizes schema and performs lightweight updates."""
        with Profiler("MigrationManager.run_migrations"):
            with self._get_connection() as conn:
                # 1. Standardize 'Unknown' cluster_id
                conn.execute("UPDATE faces SET cluster_id = -1 WHERE cluster_id IS NULL")

                # 2. Add capture_date column if missing (Safe ALTER)
                try:
                    conn.execute("ALTER TABLE faces ADD COLUMN capture_date TEXT")
                    logger.info("MigrationManager: Added capture_date column to faces table.")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # 3. Standardize Collation (NOCASE) for joins
                self._standardize_collation(conn)

                # 4. Create Performance Indices
                self._create_indices(conn)
                conn.commit()

    def _create_indices(self, conn: sqlite3.Connection) -> None:
        """Ensures all performance-critical indices exist. Consolidated for efficiency."""
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_media_is_in_trash ON media(is_in_trash)",
            "CREATE INDEX IF NOT EXISTS idx_media_group_id ON media(group_id) WHERE group_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_media_date_composite ON media(year, month, capture_date)",
            "CREATE INDEX IF NOT EXISTS idx_media_capture_date ON media(capture_date)",
            "CREATE INDEX IF NOT EXISTS idx_media_seek_paging ON media(capture_date DESC, file_path DESC)",
            "CREATE INDEX IF NOT EXISTS idx_media_trash_corrupt ON media(is_in_trash, is_corrupted)",
            "CREATE INDEX IF NOT EXISTS idx_faces_path ON faces(file_path COLLATE NOCASE)",
            "CREATE INDEX IF NOT EXISTS idx_faces_explosive_sort ON faces(is_ignored, cluster_id, capture_date DESC)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clusters_name ON clusters(custom_name) WHERE custom_name IS NOT NULL AND custom_name != ''",
        ]
        for sql in indices:
            conn.execute(sql)

    def _standardize_collation(self, conn: sqlite3.Connection) -> None:
        """
        Recreates tables with COLLATE NOCASE if they were created with binary collation.
        This is necessary for high-performance JOINs between media and faces.
        """
        with Profiler("MigrationManager._standardize_collation"):
            # Check 'faces' table collation
            res = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='faces'"
            ).fetchone()
            if res and "COLLATE NOCASE" not in res[0]:
                logger.info("MigrationManager: Recreating 'faces' table with COLLATE NOCASE...")
                conn.execute("ALTER TABLE faces RENAME TO faces_old")
                conn.execute("""
                    CREATE TABLE faces (
                        face_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path TEXT NOT NULL COLLATE NOCASE,
                        vector_blob BLOB NOT NULL,
                        bbox_json TEXT,
                        cluster_id INTEGER DEFAULT -1,
                        is_ignored INTEGER DEFAULT 0,
                        frame_index INTEGER DEFAULT 0,
                        capture_date TEXT
                    )
                """)
                conn.execute("""
                    INSERT INTO faces (face_id, file_path, vector_blob, bbox_json, cluster_id, is_ignored, frame_index, capture_date)
                    SELECT face_id, file_path, vector_blob, bbox_json, cluster_id, is_ignored, frame_index, capture_date FROM faces_old
                """)
                conn.execute("DROP TABLE faces_old")

            # Check 'duplicate_groups' table collation
            res = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='duplicate_groups'"
            ).fetchone()
            if res and "group_id TEXT PRIMARY KEY COLLATE NOCASE" not in res[0]:
                logger.info("MigrationManager: Recreating 'duplicate_groups' table with COLLATE NOCASE...")
                conn.execute("ALTER TABLE duplicate_groups RENAME TO dg_old")
                conn.execute("""
                    CREATE TABLE duplicate_groups (
                        group_id TEXT PRIMARY KEY COLLATE NOCASE,
                        primary_file_path TEXT,
                        discovery_method TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    INSERT INTO duplicate_groups (group_id, primary_file_path, discovery_method, updated_at)
                    SELECT group_id, primary_file_path, discovery_method, updated_at FROM dg_old
                """)
                conn.execute("DROP TABLE dg_old")

    def sync_capture_dates(self) -> None:
        """Heavy data denormalization. Should be run in background."""
        with Profiler("MigrationManager.sync_capture_dates"):
            with self._get_connection() as conn:
                logger.info("MigrationManager: Starting capture_date synchronization...")
                conn.execute("""
                    UPDATE faces
                    SET capture_date = (SELECT m.capture_date FROM media m WHERE m.file_path = faces.file_path)
                    WHERE capture_date IS NULL
                """)
                conn.commit()
                logger.info("MigrationManager: Background synchronization complete.")
