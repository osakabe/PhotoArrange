import sqlite3
import os
import json

def detail_check(db_file):
    if not os.path.exists(db_file):
        print(f"{db_file} not found")
        return
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Detail check for {db_file} ---")
    
    # Check media table for groups
    cursor.execute("SELECT group_id, count(*) FROM media WHERE group_id IS NOT NULL GROUP BY group_id")
    media_groups = cursor.fetchall()
    print(f"Media groups (group_id, count): {media_groups}")
    
    # Check duplicate_groups table
    cursor.execute("SELECT group_id, discovery_method FROM duplicate_groups")
    dg_rows = cursor.fetchall()
    print(f"Duplicate groups (group_id, method): {dg_rows}")
    
    # Run the query used in get_duplicate_groups
    query = '''
        SELECT m.file_path, m.group_id, dg.discovery_method
        FROM media m
        JOIN duplicate_groups dg ON m.group_id = dg.group_id
        WHERE m.group_id IN (
            SELECT group_id FROM media 
            WHERE group_id IS NOT NULL AND group_id != ''
            GROUP BY group_id HAVING COUNT(*) > 1
        )
    '''
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"Query results (file_path, group_id, method): {rows}")
    
    conn.close()

if __name__ == "__main__":
    detail_check('test_media_v32.db')
