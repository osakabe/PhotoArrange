import os
import sys
import sqlite3
import numpy as np
from PIL import Image
import torch
import json
import logging

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import Database
from processor.face_processor import FaceProcessor
from main import FaceRecognitionWorker, clustering_logic

# Setup Logger for Console Output during test
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("QA_Sheriff")

def create_test_image(path):
    # Dummy black image 640x640
    img = Image.new('RGB', (640, 640), color=(128, 128, 128))
    img.save(path)
    return os.path.abspath(os.path.normpath(path))

def test_flow():
    test_db_path = "qa_test_faces.db"
    if os.path.exists(test_db_path): os.remove(test_db_path)
    
    db = Database(db_path=test_db_path)
    # Initialize schema (Database.__init__ handles this)
    
    test_dir = "qa_test_images"
    os.makedirs(test_dir, exist_ok=True)
    img_path = create_test_image(os.path.join(test_dir, "test_face_1.jpg"))
    
    # Register image in DB (needed for file_path link)
    db.add_media_batch([(img_path, 0, "{}", None, 0, 0, 0, None, None, None, 0, 0, None, 0, 0, "", None, None)])

    logger.info("--- Phase 1: Worker Initialization ---")
    worker = FaceRecognitionWorker(test_dir, db, force_reanalyze=True)
    
    # Mock Signals
    worker.progress_val = type('Signal', (), {'emit': lambda x: logger.info(f"Progress: {x}%")})()
    worker.phase_status = type('Signal', (), {'emit': lambda x: logger.info(f"Status: {x}")})()
    worker.finished_all = type('Signal', (), {'emit': lambda ok, msg: logger.info(f"Finished: {ok}, {msg}")})()

    logger.info("--- Phase 2: AI Inference (Detect & Cluster) ---")
    try:
        # We manually call run() logic for headless testing
        worker.run()
        
        # Verify Database
        with db.get_connection() as conn:
            faces = conn.execute("SELECT * FROM faces").fetchall()
            clusters = conn.execute("SELECT * FROM clusters").fetchall()
            
        logger.info(f"Results: {len(faces)} faces detected, {len(clusters)} clusters created.")
        
        # In a dummy gray image, we expect 0 faces, but the FLOW should be successful.
        # If we wanted to test face detection, we'd need a real face image.
        if len(faces) == 0:
            logger.info("PASS: No faces detected in dummy image as expected, but flow completed.")
        else:
            logger.info(f"WARN: {len(faces)} faces detected in dummy image? Check logic.")

    except Exception as e:
        logger.exception(f"FAIL: Logic Error: {e}")
        sys.exit(1)
        
    finally:
        # Cleanup
        if os.path.exists(test_db_path): os.remove(test_db_path)
        # Keep images for fault injection test if needed, but remove for now
        import shutil
        shutil.rmtree(test_dir)

if __name__ == "__main__":
    test_flow()
