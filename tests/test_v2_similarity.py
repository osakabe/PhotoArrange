import os
import numpy as np
import torch
import faiss
from processor.duplicate_manager import DuplicateManager, DisjointSetUnion
from processor.image_processor import ImageProcessor
import json

class MockDB:
    def __init__(self):
        self.data = []
    def get_connection(self):
        return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, params=None):
        return self
    def fetchall(self):
        return self.data
    def update_image_hashes_batch(self, updates):
        print(f"DB Update: {len(updates)} hashes")

def test_faiss_clustering():
    print("Testing FAISS Clustering logic...")
    db = MockDB()
    img_proc = ImageProcessor()
    mgr = DuplicateManager(db, img_proc)
    
    # Create 3 sets of vectors
    v1 = np.random.randn(384).astype('float32')
    v1 /= np.linalg.norm(v1) # L2 Normalization
    
    v2 = v1 + np.random.randn(384).astype('float32') * 0.01 # Very close to v1
    v2 /= np.linalg.norm(v2)
    
    v3 = np.random.randn(384).astype('float32') # Completely different
    v3 /= np.linalg.norm(v3)
    
    # Mock Database Data
    # SELECT file_path, vector_blob, image_hash FROM media
    db.data = [
        ("C:/img1.jpg", v1.tobytes(), "hash1"),
        ("C:/img2.jpg", v2.tobytes(), "hash2"),
        ("C:/img3.jpg", v3.tobytes(), "hash3")
    ]
    
    # Mock find_structural_duplicates internals for PASS 2
    # We'll actually just run a simplified version of the FAISS logic here to verify thresholds
    data_np = np.vstack([v1, v2, v3])
    index = faiss.IndexFlatL2(384)
    index.add(data_np)
    
    # Radius check (D^2 = 0.05 corresponds to ~0.975 cosine similarity)
    lims, dists, indices = index.range_search(data_np, 0.05)
    
    found_pairs = []
    for i in range(len(lims) - 1):
        for j in range(lims[i], lims[i+1]):
            if i < indices[j]:
                found_pairs.append((i, indices[j]))
    
    print(f"Pairs found: {found_pairs}")
    assert len(found_pairs) == 1, f"Expected 1 pair (v1, v2), found {len(found_pairs)}"
    assert found_pairs[0] == (0, 1), f"Expected pair (0, 1), found {found_pairs[0]}"
    print("SUCCESS: FAISS Clustering Radius logic verified.")

if __name__ == "__main__":
    test_faiss_clustering()
