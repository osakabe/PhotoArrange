import sqlite3
import os

def test_project_root_stats():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    root_folder = os.getcwd()
    include_trash = False
    
    trash_cond = "is_in_trash = 0" if not include_trash else "1=1"
    params = []
    
    subquery = f"SELECT group_id FROM media WHERE {trash_cond} GROUP BY group_id HAVING COUNT(*) > 1"
    query = f"SELECT dg.group_id, dg.discovery_method FROM duplicate_groups dg WHERE dg.group_id IN ({subquery})"
    
    if root_folder:
        norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
        pattern = norm_root.replace('[', '[[]').replace('%', '[%]') + '%'
        query += f" AND EXISTS (SELECT 1 FROM media m_ex WHERE m_ex.group_id = dg.group_id AND m_ex.file_path LIKE ? COLLATE NOCASE AND m_ex.{trash_cond})"
        params.append(pattern)
        
    print(f"Norm root: {norm_root if root_folder else 'None'}")
    cursor.execute(query, params)
    rows = cursor.fetchall()
    print(f"Stats rows count: {len(rows)}")
    
    conn.close()

if __name__ == "__main__":
    test_project_root_stats()
