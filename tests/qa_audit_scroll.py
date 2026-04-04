import os
import sys
import time
import json
import logging
import psutil
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PySide6.QtCore import Qt, QTimer, QSize

# Mock logger for audit output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QA_Audit")

from core.database import Database
from ui.widgets.face_manager_view import FaceManagerView

class AuditWindow(QMainWindow):
    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path
        self.db = Database(db_path)
        self.view = FaceManagerView(self.db)
        self.setCentralWidget(self.view)
        self.resize(1000, 800)
        
        self.stats = []
        self.start_memory = psutil.Process().memory_info().rss
        logger.info(f"Initial Memory: {self.start_memory / 1024 / 1024:.2f} MB")
        
        self.audit_timer = QTimer(self)
        self.audit_timer.timeout.connect(self.run_audit_step)
        self.audit_timer.start(500) # Check every 0.1s
        
        self.last_count = 0
        self.target_reached = False
        self.wait_cycles = 0

    def run_audit_step(self):
        current_count = self.view.flow_layout.count()
        
        # Check if we have new items or need to scroll
        if current_count > self.last_count:
            # Memory measurement at milestones
            if current_count % 1000 == 0 or current_count == 5000:
                mem = psutil.Process().memory_info().rss
                logger.info(f"--- Milestone {current_count} faces: {mem / 1024 / 1024:.2f} MB ---")
                self.stats.append((current_count, mem))
            
            self.last_count = current_count
            self.wait_cycles = 0
        else:
            self.wait_cycles += 1

        # Trigger scroll if not loading and not all loaded
        if not self.view.is_loading and not self.view.all_loaded:
            bar = self.view.scroll.verticalScrollBar()
            if bar.maximum() > 0:
                bar.setValue(bar.maximum())
                logger.info(f"Scrolled to bottom. Current items: {current_count}")
        
        # Termination condition
        if self.view.all_loaded or current_count >= 5000:
            if current_count >= 5000 or self.wait_cycles > 10:
                self.finalize_audit()

    def finalize_audit(self):
        self.audit_timer.stop()
        final_count = self.view.flow_layout.count()
        final_mem = psutil.Process().memory_info().rss
        
        logger.info("==========================================")
        logger.info(f"AUDIT COMPLETED: {final_count} faces rendered.")
        logger.info(f"Final Memory: {final_mem / 1024 / 1024:.2f} MB")
        logger.info(f"Net Growth: {(final_mem - self.start_memory) / 1024 / 1024:.2f} MB")
        logger.info("==========================================")
        
        # Layout Stability Test: Simulation of Window Resizing
        logger.info("Testing Layout Stability (Resizing 1000 -> 600 -> 1200)...")
        initial_height = self.view.grid_container.height()
        
        self.resize(600, 800)
        QApplication.processEvents()
        h_600 = self.view.grid_container.height()
        
        self.resize(1200, 800)
        QApplication.processEvents()
        h_1200 = self.view.grid_container.height()
        
        logger.info(f"Heights: 1000px width -> {initial_height}px, 600px width -> {h_600}px, 1200px width -> {h_1200}px")
        
        if h_600 > initial_height and h_1200 < initial_height:
            logger.info("RESULT: Layout stability PASS (Responsive height confirmed).")
        else:
            logger.warning("RESULT: Layout stability may have ISSUES (Height didn't adapt as expected).")
        
        # Save results to file
        with open("audit_results_milestone4.json", "w") as f:
            json.dump({
                "milestones": self.stats,
                "final_count": final_count,
                "net_growth_mb": (final_mem - self.start_memory) / 1024 / 1024,
                "stability": "PASS" if h_600 > initial_height else "FAIL"
            }, f)
            
        sys.exit(0)

if __name__ == "__main__":
    # Headless mode: Use 'offscreen' platform to avoid requiring a display
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    
    app = QApplication(sys.argv)
    db_path = os.path.abspath("qa_audit_faces.db")
    
    window = AuditWindow(db_path)
    window.show()
    
    sys.exit(app.exec())
