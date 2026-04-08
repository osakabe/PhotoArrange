import logging
import os

from core.database import Database

logger = logging.getLogger("PhotoArrange")


class DatabaseAuditor:
    """
    Performs integrity checks and health monitoring for the PhotoArrange database.
    Ensures that file paths in the DB exist on disk and no orphan face records exist.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def perform_full_audit(self) -> dict[str, int]:
        """Runs all available audit checks and returns a summary."""
        results = {
            "orphaned_media": self.check_orphaned_media(),
            "orphaned_faces": self.check_orphaned_faces(),
            "missing_thumbnails": self.check_missing_thumbnails(),
            "inconsistent_clusters": self.check_cluster_consistency(),
        }
        logger.info(f"Audit Complete: {results}")
        return results

    def check_orphaned_media(self) -> int:
        """Finds media records where the file no longer exists on disk."""
        all_paths = self.db.get_all_media_paths()
        missing = [p for p in all_paths if not os.path.exists(p)]
        if missing:
            logger.warning(f"Found {len(missing)} orphaned media records in DB.")
        return len(missing)

    def check_orphaned_faces(self) -> int:
        """Finds face records that reference non-existent media files or non-existent clusters."""
        with self.db.get_connection() as conn:
            # 1. Faces with missing media
            q1 = "SELECT COUNT(*) FROM faces WHERE file_path NOT IN (SELECT file_path FROM media)"
            row1 = conn.execute(q1).fetchone()
            missing_media = int(row1[0]) if row1 else 0

            # 2. Faces with invalid clusters (excluding NULL/-1)
            q2 = "SELECT COUNT(*) FROM faces WHERE cluster_id IS NOT NULL AND cluster_id != -1 AND cluster_id NOT IN (SELECT cluster_id FROM clusters)"
            row2 = conn.execute(q2).fetchone()
            missing_clusters = int(row2[0]) if row2 else 0

        if missing_media or missing_clusters:
            logger.warning(
                f"Orphaned Faces found: {missing_media} (missing media), {missing_clusters} (invalid clusters)"
            )
        return missing_media + missing_clusters

    def check_missing_thumbnails(self) -> int:
        """Checks if referenced thumbnail files exist."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT thumbnail_path FROM media WHERE thumbnail_path IS NOT NULL AND is_in_trash = 0"
            )
            missing = [str(r[0]) for r in cursor if not os.path.exists(str(r[0]))]
        return len(missing)

    def check_cluster_consistency(self) -> int:
        """Finds clusters that have no faces associated with them."""
        with self.db.get_connection() as conn:
            q = "SELECT COUNT(*) FROM clusters WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM faces WHERE cluster_id IS NOT NULL)"
            row = conn.execute(q).fetchone()
            empty_clusters = int(row[0]) if row else 0
        return empty_clusters

    def cleanup_inconsistencies(self) -> None:
        """Surgically removes invalid records found during audit."""
        logger.info("Starting database cleanup based on audit results...")
        with self.db.get_connection() as conn:
            # Remove faces pointing to missing media
            conn.execute("DELETE FROM faces WHERE file_path NOT IN (SELECT file_path FROM media)")
            # Remove clusters with no faces (optional, but keeps DB clean)
            conn.execute(
                "DELETE FROM clusters WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM faces WHERE cluster_id IS NOT NULL)"
            )
            conn.commit()
        logger.info("Database cleanup finished.")
