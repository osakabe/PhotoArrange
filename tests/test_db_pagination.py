import os
import sqlite3
import json
import logging
import time
from core.database import Database

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestDB")

def test_pagination():
    test_db_path = "test_pagination_v2.db"
    
    # Robust cleanup
    for ext in ["", "-wal", "-shm"]:
        f = test_db_path + ext
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception as e:
                logger.warning(f"Could not remove {f}: {e}")
    
    db = Database(test_db_path)
    
    try:
        # 1. Insert dummy data
        logger.info("Inserting dummy data...")
        with db.get_connection() as conn:
            # Unknown: 50 items
            cursor = conn.cursor()
            for i in range(50):
                cursor.execute("INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored) VALUES (?, ?, ?, ?, ?)",
                             (f"unknown_{i}.jpg", b"fake_vector", "[]", -1, 0))
            # Person 1: 30 items
            for i in range(30):
                cursor.execute("INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored) VALUES (?, ?, ?, ?, ?)",
                             (f"person1_{i}.jpg", b"fake_vector", "[]", 1, 0))
            # Ignored: 20 items
            for i in range(20):
                cursor.execute("INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored) VALUES (?, ?, ?, ?, ?)",
                             (f"ignored_{i}.jpg", b"fake_vector", "[]", 2, 1))
            
            # Add a cluster entry for Person 1
            cursor.execute("INSERT INTO clusters (cluster_id, custom_name) VALUES (?, ?)", (1, "Person One"))
            conn.commit()

        # 2. Verify Counts
        counts = db.get_face_counts()
        logger.info(f"Counts: {counts}")
        assert counts["unknown"] == 50, f"Expected 50 unknown, got {counts['unknown']}"
        assert counts["ignored"] == 20, f"Expected 20 ignored, got {counts['ignored']}"
        assert counts["total_persons"] == 80, f"Expected 80 total non-ignored, got {counts['total_persons']}"
        assert counts["persons"][1] == 30, f"Expected 30 in person 1, got {counts['persons'][1]}"
        logger.info("Count verification PASSED.")

        # 3. Verify Pagination - Unknowns
        # Order is DESC by face_id
        page1 = db.get_faces_by_category("unknown", limit=20, offset=0)
        assert len(page1) == 20
        assert "unknown_49.jpg" in page1[0]["file_path"]
        
        page2 = db.get_faces_by_category("unknown", limit=20, offset=20)
        assert len(page2) == 20
        assert "unknown_29.jpg" in page2[0]["file_path"]

        page3 = db.get_faces_by_category("unknown", limit=20, offset=40)
        assert len(page3) == 10
        assert "unknown_9.jpg" in page3[0]["file_path"]
        
        logger.info("Pagination verification (Unknown) PASSED.")

        # 4. Check Index Usage
        with db.get_connection() as conn:
            cursor = conn.execute("EXPLAIN QUERY PLAN SELECT face_id FROM faces WHERE cluster_id = ? AND is_ignored = 0 ORDER BY face_id DESC LIMIT 20 OFFSET 0", (1,))
            plan = "\n".join([str(row) for row in cursor.fetchall()])
            logger.info(f"Query Plan for Person: {plan}")
            assert "idx_faces_cluster_ignored" in plan

            cursor = conn.execute("EXPLAIN QUERY PLAN SELECT face_id FROM faces WHERE is_ignored = 1 ORDER BY face_id DESC LIMIT 20 OFFSET 0")
            plan = "\n".join([str(row) for row in cursor.fetchall()])
            logger.info(f"Query Plan for Ignored: {plan}")
            assert "idx_faces_is_ignored" in plan

        logger.info("Index usage verification PASSED.")
        
    finally:
        # Explicitly close or cleanup if possible
        db = None
        time.sleep(0.1) # Wait for file lock release
        for ext in ["", "-wal", "-shm"]:
            f = test_db_path + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
    
    logger.info("Test completed successfully.")

if __name__ == "__main__":
    test_pagination()
