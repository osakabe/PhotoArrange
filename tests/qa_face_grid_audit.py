import sys
import os
import logging
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QSize, QRect

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ui.widgets.face_manager_view import FaceManagerView, FaceItem, FlowLayout

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("QA_Audit")

class MockDB:
    def __init__(self):
        self.faces = [{"face_id": i, "file_path": "dummy.jpg", "bbox": [0,0,100,100], "person_id": i} for i in range(20)]
        # Create a real dummy image so get_or_generate_crop doesn't fail early
        with open("dummy.jpg", "wb") as f:
            f.write(b"fake data")
    
    def get_face_counts(self):
        return {"unknown": 20, "ignored": 0}
    
    def get_person_list_with_counts(self):
        return []

    def get_faces_by_category(self, category, person_id=None):
        return self.faces

def run_audit():
    app = QApplication(sys.argv)
    
    # Pre-create a dummy cache image so the worker finds it immediately
    import os
    from core.utils import get_face_cache_dir
    cache_dir = get_face_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    for i in range(20):
        with open(os.path.join(cache_dir, f"face_{i}.jpg"), "wb") as f:
            f.write(b"dummy")

    db = MockDB()
    view = FaceManagerView(db)
    # Ensure UI is rendered
    view.show()
    QApplication.processEvents()

    results = []

    logger.info("--- QA Audit: Face Grid Display & Scroll ---")

    # 1. Display Correctness (Item Size)
    face_item = FaceItem(db.faces[0], None)
    size = face_item.sizeHint()
    logger.info(f"Checking Item Size: {size.width()}x{size.height()}")
    if 135 <= size.width() <= 145 and 155 <= size.height() <= 165:
        results.append(("Item Size (Target 140x160)", "PASS"))
    else:
        results.append(("Item Size (Target 140x160)", f"FAIL: Actual {size.width()}x{size.height()}"))

    # 2. FlowLayout & Wrapping Logic
    container = view.grid_container
    layout = view.flow_layout
    
    # Load 20 items
    view.load_faces(-1) # Unknown
    QApplication.processEvents()
    
    # Wait for async load (though mock is synchronous, worker is thread)
    timeout = 1000
    while layout.count() < 20 and timeout > 0:
        QApplication.processEvents()
        import time
        time.sleep(0.01) # Small sleep
        timeout -= 1

    # Check Wrapping at 800px width
    view.resize(1000, 600) # Sidebar is 250px, Grid area ~750px
    QApplication.processEvents()
    
    # Grid container width is roughly 750 - scrollbar
    grid_w = container.width()
    item_w = 140
    spacing = layout.spacing()
    margin = layout.contentsMargins().left() + layout.contentsMargins().right()
    
    expected_cols = (grid_w - margin + spacing) // (item_w + spacing)
    logger.info(f"Grid Width: {grid_w}, Expected Columns: {expected_cols}, Actual Items: {layout.count()}")
    
    rows = (layout.count() + expected_cols - 1) // expected_cols
    expected_height = rows * 160 + (rows - 1) * spacing + layout.contentsMargins().top() + layout.contentsMargins().bottom()
    
    actual_height = container.height()
    logger.info(f"Expected Height: ~{expected_height}, Actual Height: {actual_height}")
    
    if actual_height >= expected_height - 10: # Allow some margin
        results.append(("Scroll Height Calculation", "PASS"))
    else:
        results.append(("Scroll Height Calculation", f"FAIL: Height too small ({actual_height} < {expected_height})"))

    # 3. Resize Resilience
    # Change width and check if height adapts
    view.resize(600, 600) # Grid area ~350px
    QApplication.processEvents()
    new_height = container.height()
    logger.info(f"Resized Width to 600. New Height: {new_height}")
    if new_height > actual_height:
        results.append(("Dynamic Resizing (Cols -> Rows)", "PASS"))
    else:
        results.append(("Dynamic Resizing (Cols -> Rows)", "FAIL: Height did not increase on narrowing window"))

    # 4. Count Consistency
    sidebar_text = view.sidebar.item(0).text()
    import re
    match = re.search(r'\((\d+)\)', sidebar_text)
    if match:
        count = int(match.group(1))
        logger.info(f"Sidebar Count: {count}, Grid Items: {layout.count()}")
        if count == layout.count():
            results.append(("Count Sync (Sidebar vs Grid)", "PASS"))
        else:
            results.append(("Count Sync (Sidebar vs Grid)", f"FAIL: {count} != {layout.count()}"))

    # Final Report
    with open("audit_results.txt", "w", encoding="utf-8") as f:
        f.write("      QA SHERIFF AUDIT REPORT\n")
        f.write("="*40 + "\n")
        for test, res in results:
            status = "[PASS]" if res == "PASS" else "[FAIL]"
            f.write(f"{status} {test}: {res}\n")
        f.write("="*40 + "\n")

    # Done
    view.close()
    app.quit()

if __name__ == "__main__":
    run_audit()
