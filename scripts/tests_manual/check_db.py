import sqlite3


def check_db():
    conn = sqlite3.connect("photo_app.db")
    cursor = conn.cursor()

    print("Checking counts in photo_app.db:")
    try:
        cursor.execute("SELECT count(*) FROM duplicate_groups;")
        dg_count = cursor.fetchone()[0]
        print(f"Count in duplicate_groups: {dg_count}")

        cursor.execute("SELECT count(*) FROM media WHERE group_id IS NOT NULL;")
        media_group_count = cursor.fetchone()[0]
        print(f"Count in media with group_id: {media_group_count}")

        cursor.execute("SELECT * FROM duplicate_groups LIMIT 5;")
        dg_samples = cursor.fetchall()
        print(f"Sample duplicate_groups: {dg_samples}")

        # Check media with group_id
        cursor.execute("SELECT file_path, group_id FROM media WHERE group_id IS NOT NULL LIMIT 5;")
        media_samples = cursor.fetchall()
        print(f"Sample media with group_id: {media_samples}")

        # Look for AI groups
        cursor.execute("SELECT count(*) FROM duplicate_groups WHERE discovery_method = 'ai_local';")
        ai_group_count = cursor.fetchone()[0]
        print(f"Count of AI local groups: {ai_group_count}")

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    check_db()
