import sys
import os
import traceback
from core.database import Database
from processor.person_logic import PersonManagementWorker, PersonAction

try:
    db_path = "debug_logic.db"
    if os.path.exists(db_path): os.remove(db_path)
    db = Database(db_path)
    
    # Setup dummy data
    with db.get_connection() as conn:
        import numpy as np
        dummy_vec = np.zeros(512, dtype='float32').tobytes()
        conn.execute("INSERT INTO media (file_path) VALUES ('test.jpg')")
        conn.execute("INSERT INTO faces (file_path, vector_blob, bbox_json) VALUES ('test.jpg', ?, '[]')", (dummy_vec,))
    
    # Try registration
    params = {"face_id": 1, "name": "Test Person"}
    worker = PersonManagementWorker(db, PersonAction.REGISTER_NEW, params)
    
    def on_finished(success, msg):
        print(f"Task finished. Success: {success}, Message: {msg}")

    worker.task_finished.connect(on_finished)
    worker.run()
    
except Exception:
    traceback.print_exc()
