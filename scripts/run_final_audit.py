import logging
import os
import sys

# Add project root to sys.path
sys.path.append(os.getcwd())

from core.audit import DatabaseAuditor
from core.database import Database
from core.utils import get_app_data_dir


def run_audit():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("FinalAudit")

    db_path = os.path.join(get_app_data_dir(), "media_cache.db")
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    print("--- Starting Final Database Audit ---")
    print(f"DB Path: {db_path}")

    db = Database(db_path)
    auditor = DatabaseAuditor(db)

    results = auditor.perform_full_audit()

    print("\nAudit Results Summary:")
    print(f"- Orphaned Media: {results.get('orphaned_media', 0)}")
    print(f"- Orphaned Faces: {results.get('orphaned_faces', 0)}")
    print(f"- Missing Thumbnails: {results.get('missing_thumbnails', 0)}")
    print(f"- Inconsistent Clusters: {results.get('inconsistent_clusters', 0)}")

    total_issues = sum(results.values())
    if total_issues > 0:
        print(f"\n[WARNING] Found {total_issues} inconsistencies.")
        # Optional: auditor.cleanup()
    else:
        print("\n[SUCCESS] No inconsistencies found. Database is healthy.")


if __name__ == "__main__":
    run_audit()
