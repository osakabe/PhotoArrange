import hashlib
import os
import sqlite3

import faiss
import numpy as np


def fix_grouping_logic():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print("--- Recalculating Grouping (Zero Base) ---")

    # 1. Clear all potentially broken group data
    cursor.execute("UPDATE media SET group_id = NULL")
    cursor.execute("DELETE FROM duplicate_groups")

    # 2. Extract DINOv2 vectors
    cursor.execute(
        "SELECT m.file_path, f.vector_blob FROM media m JOIN media_features f ON m.file_path = f.file_path WHERE f.vector_blob IS NOT NULL"
    )
    rows = cursor.fetchall()
    if not rows:
        print("No vectors found.")
        return

    paths = [r[0] for r in rows]
    vecs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    data_np = np.vstack(vecs).astype("float32")

    # 3. FAISS Cluster
    index = faiss.IndexFlatL2(data_np.shape[1])
    index.add(data_np)
    lims, dists, indices = index.range_search(data_np, 1.0)

    parent = {p: p for p in paths}

    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_j] = root_i

    for i in range(len(lims) - 1):
        for j in range(lims[i], lims[i + 1]):
            neighbor_idx = indices[j]
            if i < neighbor_idx:
                union(paths[i], paths[neighbor_idx])

    groups = {}
    for p in paths:
        root = find(p)
        if root not in groups:
            groups[root] = []
        groups[root].append(p)

    final_groups = [g for g in groups.values() if len(g) > 1]
    print(f"FAISS identified {len(final_groups)} valid duplicate groups.")

    # 4. Correct Database Persistence
    # Use explicit transaction for speed and safety
    try:
        total_updated = 0
        for g in final_groups:
            # Generate a stable group ID
            primary_id = "sim:" + hashlib.md5(g[0].encode()).hexdigest()[:16]
            discovery_method = "ai_local"

            # Step A: Register the group
            cursor.execute(
                "INSERT INTO duplicate_groups (group_id, discovery_method) VALUES (?, ?)",
                (primary_id, discovery_method),
            )

            # Step B: Tag all members in media table
            for p in g:
                cursor.execute("UPDATE media SET group_id = ? WHERE file_path = ?", (primary_id, p))
                if cursor.rowcount > 0:
                    total_updated += 1
                else:
                    # Path normalization mismatch debug
                    print(f"Warning: Failed to update path {p}")

        conn.commit()
        print(f"Update complete: {total_updated} media items tagged.")

    except Exception as e:
        conn.rollback()
        print(f"Error during DB update: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    fix_grouping_logic()
