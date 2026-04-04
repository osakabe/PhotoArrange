import os
import sqlite3
import json
import numpy as np
from PIL import Image
from core.database import Database
from core.utils import get_app_data_dir

def setup_qa_data(db_filename="qa_audit_faces.db", count=5000):
    db_path = os.path.abspath("qa_formal_audit.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    db = Database(db_path)
    
    # Create a REAL valid dummy JPEG
    dummy_path = os.path.abspath("dummy_qa.jpg")
    if not os.path.exists(dummy_path):
        img = Image.new('RGB', (200, 200), color=(70, 70, 70))
        img.save(dummy_path, "JPEG")
        print(f"Created dummy JPEG at {dummy_path}")
            
    with db.get_connection() as conn:
        # Generate several dummy media entries with different dates
        dates = ["2024:04:01 10:00:00", "2024:04:02 11:00:00", "2024:04:03 12:00:00"]
        for i, d in enumerate(dates):
            path = dummy_path if i == 0 else os.path.abspath(f"dummy_qa_{i}.jpg")
            if not os.path.exists(path):
                img = Image.new('RGB', (200, 200), color=(70 + i*20, 70, 70))
                img.save(path, "JPEG")
            conn.execute("INSERT OR REPLACE INTO media (file_path, file_hash, capture_date, is_corrupted) VALUES (?, ?, ?, 0)", 
                         (path, f"hash_{i}", d))
        
        # Insert 5000 faces spread across many dates
        print(f"Generating {count} face records...")
        faces = []
        for i in range(count):
            # Create a unique date for every 10 items to ensure many headers
            day = (i // 10) % 28 + 1
            month = (i // 280) % 12 + 1
            date_str = f"2024:{month:02d}:{day:02d} 10:00:00"
            
            # Ensure media exists for this date
            path = os.path.abspath(f"dummy_qa_{i//10}.jpg")
            if not os.path.exists(path):
                img = Image.new('RGB', (200, 200), color=(70, 70, 70))
                img.save(path, "JPEG")
                conn.execute("INSERT OR REPLACE INTO media (file_path, file_hash, capture_date, is_corrupted) VALUES (?, ?, ?, 0)", 
                             (path, f"hash_{i//10}", date_str))
            
            # 512-dim random embedding
            vec = np.random.rand(512).astype(np.float32).tobytes()
            bbox = json.dumps([10, 10, 150, 150])
            faces.append((path, vec, bbox, -1, 0)) # unknown, not ignored
            
        conn.executemany("INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored) VALUES (?, ?, ?, ?, ?)", faces)
        conn.commit()
        
    print(f"Successfully created {db_path} with {count} faces across {len(dates)} dates.")
    return db_path

if __name__ == "__main__":
    setup_qa_data()
