import os
import sys
import sqlite3

# Add project dir to path
sys.path.append(os.getcwd())

from core.database import Database

def verify_trash_duplicates():
    db = Database("photo_app.db")
    # 1. Find a duplicate pair
    conn = sqlite3.connect("photo_app.db")
    cursor = conn.execute("SELECT image_hash, COUNT(*) FROM media GROUP BY image_hash HAVING COUNT(*) > 1 LIMIT 1")
    row = cursor.fetchone()
    if not row:
        print("No duplicates found in DB to test with.")
        return
    
    target_hash = row[0]
    print(f"Testing with hash: {target_hash}")
    
    # 2. Mark one as trash
    cursor = conn.execute("SELECT file_path FROM media WHERE image_hash = ?", (target_hash,))
    paths = [r[0] for r in cursor.fetchall()]
    
    path_a = paths[0]
    path_b = paths[1]
    
    conn.execute("UPDATE media SET is_in_trash = 0 WHERE file_path = ?", (path_a,))
    conn.execute("UPDATE media SET is_in_trash = 1 WHERE file_path = ?", (path_b,))
    conn.commit()
    
    print(f"Set {path_a} to Main, {path_b} to Trash")
    
    # 3. Query with include_trash=False
    print("\nQuerying with include_trash=False:")
    results = db.get_media_paged(cluster_id=-2, year=None, month=None, include_trash=False)
    for r in results:
        if r['group_hash'] == target_hash:
            print(f"  Found: {r['file_path']} | is_trash: {r['is_in_trash']} | is_duplicate: {r['is_duplicate']}")
            
    # 4. Query with include_trash=True
    print("\nQuerying with include_trash=True:")
    results = db.get_media_paged(cluster_id=-2, year=None, month=None, include_trash=True)
    for r in results:
        if r['group_hash'] == target_hash:
            print(f"  Found: {r['file_path']} | is_trash: {r['is_in_trash']} | is_duplicate: {r['is_duplicate']}")

if __name__ == "__main__":
    verify_trash_duplicates()
