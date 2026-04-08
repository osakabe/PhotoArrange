import os
import sqlite3


def sample_db():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Samples for {db_file} ---")

    # Check media table for any group_id
    cursor.execute("SELECT group_id, file_path FROM media WHERE group_id IS NOT NULL LIMIT 5")
    print(f"Sample media with group_id: {cursor.fetchall()}")

    # Check all counts of group_id in media
    cursor.execute(
        "SELECT group_id, count(*) as c FROM media GROUP BY group_id ORDER BY c DESC LIMIT 10"
    )
    print(f"Top 10 group_id counts in media: {cursor.fetchall()}")

    # Check duplicate_groups
    cursor.execute("SELECT * FROM duplicate_groups")
    print(f"Entries in duplicate_groups: {cursor.fetchall()}")

    conn.close()


if __name__ == "__main__":
    sample_db()
