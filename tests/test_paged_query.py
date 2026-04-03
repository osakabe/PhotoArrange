import sqlite3
import os

def test_paged_query():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # UI Defaults
    include_trash = False
    root_folder = None
    discovery_filter = None
    cluster_id = -2 # Duplicates
    
    trash_cond = "m.is_in_trash = 0" if not include_trash else "1=1"
    
    # Simulating get_media_paged logic
    query = f"""
        SELECT m.file_path, m.group_id
        FROM media m
        WHERE {trash_cond}
    """
    if cluster_id == -2:
        query += " AND m.group_id IN (SELECT group_id FROM media GROUP BY group_id HAVING COUNT(*) > 1)"
        
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"Paged query returned {len(rows)} items.")
    if rows:
        print(f"Sample item: {rows[0]}")
        
    conn.close()

if __name__ == "__main__":
    test_paged_query()
