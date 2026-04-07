import os
import sqlite3


def check_group_persistence():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print(f"--- Checking group persistence in {db_file} ---")

    # Check if group_id exists in media table
    cursor.execute("SELECT count(*) FROM media WHERE group_id IS NOT NULL AND group_id != ''")
    count = cursor.fetchone()[0]
    print(f"Total rows in 'media' with group_id: {count}")

    # Check duplicate_groups table
    cursor.execute("SELECT count(*) FROM duplicate_groups")
    dg_count = cursor.fetchone()[0]
    print(f"Total rows in 'duplicate_groups' table: {dg_count}")

    # Sample a few group_ids
    cursor.execute("SELECT group_id, file_path FROM media WHERE group_id IS NOT NULL LIMIT 5")
    print(f"Sample media group associations: {cursor.fetchall()}")

    conn.close()


if __name__ == "__main__":
    check_group_persistence()
