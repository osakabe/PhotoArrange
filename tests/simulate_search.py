import os
import sqlite3

import faiss
import numpy as np


def simulate_search():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Simulating FAISS search on {db_file} ---")

    cursor.execute(
        "SELECT vector_blob, file_path FROM media_features WHERE vector_blob IS NOT NULL"
    )
    rows = cursor.fetchall()

    if len(rows) < 2:
        print("Not enough data to search.")
        return

    valid_vecs = []
    for row in rows:
        arr = np.frombuffer(row[0], dtype=np.float32)
        valid_vecs.append(arr)

    data_np = np.vstack(valid_vecs).astype("float32")
    dim = data_np.shape[1]

    index = faiss.IndexFlatL2(dim)
    index.add(data_np)

    default_threshold = 1.0
    lims, dists, indices = index.range_search(data_np, default_threshold)

    print(f"Total points searched: {len(data_np)}")
    print(f"Total pairs found (including self): {len(indices)}")

    # Exclude self matches (distance is usually 0)
    count = 0
    for i in range(len(lims) - 1):
        for j in range(lims[i], lims[i + 1]):
            neighbor_idx = indices[j]
            if i < neighbor_idx:
                count += 1
                if count < 5:
                    print(f"Match found: {i} and {neighbor_idx}, distance: {dists[j]}")

    print(f"Duplicate pairs (excluding self): {count}")

    conn.close()


if __name__ == "__main__":
    simulate_search()
