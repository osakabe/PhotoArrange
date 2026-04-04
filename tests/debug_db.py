import os
import sys
import json
import traceback

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import Database

def debug_counts():
    db_path = "debug_db_test.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            if os.path.exists(db_path + "-wal"): os.remove(db_path + "-wal")
            if os.path.exists(db_path + "-shm"): os.remove(db_path + "-shm")
        except: pass
        
    try:
        db = Database(db_path)
        print("Database initialized successfully.")
        
        # Insert a test face
        with db.get_connection() as conn:
            conn.execute("INSERT INTO faces (file_path, vector_blob) VALUES ('test.jpg', ?)", (b'dummy',))
            conn.commit()
            print("Inserted dummy face.")
            
        print("Calling get_face_counts()...")
        counts = db.get_face_counts()
        print(f"Result: {counts}")
        
        print("Calling get_faces_by_category('unknown')...")
        faces = db.get_faces_by_category('unknown')
        print(f"Result: {len(faces)} faces found.")

        print("Testing update_face_association...")
        db.update_face_association(1, 100, is_ignored=True)
        print("Update successful.")
        
        print(f"New counts: {db.get_face_counts()}")

    except Exception as e:
        print(f"\n!!! FAILED !!!")
        print(f"Exception Type: {type(e)}")
        print(f"Exception Message: {e}")
        traceback.print_exc()
    finally:
        if os.path.exists(db_path):
            try:
                # Close any implicit connections if needed?
                pass
            except: pass

if __name__ == "__main__":
    debug_counts()
