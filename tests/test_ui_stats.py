import sqlite3
import os

def test_ui_stats():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # UI Defaults
    include_trash = False
    root_folder = None # Assuming the user hasn't selected a folder that filters it out
    discovery_filter = None
    
    trash_cond = "is_in_trash = 0" if not include_trash else "1=1"
    
    # 1. Identify groups that are duplicates globally
    subquery = f"SELECT group_id FROM media WHERE {trash_cond} GROUP BY group_id HAVING COUNT(*) > 1"
    
    # 2. Select these groups
    query = f"SELECT dg.group_id, dg.discovery_method FROM duplicate_groups dg WHERE dg.group_id IN ({subquery})"
    
    # 3. Apply discovery method filter
    if discovery_filter:
        if discovery_filter == 'ai_local':
            query = f"SELECT group_id, discovery_method FROM ({query}) WHERE discovery_method LIKE 'ai_%'"
        else:
            query = f"SELECT group_id, discovery_method FROM ({query}) WHERE discovery_method = ?"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"Stats rows count: {len(rows)}")
    
    if rows:
        ids = [r[0] for r in rows]
        f_query = f"SELECT COUNT(*) FROM media WHERE group_id IN ({','.join(['?']*len(ids))}) AND {trash_cond}"
        cursor.execute(f_query, ids)
        total_files = cursor.fetchone()[0]
        print(f"Total files: {total_files}")
        
    conn.close()

if __name__ == "__main__":
    test_ui_stats()
