import os
import sqlite3


def thorough_db_audit():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    if not os.path.exists(db_file):
        print("Error: Database not found.")
        return

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print(f"--- Thorough Audit: {db_file} ---")

    # 1. Check media table for any group_id
    cursor.execute("SELECT count(*) FROM media WHERE group_id IS NOT NULL")
    total_with_gid = cursor.fetchone()[0]
    print(f"Total media with group_id: {total_with_gid}")

    # 2. Check duplicate_groups table entries
    cursor.execute("SELECT count(*) FROM duplicate_groups")
    total_dg = cursor.fetchone()[0]
    print(f"Total entries in duplicate_groups: {total_dg}")

    # 3. Check for Orphaned group_ids (integrity check)
    # mediaにgroup_idがあるが、duplicate_groupsに存在しないもの
    cursor.execute("""
        SELECT count(*) FROM media 
        WHERE group_id IS NOT NULL 
        AND group_id NOT IN (SELECT group_id FROM duplicate_groups)
    """)
    orphans = cursor.fetchone()[0]
    print(f"Orphaned group_ids in media (not in duplicate_groups): {orphans}")

    # 4. Check for 'Ghost' groups (integrity check)
    # duplicate_groupsに存在するが、mediaに紐づくファイルが1つ以下しかないもの
    cursor.execute("""
        SELECT dg.group_id, count(m.file_path) as c
        FROM duplicate_groups dg
        LEFT JOIN media m ON dg.group_id = m.group_id
        GROUP BY dg.group_id
        HAVING c < 2
    """)
    ghost_groups = cursor.fetchall()
    print(f"Ghost groups (less than 2 media items): {len(ghost_groups)}")
    if ghost_groups:
        print(f"Sample ghost groups: {ghost_groups[:3]}")

    # 5. Discovery Method distribution
    cursor.execute(
        "SELECT discovery_method, count(*) FROM duplicate_groups GROUP BY discovery_method"
    )
    print(f"Discovery methods distribution: {cursor.fetchall()}")

    # 6. Check for NULL/Empty group_ids in duplicate_groups
    cursor.execute("SELECT count(*) FROM duplicate_groups WHERE group_id IS NULL OR group_id = ''")
    invalid_ids = cursor.fetchone()[0]
    print(f"Invalid (NULL/Empty) group_ids in duplicate_groups: {invalid_ids}")

    conn.close()


if __name__ == "__main__":
    thorough_db_audit()
