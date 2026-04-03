import sqlite3
import os
import numpy as np
import faiss
import json
import hashlib

def simulate_full_update():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # 1. Fetch data
    cursor.execute("SELECT m.file_path, f.vector_blob FROM media m JOIN media_features f ON m.file_path = f.file_path WHERE f.vector_blob IS NOT NULL")
    rows = cursor.fetchall()
    if not rows: return
    
    paths = [r[0] for r in rows]
    vecs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    data_np = np.vstack(vecs).astype('float32')
    
    # 2. FAISS Grouping
    index = faiss.IndexFlatL2(data_np.shape[1])
    index.add(data_np)
    lims, dists, indices = index.range_search(data_np, 1.0)
    
    parent = {p: p for p in paths}
    def find(i):
        if parent[i] == i: return i
        parent[i] = find(parent[i])
        return parent[i]
    def union(i, j):
        root_i, root_j = find(i), find(j)
        if root_i != root_j: parent[root_j] = root_i

    for i in range(len(lims) - 1):
        for j in range(lims[i], lims[i+1]):
            neighbor_idx = indices[j]
            if i < neighbor_idx:
                union(paths[i], paths[neighbor_idx])
                
    groups = {}
    for p in paths:
        root = find(p)
        if root not in groups: groups[root] = []
        groups[root].append(p)
    
    final_groups = [g for g in groups.values() if len(g) > 1]
    print(f"FAISS found {len(final_groups)} groups.")
    
    # 3. DB Update Logic (Simulation)
    update_list = []
    for g in final_groups:
        primary_id = "sim:" + hashlib.md5(g[0].encode()).hexdigest()[:16]
        discovery_method = "ai_local"
        for p in g:
            # We must use EXACTLY what's in the DB
            update_list.append((primary_id, discovery_method, p))
            
    # 4. Try the update for real
    print(f"Attempting to update {len(update_list)} records in 'media' table...")
    count = 0
    for gid, method, path in update_list:
        cursor.execute("UPDATE media SET group_id = ? WHERE file_path = ?", (gid, path))
        if cursor.rowcount > 0:
            count += 1
            cursor.execute("INSERT OR REPLACE INTO duplicate_groups (group_id, discovery_method) VALUES (?, ?)", (gid, method))
        else:
            # Check why it failed
            cursor.execute("SELECT count(*) FROM media WHERE file_path = ?", (path,))
            exists = cursor.fetchone()[0]
            if not exists:
                print(f"Path MISSING in media table: {path}")
    
    conn.commit()
    print(f"Successfully updated {count} out of {len(update_list)} records.")
    
    conn.close()

if __name__ == "__main__":
    simulate_full_update()
