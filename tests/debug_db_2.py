import os
import sys
import json
import traceback
import sqlite3

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import Database

def debug_db():
    db_path = "debug_db_inspect.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    try:
        db = Database(db_path)
        print("Database initialized.")
        
        db.update_face_association(1, 100, is_ignored=True)
        # Should do nothing since no face with id 1 exists yet.
        
        with db.get_connection() as conn:
            conn.execute("INSERT INTO faces (file_path, vector_blob, cluster_id, is_ignored) VALUES ('test.jpg', ?, 100, 1)", (b'dummy',))
            conn.commit()
            
            cursor = conn.execute("SELECT face_id, cluster_id, is_ignored FROM faces")
            row = cursor.fetchone()
            print(f"Row in DB: {row}")
            
            print("Running Person Count query...")
            cursor = conn.execute("""
                SELECT cluster_id, COUNT(*) 
                FROM faces 
                WHERE cluster_id IS NOT NULL AND cluster_id != -1 AND is_ignored = 0
                GROUP BY cluster_id
            """)
            print(f"Person counts result: {cursor.fetchall()}")
            
            print("Running Summary Count query...")
            cursor = conn.execute("""
                SELECT 
                    SUM(CASE WHEN (cluster_id IS NULL OR cluster_id = -1) AND is_ignored = 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN is_ignored = 1 THEN 1 ELSE 0 END)
                FROM faces
            """)
            print(f"Summary counts result: {cursor.fetchone()}")

    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    debug_db()
