import os
import sqlite3


def check_db(db_file):
    if not os.path.exists(db_file):
        print(f"{db_file} does not exist.")
        return

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print(f"\nChecking counts in {db_file}:")
    try:
        # Check if table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_groups';"
        )
        if not cursor.fetchone():
            print("Table duplicate_groups DOES NOT EXIST.")
            return

        cursor.execute("SELECT count(*) FROM duplicate_groups;")
        dg_count = cursor.fetchone()[0]
        print(f"Count in duplicate_groups: {dg_count}")

        cursor.execute("SELECT count(*) FROM media WHERE group_id IS NOT NULL;")
        media_group_count = cursor.fetchone()[0]
        print(f"Count in media with group_id: {media_group_count}")

        cursor.execute("SELECT * FROM duplicate_groups LIMIT 3;")
        dg_samples = cursor.fetchall()
        print(f"Sample duplicate_groups: {dg_samples}")

        # Look for AI groups
        cursor.execute("SELECT count(*) FROM duplicate_groups WHERE discovery_method = 'ai_local';")
        ai_group_count = cursor.fetchone()[0]
        print(f"Count of AI local groups: {ai_group_count}")

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    dbs = ["photo_app.db", "test_media_v32.db", "test_media.db", "test_verify.db"]
    for db in dbs:
        check_db(db)
