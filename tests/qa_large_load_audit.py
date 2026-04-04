import sys
import os
import logging
import time
import psutil
import re
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer, QCoreApplication

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import Database
from core.utils import get_app_data_dir, get_face_cache_dir
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

def run_formal_audit():
    app = QApplication(sys.argv)
    
    # Use the formal audit DB generated earlier
    db_path = os.path.abspath("qa_formal_audit.db")
    if not os.path.exists(db_path):
        logger.error(f"Test database not found at {db_path}. Run generate_qa_data.py first.")
        return
        
    db = Database(db_path)
    
    logger.info("=== Formal QA Audit: Face Manager Performance & Stability ===")
    
    view = FaceManagerView(db)
    view.show()
    
    results = []
    
    # --- 1. Latency & Batch Loading ---
    logger.info("TEST: Measuring 'Unknown' category initial load latency...")
    start_time = time.time()
    
    # Trigger load manually
    view.refresh_sidebar()
    for _ in range(50):
        QApplication.processEvents()
        if view.sidebar.count() > 0: break
        time.sleep(0.1)
        
    # Select 'Unknown'
    view.on_category_changed(0) 
    
    first_batch_time = None
    
    # Wait for faces to appear in model
    for _ in range(100): # 10 seconds max
        QApplication.processEvents()
        if view.face_model.rowCount() > 0:
            if first_batch_time is None:
                first_batch_time = time.time() - start_time
                logger.info(f"First batch appeared in model after {first_batch_time:.2f}s")
            if view.face_model.rowCount() >= 200: break
        time.sleep(0.1)
        
    if first_batch_time and first_batch_time < 2.0:
        results.append(("Initial Load Latency (< 2s)", "PASS"))
    else:
        results.append(("Initial Load Latency (< 2s)", f"FAIL: Took {first_batch_time}s"))

    # --- 2. Memory Usage ---
    mem_peak = get_mem_usage()
    logger.info(f"Memory Usage after 200+ faces: {mem_peak:.1f} MB")
    if mem_peak < 800: # Heuristic for 200 items in headless
        results.append(("Memory Usage (200 faces)", "PASS"))
    else:
        results.append(("Memory Usage (200 faces)", f"WARNING: {mem_peak:.1f} MB"))

    # --- 3. Bulk Selection Stress Test ---
    logger.info("TEST: Stress testing 'Select This Day' 버튼連打 (Rapid Click Stability)...")
    headers = [i for i in view.face_model._data if i.get("is_header")]
    if headers:
        header_date = headers[0].get("date_header")
        stress_start = time.time()
        try:
            # Simulate 20 rapid clicks
            for i in range(20):
                view.face_model.select_all_in_date_range(header_date)
                QApplication.processEvents()
            stress_duration = time.time() - stress_start
            logger.info(f"20 rapid selections took {stress_duration:.3f}s")
            results.append(("Bulk Selection Stress (20 clicks)", "PASS"))
        except Exception as e:
            logger.error(f"STRESS TEST CRASHED: {e}")
            results.append(("Bulk Selection Stress (20 clicks)", f"FAIL: {e}"))
    else:
        results.append(("Bulk Selection Stress", "SKIPPED (No headers)"))

    # --- 4. Data Operation Integrity (Bulk Ignore) ---
    logger.info("TEST: Bulk Ignore integrity...")
    selected_ids = view.get_selected_face_ids()
    if selected_ids:
        ignore_count_before = view.db.get_face_counts().get("ignored", 0)
        logger.info(f"Selected {len(selected_ids)} faces. Current ignored in DB: {ignore_count_before}")
        
        # We manually trigger the logic used in bulk_ignore to ensure audit completeness
        for fid in selected_ids:
            view.db.update_face_association(fid, -1, is_ignored=True)
            
        new_counts = view.db.get_face_counts()
        ignore_count_after = new_counts.get("ignored", 0)
        
        if ignore_count_after == ignore_count_before + len(selected_ids):
            results.append(("Data Integrity (Bulk Ignore)", "PASS"))
        else:
            results.append(("Data Integrity (Bulk Ignore)", f"FAIL: Expected {ignore_count_before + len(selected_ids)}, got {ignore_count_after}"))
    else:
        results.append(("Bulk Ignore Test", "SKIPPED (No selection)"))

    # Final Report
    report_path = "formal_audit_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("      FORMAL QA AUDIT REPORT - FACE MANAGER\n")
        f.write("="*60 + "\n")
        f.write(f"Timestamp: {time.ctime()}\n")
        f.write("-" * 60 + "\n")
        for test, res in results:
            status = "[PASS]" if res == "PASS" else "[FAIL]"
            if "WARNING" in res: status = "[WARN]"
            f.write(f"{status} {test}: {res}\n")
        f.write("="*60 + "\n")
        
    logger.info(f"Audit completed. Report saved to {report_path}")
    
    view.close()
    app.quit()

if __name__ == "__main__":
    run_formal_audit()
