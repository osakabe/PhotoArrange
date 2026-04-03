import sqlite3
import os
import json
import numpy as np
import faiss

def diagnose_grouping():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    if not os.path.exists(db_file):
        print(f"Database not found at {db_file}")
        return

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    print(f"--- Diagnosing Grouping for {db_file} ---")

    # 1. Extract all vectors from DB
    cursor.execute("SELECT file_path, vector_blob FROM media_features WHERE vector_blob IS NOT NULL")
    rows = cursor.fetchall()
    
    if not rows:
        print("No vectors found in media_features.")
        return

    paths = []
    vecs = []
    for p, b in rows:
        paths.append(p)
        vecs.append(np.frombuffer(b, dtype=np.float32))
    
    data_np = np.vstack(vecs).astype('float32')
    dim = data_np.shape[1]
    
    # 2. Perform FAISS Search (Same as DuplicateManager)
    index = faiss.IndexFlatL2(dim)
    index.add(data_np)
    
    threshold = 1.0 # Specification v3.2.0
    lims, dists, indices = index.range_search(data_np, threshold)
    
    # 3. Build Simulated Groups using DSU
    parent = {p: p for p in paths}
    def find(i):
        if parent[i] == i: return i
        parent[i] = find(parent[i])
        return parent[i]
    def union(i, j):
        root_i, root_j = find(i), find(j)
        if root_i != root_j: parent[root_j] = root_i

    pair_count = 0
    for i in range(len(lims) - 1):
        for j in range(lims[i], lims[i+1]):
            neighbor_idx = indices[j]
            if i < neighbor_idx:
                union(paths[i], paths[neighbor_idx])
                pair_count += 1
    
    sim_groups = {}
    for p in paths:
        root = find(p)
        if root not in sim_groups: sim_groups[root] = []
        sim_groups[root].append(p)
    
    actual_sim_groups = [g for g in sim_groups.values() if len(g) > 1]
    
    print(f"FAISS found {pair_count} pairs.")
    print(f"FAISS would create {len(actual_sim_groups)} duplicate groups.")

    # 4. Compare with current DB state
    cursor.execute("SELECT file_path, group_id FROM media WHERE group_id IS NOT NULL")
    db_media_groups = cursor.fetchall()
    
    db_groups = {}
    for p, g in db_media_groups:
        if g not in db_groups: db_groups[g] = []
        db_groups[g].append(p)
    
    print(f"Current DB has {len(db_groups)} groups in 'media' table.")

    # 5. Identify Discrepancies
    missing_in_db = 0
    sample_missing = []
    for sg in actual_sim_groups:
        # Check if any member of this simulated group has a group_id in DB
        found_in_db = False
        for p in sg:
            # Check for path normalization issues here
            if any(p == db_p for db_p, db_g in db_media_groups):
                found_in_db = True
                break
        if not found_in_db:
            missing_in_db += 1
            if len(sample_missing) < 3:
                sample_missing.append(sg)

    print(f"Groups found by FAISS but MISSING in DB: {missing_in_db}")
    if sample_missing:
        print(f"Sample missing groups: {sample_missing[0]}")

    # 6. Check for Normalization Mismatches
    # Compare paths in media_features vs media table
    cursor.execute("SELECT file_path FROM media LIMIT 5")
    media_paths = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT file_path FROM media_features LIMIT 5")
    feature_paths = [r[0] for r in cursor.fetchall()]
    
    print(f"\nPath Normalization Check:")
    print(f"Media Table Path Sample: {media_paths[0] if media_paths else 'N/A'}")
    print(f"Features Table Path Sample: {feature_paths[0] if feature_paths else 'N/A'}")

    conn.close()

if __name__ == "__main__":
    diagnose_grouping()
