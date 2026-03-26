import os
import sys

# 1. CRITICAL: Handle DLL search path and numerical library conflicts before ANY other imports
# For Conda on Windows, specifically insightface/onnxruntime, we need the Library\bin path.
env_path = r"c:\Users\osaka\miniforge3\envs\photo_env"
env_bin = os.path.join(env_path, "Library", "bin")
if os.path.exists(env_bin):
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(env_bin)
    else:
        os.environ["PATH"] = env_bin + os.pathsep + os.environ["PATH"]

# Set OpenMP conflict flag
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sqlite3
import numpy as np
import shutil
import logging
import time
import send2trash
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QProgressBar, QFileDialog,
                             QSplitter, QLabel, QMessageBox, QFrame)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QIcon

# Local imports
from core.database import Database
from ui.widgets.tree_view import MediaTreeView
from ui.widgets.thumbnail_grid import ThumbnailGrid
from ui.theme import get_style_sheet
from ui.dialogs.settings_dialog import SettingsDialog

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AnalysisWorker(QThread):
    progress_val = Signal(int)
    phase_status = Signal(str)
    finished_all = Signal(bool, str)

    def __init__(self, folder_path, db, threshold):
        super().__init__()
        self.folder_path = folder_path
        self.db = db
        self.threshold = threshold
        self.is_cancelled = False
        
        # Defer processor loading to run() to avoid UI hang and DLL issues on main thread
        self.img_proc = None
        self.face_proc = None
        self.geo_proc = None

    def stop(self):
        self.is_cancelled = True

    def run(self):
        try:
            self.phase_status.emit("Initializing processors...")
            # Import heavy processors INSIDE background thread to ensure main stability
            from processor.image_processor import ImageProcessor
            from processor.face_processor import FaceProcessor
            from processor.geo_processor import GeoProcessor
            import cv2  

            self.img_proc = ImageProcessor()
            self.face_proc = FaceProcessor()
            self.geo_proc = GeoProcessor()
            
            self.phase_status.emit("Scanning files...")
            files = []
            for root, _, filenames in os.walk(self.folder_path):
                if self.is_cancelled: return
                for f in filenames:
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4', '.avi', '.mov')):
                        files.append(os.path.join(root, f))
            
            total = len(files)
            if total == 0:
                self.finished_all.emit(True, "No media files found.")
                return

            chunk_size = 100
            for i in range(0, total, chunk_size):
                if self.is_cancelled: break
                chunk = files[i:i+chunk_size]
                chunk_media = []
                chunk_faces = []

                for file_path in chunk:
                    if self.is_cancelled: break
                    
                    mtime = os.path.getmtime(file_path)
                    cached = self.db.get_media(file_path)
                    if cached and cached[1] == mtime:
                        continue

                    is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov'))
                    metadata = self.img_proc.get_video_metadata(file_path) if is_video else self.img_proc.get_metadata(file_path)
                    
                    year = metadata.get('year')
                    month = metadata.get('month')
                    
                    img_hash = None
                    faces = []
                    if not is_video:
                        img_hash = self.img_proc.get_image_hash(file_path)
                        faces = self.face_proc.detect_faces(file_path)
                    
                    lat = metadata.get('lat')
                    lon = metadata.get('lon')
                    alt = metadata.get('alt')
                    
                    loc = None
                    if lat and lon:
                        loc = self.geo_proc.get_location(lat, lon)

                    chunk_media.append((
                        file_path, mtime, json.dumps(metadata), img_hash,
                        lat, lon, alt, 
                        loc['country'] if loc else None, 
                        loc['prefecture'] if loc else None, 
                        loc['city'] if loc else None,
                        year, month
                    ))

                    for face in faces:
                        chunk_faces.append((file_path, face['embedding'].tobytes(), json.dumps(face['bbox'])))
                    
                    # Generate thumbnail
                    self.img_proc.generate_thumbnail(file_path)

                if chunk_media:
                    self.db.add_media_batch(chunk_media)
                if chunk_faces:
                    self.db.add_faces_batch(chunk_faces)
                
                self.progress_val.emit(int((i + len(chunk)) / total * 80))

            if self.is_cancelled:
                self.finished_all.emit(False, "Cancelled by user.")
                return

            # Clustering
            self.phase_status.emit("Clustering faces...")
            all_faces = self.db.get_all_faces()
            if all_faces:
                embeddings = [np.frombuffer(f[2], dtype=np.float32) for f in all_faces]
                if embeddings:
                    labels = self.face_proc.cluster_faces(embeddings, self.threshold)
                    face_ids = [f[0] for f in all_faces]
                    self.db.update_face_clusters_batch(face_ids, labels)
            
            self.progress_val.emit(100)
            self.finished_all.emit(True, f"Processed {total} files successfully.")

        except Exception as e:
            logger.exception("Worker Error:")
            self.finished_all.emit(False, str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoArrange - Professional Photo Organizer")
        self.setMinimumSize(1200, 850)
        
        self.db = Database()
        self.current_folder = None
        self.threshold = 5 
        
        # Pagination & Rendering state
        self.current_filter = {"cluster_id": None, "year": None, "month": None}
        self.page_size = 50
        self.current_offset = 0
        self.is_loading_more = False

        self.init_ui()
        self.apply_theme()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        
        title_label = QLabel("PhotoArrange")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #3D5AFE;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.btn_select = QPushButton("📁 Folder Select")
        self.btn_select.setFixedWidth(150)
        self.btn_select.clicked.connect(self.select_folder)
        header_layout.addWidget(self.btn_select)

        self.btn_analyze = QPushButton("🚀 Run Analysis")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setFixedWidth(150)
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self.run_analysis)
        header_layout.addWidget(self.btn_analyze)
        
        self.btn_cleanup = QPushButton("🧹 Cleanup Duplicates")
        self.btn_cleanup.setObjectName("danger")
        self.btn_cleanup.setFixedWidth(160)
        self.btn_cleanup.setVisible(False)
        self.btn_cleanup.clicked.connect(self.cleanup_duplicates)
        header_layout.addWidget(self.btn_cleanup)

        btn_settings = QPushButton("⚙️")
        btn_settings.setFixedWidth(40)
        btn_settings.clicked.connect(self.show_settings)
        header_layout.addWidget(btn_settings)

        main_layout.addWidget(header)

        # Body - Splitter
        splitter = QSplitter(Qt.Horizontal)
        
        # Left: Tree
        self.tree_view = MediaTreeView()
        self.tree_view.loadRequest.connect(self.on_tree_load_request)
        self.tree_view.clicked.connect(self.on_tree_selection)
        self.tree_view.renameRequested.connect(self.on_rename_person)
        splitter.addWidget(self.tree_view)

        # Right: Grid
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        
        self.grid_view = ThumbnailGrid()
        self.grid_view.item_clicked.connect(self.open_file)
        self.grid_view.request_more_data.connect(self.load_next_page)
        grid_layout.addWidget(self.grid_view)
        
        # Load More Button
        self.btn_load_more = QPushButton("Load More...")
        self.btn_load_more.setFixedHeight(40)
        self.btn_load_more.setVisible(False)
        self.btn_load_more.clicked.connect(self.load_next_page)
        grid_layout.addWidget(self.btn_load_more)

        splitter.addWidget(grid_container)
        splitter.setStretchFactor(1, 4)
        main_layout.addWidget(splitter)

        # Footer
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet("background-color: #1A1D2E; border-top: 1px solid #2D324A;")
        footer_layout = QHBoxLayout(footer)
        
        self.status_label = QLabel("Ready")
        footer_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setVisible(False)
        footer_layout.addWidget(self.progress_bar)
        
        main_layout.addWidget(footer)

    def apply_theme(self):
        self.setStyleSheet(get_style_sheet())

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.current_folder = folder
            self.status_label.setText(f"Folder: {folder}")
            self.btn_analyze.setEnabled(True)
            self.initialize_tree()

    def initialize_tree(self):
        clusters = self.db.get_clusters()
        categories = []
        for cid, name in clusters:
            display_name = name if name else f"Person {cid}"
            categories.append((display_name, cid))
        self.tree_view.initialize_categories(categories)

    def on_tree_load_request(self, item, level, params):
        if level == "years":
            cid = params.get("cluster_id")
            years = self.db.get_years(cid)
            self.tree_view.add_sub_items(item, years, "years")
        elif level == "months":
            cid = params.get("cluster_id")
            year = params.get("year")
            months = self.db.get_months(cid, year)
            self.tree_view.add_sub_items(item, months, "months")

    def on_tree_selection(self, index):
        item = self.tree_view.model.itemFromIndex(index)
        itype = item.data(Qt.UserRole + 2)
        
        cid = None
        year = None
        month = None

        if itype == "category":
            cid = item.data(Qt.UserRole)
        elif itype == "year":
            cid = item.parent().data(Qt.UserRole)
            year = item.data(Qt.UserRole + 4)
        elif itype == "month":
            cid, year, month = item.data(Qt.UserRole + 1)
        
        self.current_filter = {"cluster_id": cid, "year": year, "month": month}
        self.btn_cleanup.setVisible(cid == -2)
        self.show_images_paged()

    def show_images_paged(self):
        self.current_offset = 0
        self.grid_view.clear()
        
        # Reset pagination button state
        self.btn_load_more.setVisible(True)
        self.btn_load_more.setEnabled(True)
        self.btn_load_more.setText("Load More...")
        self.is_loading_more = False
        
        self.load_next_page()

    def load_next_page(self):
        # Prevent parallel loading and check if we are already at the end
        if self.is_loading_more: return
        
        # Explicitly check if the last batch was full, indicating there might be more
        # btn_load_more is used as a 'has_more' flag since pagination logic uses it
        if self.current_offset > 0 and not self.btn_load_more.isVisible():
            return

        self.is_loading_more = True
        self.btn_load_more.setEnabled(False)
        self.btn_load_more.setText("Loading Batch...")

        f = self.current_filter
        media = self.db.get_media_paged(f["cluster_id"], f["year"], f["month"], 
                                      limit=self.page_size, offset=self.current_offset)
        print(f"[DEBUG] Selection: {f}, Fetched: {len(media)} items")
        
        if not media:
            self.btn_load_more.setVisible(False)
        else:
            # Prepare data for model
            display_data = []
            from processor.image_processor import ImageProcessor
            img_proc = ImageProcessor()
            
            # Keep track of last hash for header insertion in Duplicates view
            # Note: Accessing model internal data directly for state tracking
            last_hash = self.grid_view.media_model._data[-1].get("group_hash") if self.grid_view.media_model._data else None
            
            for m in media:
                file_path = m["file_path"]
                current_hash = m.get("group_hash")
                
                # Check for new group header in Duplicates view
                if f["cluster_id"] == -2 and current_hash and current_hash != last_hash:
                    display_data.append({
                        "is_header": True,
                        "group_hash": current_hash
                    })
                    last_hash = current_hash
                
                display_data.append({
                    "file_path": file_path,
                    "thumbnail_path": img_proc.get_thumbnail_path(file_path),
                    "metadata": m["metadata"],
                    "group_hash": current_hash
                })
            
            self.grid_view.append_data(display_data)
            self.current_offset += len(media)
            self.btn_load_more.setVisible(len(media) == self.page_size)
    
    def cleanup_duplicates(self):
        # UI Feedback for search phase
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.status_label.setText("Searching for duplicates...")
        QApplication.processEvents()
        
        try:
            groups = self.db.get_duplicate_groups()
        finally:
            QApplication.restoreOverrideCursor()
            self.status_label.setText("")

        if not groups:
            QMessageBox.information(self, "Cleanup", "No duplicate groups found.")
            return
            
        confirm = QMessageBox.question(self, "Confirm Cleanup", 
                                     f"Found {len(groups)} groups. Delete smaller versions of all duplicates?\n"
                                     "(Files will be moved to Recycle Bin)",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes: return

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(groups))
        self.progress_bar.setValue(0)
        
        count = 0
        for i, group in enumerate(groups):
            # Sort by size descending
            group.sort(key=lambda x: x["metadata"].get("size", 0), reverse=True)
            
            # Keep the first (largest), delete others
            to_delete = group[1:]
            for item in to_delete:
                path = item["file_path"]
                # Normalize path for Windows compatibility (avoids Errno 3)
                norm_path = os.path.abspath(os.path.normpath(path))
                
                try:
                    if os.path.exists(norm_path):
                        send2trash.send2trash(norm_path)
                    else:
                        logger.warning(f"File already missing from disk: {norm_path}. Removing DB entry.")
                        
                    # Always remove from DB so the UI/view stays clean
                    self.db.delete_media(path)
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to delete {norm_path}: {e}")
            
            self.progress_bar.setValue(i + 1)
            if i % 10 == 0: QApplication.processEvents()

        self.progress_bar.setVisible(False)
        QMessageBox.information(self, "Cleanup Done", f"Synchronized {count} files (Deleted/Removed missing entries).")
        self.initialize_tree() # Refresh categories
        self.show_images_paged()
        
        self.is_loading_more = False
        self.btn_load_more.setEnabled(True)
        self.btn_load_more.setText("Load More...")

    def run_analysis(self):
        if not self.current_folder: return
        
        self.btn_analyze.setEnabled(False)
        self.btn_select.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.worker = AnalysisWorker(self.current_folder, self.db, self.threshold)
        self.worker.progress_val.connect(self.progress_bar.setValue)
        self.worker.phase_status.connect(self.status_label.setText)
        self.worker.finished_all.connect(self.on_process_result)
        self.worker.start()

    def on_process_result(self, success, message):
        self.btn_analyze.setEnabled(True)
        self.btn_select.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(message)
        
        if success:
            QMessageBox.information(self, "Complete", message)
            self.initialize_tree()
        else:
            QMessageBox.critical(self, "Error", message)

    def on_rename_person(self, old_name, new_name):
        # Find cluster_id
        clusters = self.db.get_clusters()
        for cid, name in clusters:
            if (name if name else f"Person {cid}") == old_name:
                self.db.upsert_cluster(cid, new_name)
                self.initialize_tree()
                break

    def open_file(self, file_path):
        import subprocess
        if os.name == 'nt':
            os.startfile(file_path)
        else:
            subprocess.call(['open', file_path])

    def show_settings(self):
        dialog = SettingsDialog(self.threshold, self)
        dialog.settings_changed.connect(self.update_threshold)
        dialog.data_reset.connect(self.reset_all)
        dialog.exec()

    def update_threshold(self, val):
        self.threshold = val

    def reset_all(self):
        self.db.clear_all_data()
        self.grid_view.clear()
        self.initialize_tree()
        self.status_label.setText("All cache cleared.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Fix for High DPI scaling
    app.setAttribute(Qt.AA_EnableHighDpiScaling)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
