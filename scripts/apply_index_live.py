import os
import sqlite3
import sys


def get_app_data_dir() -> str:
    # Match the logic in utils.py
    base_dir = os.environ.get("LOCALAPPDATA", "")
    if not base_dir:
        base_dir = os.path.expanduser("~")
    app_dir = os.path.join(base_dir, "PhotoArrange")
    return app_dir


def apply_index_live():
    db_path = os.path.join(get_app_data_dir(), "media_cache.db")
    print(f"Connecting to live database at: {db_path}")

    if not os.path.exists(db_path):
        print("CRITICAL: Database file not found where the app expects it.")
        # Try local path as fallback
        db_path = "media_cache.db"
        if not os.path.exists(db_path):
            sys.exit(1)
        print(f"Using local fallback: {db_path}")

    try:
        # Use a timeout so we don't hang if the app has a writer lock
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        # Check if already exists
        cursor = conn.execute("PRAGMA index_list(faces)")
        existing = [row[1] for row in cursor.fetchall()]

        if "idx_faces_file_path" not in existing:
            print("Action: Creating index idx_faces_file_path...")
            conn.execute("CREATE INDEX idx_faces_file_path ON faces(file_path)")
            print("Success: Index created.")
        else:
            print("Info: Index already exists.")

        conn.commit()
        conn.close()
        print("Result: Live database optimized.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    apply_index_live()
