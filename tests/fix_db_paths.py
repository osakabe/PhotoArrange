import sqlite3
import os

def migrate_paths():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    print("Normalizing all paths in database...")
    
    # 1. Fetch all media
    cursor.execute("SELECT file_path FROM media")
    rows = cursor.fetchall()
    
    updates = []
    for (old_path,) in rows:
        new_path = os.path.normcase(os.path.abspath(old_path))
        if old_path != new_path:
            updates.append((new_path, old_path))
    
    if updates:
        print(f"Updating {len(updates)} paths in media table...")
        # Note: file_path is Primary Key, so we must be careful. 
        # Using a temporary table to avoid PK conflicts during update.
        cursor.execute("CREATE TABLE media_backup AS SELECT * FROM media")
        cursor.execute("DELETE FROM media")
        
        cursor.execute("SELECT * FROM media_backup")
        columns = [description[0] for description in cursor.description]
        col_names = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        
        old_data = cursor.fetchall()
        new_data = []
        for row in old_data:
            row_list = list(row)
            row_list[0] = os.path.normcase(os.path.abspath(row_list[0]))
            new_data.append(tuple(row_list))
            
        cursor.executemany(f"INSERT OR REPLACE INTO media ({col_names}) VALUES ({placeholders})", new_data)
        cursor.execute("DROP TABLE media_backup")
        
        # Similarly for media_features
        cursor.execute("SELECT file_path FROM media_features")
        f_rows = cursor.fetchall()
        for (old_f,) in f_rows:
            new_f = os.path.normcase(os.path.abspath(old_f))
            if old_f != new_f:
                cursor.execute("UPDATE media_features SET file_path = ? WHERE file_path = ?", (new_f, old_f))

        conn.commit()
        print("Path normalization complete.")
    else:
        print("All paths already normalized.")
        
    conn.close()

if __name__ == "__main__":
    migrate_paths()
