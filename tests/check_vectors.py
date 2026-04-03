import sqlite3
import os
import numpy as np

def check_vectors():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Vector quality check for {db_file} ---")
    
    cursor.execute("SELECT vector_blob FROM media_features WHERE vector_blob IS NOT NULL LIMIT 10")
    rows = cursor.fetchall()
    
    norms = []
    for row in rows:
        arr = np.frombuffer(row[0], dtype=np.float32)
        norm = np.linalg.norm(arr)
        norms.append(norm)
    
    print(f"Sample norms: {norms}")
    print(f"Mean norm: {np.mean(norms)}")
    
    conn.close()

if __name__ == "__main__":
    check_vectors()
