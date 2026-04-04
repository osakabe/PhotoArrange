import os
import sqlite3
import json
import numpy as np
from PIL import Image
from core.database import Database
from core.utils import get_app_data_dir

def setup_dummy_data(count=5000):
    db_path = os.path.join(get_app_data_dir(), "media_cache.db")
    db = Database(db_path)
    
    # Use a dummy image path
    dummy_path = os.path.abspath("dummy.jpg")
    # Create a REAL valid JPEG
    img = Image.new('RGB', (100, 100), color='white')
    img.save(dummy_path, "JPEG")
    print(f"Created valid dummy JPEG at {dummy_path}")
            
    # Add dummy media if not exists
    with db.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO media (file_path, file_hash) VALUES (?, ?)", (dummy_path, "dummy_hash"))
        
        # Clear existing faces to avoid confusion
        conn.execute("DELETE FROM faces")
        
        # Insert 5000 faces
        faces = []
        for i in range(count):
            # 512-dim embedding
            vec = np.random.rand(512).astype(np.float32).tobytes()
            bbox = json.dumps([10, 10, 80, 80])
            faces.append((dummy_path, vec, bbox))
            
        conn.executemany("INSERT INTO faces (file_path, vector_blob, bbox_json) VALUES (?, ?, ?)", faces)
        conn.commit()
        
    print(f"Successfully inserted {count} dummy faces into {db_path}")

if __name__ == "__main__":
    setup_dummy_data(5000)
