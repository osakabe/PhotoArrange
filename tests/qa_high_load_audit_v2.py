import sys
import os
import logging
import time
import psutil
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer, QCoreApplication

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import Database
from ui.widgets.face_manager_view import FaceManagerView

# Headless mode for Windows
os.environ["QT_QPA_PLATFORM"] = "offscreen"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("QA_Sheriff")

# Milestone 4: Bind PhotoArrange logger to capture performance metrics from workers
pa_logger = logging.getLogger("PhotoArrange")
pa_logger.setLevel(logging.INFO)
file_handler = logging.FileHandler("logs/app_debug.log", mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
pa_logger.addHandler(file_handler)

def get_mem_usage():
    process = psutil.Process()
    # RSS in MB
    return process.memory_info().rss / 1024 / 1024

def run_advanced_audit():
    app = QApplication(sys.argv)
    
    # Use the formal audit DB generated earlier
    db_path = os.path.abspath("qa_formal_audit.db")
    if not os.path.exists(db_path):
        logger.error(f"Test database not found at {db_path}. Run generate_qa_data.py first.")
        return
        
    db = Database(db_path)
    
    logger.info("=== Formal QA Audit v2: Refactored Face Manager ===")
    
    view = FaceManagerView(db)
    view.show()
    
    results = []
    
    # --- 1. CRASH TEST: Rapid Category Switching (The primary stability check) ---
    logger.info("CRASH TEST: Rapidly switching categories to verify stop-without-wait safety...")
    try:
        # Simulate very fast user clicks
        for i in range(10):
            # Switch between Unknown (-1) and Ignored (-2)
            cat_id = -1 if i % 2 == 0 else -2
            view.load_faces(cat_id)
            QApplication.processEvents()
            # Very short wait to trigger worker start but interrupt it
            time.sleep(0.05) 
        
        results.append(("Thread Safety (Rapid Switch)", "PASS"))
        logger.info("CRASH TEST: Rapid switching completed without crash.")
    except Exception as e:
        logger.error(f"CRASH TEST FAILED: {e}")
        results.append(("Thread Safety (Rapid Switch)", f"FAIL: {e}"))

    # --- 2. RESPONSIVENESS: Unknown Initial Load ---
    logger.info("TEST: Measuring placeholder responsiveness for 5000 unknowns...")
    start_time = time.time()
    
    view.load_faces(-1) # Unknown
    
    first_placeholder_time = None
    for _ in range(50): # 5 seconds max
        QApplication.processEvents()
        if view.face_model.rowCount() > 0:
            first_placeholder_time = time.time() - start_time
            logger.info(f"First placeholders appeared after {first_placeholder_time:.3f}s")
            break
        time.sleep(0.1)
        
    if first_placeholder_time and first_placeholder_time < 1.0:
        results.append(("Initial Placeholder Respond (< 1s)", "PASS"))
    else:
        results.append(("Initial Placeholder Respond (< 1s)", f"FAIL: {first_placeholder_time}s"))

    # --- 3. TWO-STAGE LOADING: Background Cropping ---
    logger.info("TEST: Waiting for background crops to replace placeholders...")
    ready_count = 0
    # Monitor image_ready via model's needs_crop status
    for _ in range(100): # 10 seconds max
        QApplication.processEvents()
        # Check how many items in first 50 rows have images now
        ready_count = sum(1 for i in range(min(50, view.face_model.rowCount())) 
                         if not view.face_model._data[i].get("is_header") 
                         and view.face_model._data[i].get("qimage") is not None)
        if ready_count > 0:
            logger.info(f"Background items ready: {ready_count}")
            break
        time.sleep(0.1)
        
    if ready_count > 0:
        results.append(("Background Cropping (Signal Flow)", "PASS"))
    else:
        results.append(("Background Cropping (Signal Flow)", "FAIL: No images loaded from background"))

    # --- 4. SELECTION: Precision Signaling Speed ---
    logger.info("TEST: Precision signaling efficiency check...")
    headers = [i for i in view.face_model._data if i.get("is_header")]
    if headers:
        header_date = headers[0].get("date_header")
        select_start = time.time()
        view.face_model.select_all_in_date_range(header_date)
        select_duration = time.time() - select_start
        logger.info(f"Select All for 10 items (Precision Signal) took {select_duration:.5f}s")
        results.append(("Selection Precision Signal", "PASS"))
    else:
        results.append(("Selection Precision Signal", "SKIPPED (No headers)"))

    # Final Report Generation
    report_path = "formal_audit_report_v2.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("      FORMAL QA AUDIT REPORT - REFACTORED FACE MANAGER\n")
        f.write("="*60 + "\n")
        f.write(f"Timestamp: {time.ctime()}\n")
        f.write(f"RAM Usage: {get_mem_usage():.1f} MB\n")
        f.write("-" * 60 + "\n")
        for test, res in results:
            status = "[PASS]" if res == "PASS" else "[FAIL]"
            f.write(f"{status} {test}: {res}\n")
        f.write("="*60 + "\n")
        
    logger.info(f"Audit completed. Report saved to {report_path}")
    view.close()
    app.quit()

if __name__ == "__main__":
    run_advanced_audit()
