from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QWidget, QFrame, QMenu, QMessageBox, QApplication)
from PySide6.QtCore import Qt, QSize, QThread, Signal, QRect, QPoint
from PySide6.QtGui import QPixmap, QIcon, QAction, QFont, QImage
import os
from PIL import Image
import json
import logging

logger = logging.getLogger(__name__)

class FaceLoadWorker(QThread):
    group_loaded = Signal(dict)
    face_loaded = Signal(int, dict, QImage) # cid, face_info, QImage is thread-safe
    finished = Signal()


    def __init__(self, db):
        super().__init__()
        self.db = db
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        from processor.image_processor import ImageProcessor
        img_proc = ImageProcessor()
        
        # 1. First, show Unclassified (NULL Cluster) faces as a special group
        unclassified_faces = self.db.get_faces_with_meta_unclassified()
        if unclassified_faces:
            self.group_loaded.emit({
                "cid": -1, 
                "name": "👤 Unclassified / Tag Me", 
                "num_faces": len(unclassified_faces)
            })
            for f in unclassified_faces:
                if not self.is_running: break
                img = self.generate_crop(f, img_proc)
                if img:
                    self.face_loaded.emit(-1, f, img)


        # 2. Then show existing clusters
        clusters = self.db.get_clusters()
        for cid, name in sorted(clusters, key=lambda x: x[0]):
            if not self.is_running: break
            
            # Fetch all faces with metadata linked
            faces = self.db.get_faces_with_meta_for_cluster(cid)
            if not faces: continue
            
            group_data = {
                "cid": cid,
                "name": name if name else f"Person {cid}",
                "num_faces": len(faces)
            }
            self.group_loaded.emit(group_data)
            
            for f in faces:
                if not self.is_running: break
                
                # Perform cropping in background thread
                img = self.generate_crop(f, img_proc)
                if img:
                    self.face_loaded.emit(cid, f, img)

        
        self.finished.emit()

    def generate_crop(self, face_data, img_proc):
        file_path = face_data["file_path"]
        bbox = face_data["bbox"]
        meta = face_data.get("meta", {})
        frame_idx = face_data.get("frame_index", 0)
        
        is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
        
        try:
            if is_video:
                # For videos, extract the specific frame
                import cv2
                cap = cv2.VideoCapture(file_path)
                if frame_idx > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                success, frame = cap.read()
                cap.release()
                if success:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
                else:
                    # Fallback to thumbnail if specific frame extraction fails
                    thumb_path = img_proc.get_thumbnail_path(file_path)
                    if not os.path.exists(thumb_path):
                        img_proc.generate_thumbnail(file_path)
                    qimg = QImage(thumb_path)
            else: # For images or videos where frame_idx is not relevant/0
                thumb_path = img_proc.get_thumbnail_path(file_path)
                if not os.path.exists(thumb_path):
                    img_proc.generate_thumbnail(file_path)
                qimg = QImage(thumb_path)

            if qimg.isNull(): return None
            
            if bbox:
                ow = meta.get("width")
                oh = meta.get("height")
                
                # If metadata is missing, fallback to thumb size or check if it's an image
                if not ow or not oh:
                    is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
                    if not is_video:
                        try:
                            with Image.open(file_path) as tmp:
                                ow, oh = tmp.size
                        except:
                            ow, oh = qimg.width(), qimg.height()
                    else:
                        ow, oh = qimg.width(), qimg.height()

                if ow and oh:
                    # Scale factor from original to thumbnail
                    sx = qimg.width() / ow
                    sy = qimg.height() / oh
                    
                    x1, y1, x2, y2 = bbox
                    nx1, ny1 = x1 * sx, y1 * sy
                    nw, nh = (x2 - x1) * sx, (y2 - y1) * sy
                    
                    # Add padding
                    nx1 = max(0, nx1 - nw * 0.3)
                    ny1 = max(0, ny1 - nh * 0.3)
                    nw *= 1.6
                    nh *= 1.6
                    
                    rect = QRect(int(nx1), int(ny1), int(nw), int(nh)).intersected(qimg.rect())
                    if rect.width() > 0 and rect.height() > 0:
                        qimg = qimg.copy(rect)
                
            return qimg.scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        except Exception as e:
            logger.error(f"Error generating crop for {file_path}: {e}")
            return None


class FaceItem(QLabel):
    def __init__(self, face_id, file_path, pixmap, db, parent_dialog):
        super().__init__()
        self.face_id = face_id
        self.file_path = file_path
        self.db = db
        self.parent_dialog = parent_dialog
        
        self.setFixedSize(120, 120)
        self.setToolTip(f"{os.path.basename(self.file_path)}\nRight-click to organize")
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_menu)
        
        self.setStyleSheet("""
            QLabel { 
                background-color: #0F111A; 
                border: 2px solid #37474F;
                border-radius: 6px;
            }
            QLabel:hover {
                border-color: #3D5AFE;
                background-color: #1A1D2E;
            }
        """)
        self.setAlignment(Qt.AlignCenter)
        if pixmap:
            self.setPixmap(pixmap)

    def show_menu(self, pos):
        menu = QMenu(self)
        
        move_menu = menu.addMenu("🔄 Move to Person...")
        clusters = self.db.get_clusters()
        for cid, name in clusters:
            act = QAction(name if name else f"Person {cid}", self)
            act.triggered.connect(lambda checked=False, target=cid: self.move_to(target))
            move_menu.addAction(act)
            
        new_act = QAction("✨ New Person...", self)
        new_act.triggered.connect(self.move_to_new)
        move_menu.addAction(new_act)

        menu.addSeparator()
        
        open_act = QAction("📂 Open Source Photo", self)
        open_act.triggered.connect(self.open_source)
        menu.addAction(open_act)
        
        remove_act = QAction("❌ Remove This Tag", self)
        remove_act.triggered.connect(self.remove_tag)
        menu.addAction(remove_act)
        
        menu.exec(self.mapToGlobal(pos))

    def move_to(self, target_cid):
        self.db.move_face_to_cluster(self.face_id, target_cid)
        self.parent_dialog.on_face_moved(self.face_id, target_cid)

    def move_to_new(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Person", "Enter Name:")
        if ok and name:
            new_id = self.db.create_cluster_manual(name)
            self.move_to(new_id)

    def open_source(self):
        if os.name == 'nt':
            os.startfile(self.file_path)

    def remove_tag(self):
        self.db.remove_face(self.face_id)
        self.setParent(None)
        self.deleteLater()

class PersonSection(QFrame):
    def __init__(self, cid, name, db, parent_dialog):
        super().__init__()
        self.cid = cid
        self.db = db
        self.parent_dialog = parent_dialog
        
        self.setObjectName("section")
        self.setStyleSheet("""
            #section { 
                background-color: #161925; 
                border-bottom: 1px solid #2D324A;
                padding-bottom: 20px;
                margin-bottom: 10px;
            }
        """)
        
        layout = QVBoxLayout(self)
        
        header = QHBoxLayout()
        self.label = QLabel(f"<b>Person {cid}</b>: {name}")
        self.label.setStyleSheet("font-size: 14px; color: #8C9EFF;")
        header.addWidget(self.label)
        header.addStretch()
        
        btn_rename = QPushButton("Rename")
        btn_rename.setFixedWidth(80)
        btn_rename.clicked.connect(self.on_rename)
        header.addWidget(btn_rename)
        layout.addLayout(header)

        self.container = QWidget()
        self.flow_layout = QHBoxLayout(self.container) 
        self.flow_layout.setAlignment(Qt.AlignLeft)
        self.flow_layout.setSpacing(10)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedHeight(170)
        self.scroll.setWidget(self.container)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.scroll)

    def on_rename(self):
        from PySide6.QtWidgets import QInputDialog
        curr = self.label.text().split(': ')[-1].replace("</b>", "").strip()
        new_name, ok = QInputDialog.getText(self, "Rename", "New Name:", text=curr)
        if ok and new_name:
            self.db.upsert_cluster(self.cid, new_name)
            self.label.setText(f"<b>Person {self.cid}</b>: {new_name}")
            self.parent_dialog.needs_sidebar_refresh = True

    def add_face(self, face_info, pixmap):
        item = FaceItem(face_info["face_id"], face_info["file_path"], pixmap, self.db, self.parent_dialog)
        self.flow_layout.addWidget(item)

class FaceOrganizerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.sections = {} # cid -> PersonSection
        self.needs_sidebar_refresh = False
        
        self.setWindowTitle("👤 Face Organizer - Visual Repository")
        self.setMinimumSize(1000, 750)
        self.init_ui()
        self.start_loading()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        header = QLabel("Detected Face Repository")
        header.setStyleSheet("font-size: 22px; font-weight: bold; padding: 10px; color: #3D5AFE;")
        layout.addWidget(header)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        self.list_layout = QVBoxLayout(content)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)
        
        footer = QHBoxLayout()
        footer.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

    def start_loading(self):
        self.worker = FaceLoadWorker(self.db)
        self.worker.group_loaded.connect(self.add_section)
        self.worker.face_loaded.connect(self.add_face_to_section)
        self.worker.start()

    def add_section(self, data):
        cid = data["cid"]
        section = PersonSection(cid, data["name"], self.db, self)
        self.sections[cid] = section
        self.list_layout.addWidget(section)

    def add_face_to_section(self, cid, face_info, qimage):
        if cid in self.sections:
            # QPixmap conversion must happen on the main GUI thread
            pixmap = QPixmap.fromImage(qimage)
            self.sections[cid].add_face(face_info, pixmap)


    def on_face_moved(self, face_id, target_cid):
        self.needs_sidebar_refresh = True
        QMessageBox.information(self, "Success", "Face moved. Changes will be reflected when the window is reopened.")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        event.accept()
