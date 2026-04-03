import sqlite3
import os

def test_no_root_stats():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    root_folder = None
    include_trash = False
    
    trash_cond = "is_in_trash = 0" if not include_trash else "1=1"
    params = []
    
    # 1. Identify groups that are duplicates globally
    subquery = f"SELECT group_id FROM media WHERE {trash_cond} GROUP BY group_id HAVING COUNT(*) > 1"
    
    # 2. Select these groups
    query = f"SELECT dg.group_id, dg.discovery_method FROM duplicate_groups dg WHERE dg.group_id IN ({subquery})"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    print(f"Stats rows count (no root): {len(rows)}")
    
    conn.close()

if __name__ == "__main__":
    test_no_root_stats()
