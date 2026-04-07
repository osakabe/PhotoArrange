import os
import sqlite3


def check_db():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    if not os.path.exists(db_file):
        print(f"{db_file} not found")
        return
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Detail check for {db_file} ---")

    # Check media table for groups
    cursor.execute(
        "SELECT group_id, count(*) FROM media WHERE group_id IS NOT NULL GROUP BY group_id HAVING count(*) > 1"
    )
    media_groups = cursor.fetchall()
    print(f"Duplicate media groups (group_id, count): {len(media_groups)} groups found")

    # Check duplicate_groups table
    cursor.execute("SELECT count(*) FROM duplicate_groups")
    dg_count = cursor.fetchone()[0]
    print(f"Duplicate groups in duplicate_groups table: {dg_count}")

    # Check if there are any media with group_id that are NOT in duplicate_groups
    cursor.execute(
        "SELECT count(*) FROM media WHERE group_id IS NOT NULL AND group_id NOT IN (SELECT group_id FROM duplicate_groups)"
    )
    missing_groups = cursor.fetchone()[0]
    print(f"Media with group_id NOT in duplicate_groups: {missing_groups}")

    # Run the query used in get_duplicate_stats for stats
    # No root_folder filter for now
    query = "SELECT count(*) FROM duplicate_groups dg JOIN media m ON dg.group_id = m.group_id WHERE m.is_in_trash = 0 GROUP BY dg.group_id HAVING COUNT(*) > 1"
    cursor.execute(f"SELECT COUNT(*) FROM ({query})")
    stats_count = cursor.fetchone()[0]
    print(f"Stats group count (no root filter): {stats_count}")

    conn.close()


if __name__ == "__main__":
    check_db()
