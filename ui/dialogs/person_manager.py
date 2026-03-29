from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QScrollArea, QWidget, QFrame, QMessageBox, QCheckBox, QProgressBar)
from PySide6.QtCore import Qt, QSize, QThread, Signal, QRect
from PySide6.QtGui import QPixmap, QIcon
import os
from PIL import Image
from processor.image_processor import ImageProcessor

class PersonLoadWorker(QThread):
    person_loaded = Signal(dict)
    finished = Signal()

    def __init__(self, db, img_proc):
        super().__init__()
        self.db = db
        self.img_proc = img_proc

    def run(self):
        clusters = self.db.get_clusters()
        for cid, current_name in sorted(clusters, key=lambda x: x[0]):
            data = {
                "cid": cid,
                "name": current_name,
                "pixmap": None
            }
            
            # Heavy thumbnail processing
            file_path, bbox, metadata = self.db.get_cluster_representative_data(cid)
            if file_path and os.path.exists(file_path):
                thumb_path = self.img_proc.get_thumbnail_path(file_path)
                if os.path.exists(thumb_path):
                    pix = QPixmap(thumb_path)
                    if not pix.isNull() and bbox:
                        try:
                            orig_w = metadata.get("width")
                            orig_h = metadata.get("height")
                            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov'))
                            
                            if not orig_w or not orig_h:
                                if is_video:
                                    import cv2
                                    cap = cv2.VideoCapture(file_path)
                                    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                    cap.release()
                                else:
                                    with Image.open(file_path) as orig:
                                        orig_w, orig_h = orig.size
                            
                            if orig_w and orig_h:
                                scale = min(256 / orig_w, 256 / orig_h)
                                x1, y1, x2, y2 = bbox
                                nx1, ny1 = x1 * scale, y1 * scale
                                nw, nh = (x2 - x1) * scale, (y2 - y1) * scale
                                nx1 = max(0, nx1 - nw * 0.1)
                                ny1 = max(0, ny1 - nh * 0.1)
                                nw *= 1.2
                                nh *= 1.2
                                crop_rect = QRect(int(nx1), int(ny1), int(nw), int(nh))
                                data["pixmap"] = pix.copy(crop_rect)
                        except:
                            data["pixmap"] = pix
                    elif not pix.isNull():
                        data["pixmap"] = pix
            
            self.person_loaded.emit(data)
        
        self.finished.emit()

class PersonManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.img_proc = ImageProcessor()
        self.check_boxes = {} # cid -> (checkbox, name)
        self.setWindowTitle("Manage People")
        self.setMinimumSize(540, 600)
        self.init_ui()
        self.start_loading()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        title = QLabel("👥 Registered Persons")
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(title)

        # Batch actions row
        batch_layout = QHBoxLayout()
        self.btn_batch = QPushButton("🙈 Ignore Selected")
        self.btn_batch.setFixedWidth(150)
        self.btn_batch.setStyleSheet("background-color: #EF5350; color: white; font-weight: bold; padding: 5px;")
        self.btn_batch.clicked.connect(self.on_batch_ignore)
        batch_layout.addWidget(self.btn_batch)
        
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.setFixedWidth(100)
        self.btn_select_all.clicked.connect(self.select_all)
        batch_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.setFixedWidth(100)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        batch_layout.addWidget(self.btn_deselect_all)

        batch_layout.addStretch()
        layout.addLayout(batch_layout)



        # Loading Slider
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0) # Indeterminate
        self.loading_bar.setFixedHeight(4)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setStyleSheet("QProgressBar::chunk { background-color: #3D5AFE; }")
        layout.addWidget(self.loading_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        self.list_layout = QVBoxLayout(content_widget)
        self.list_layout.setAlignment(Qt.AlignTop)
        
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)

        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def start_loading(self):
        self.check_boxes = {}
        self.worker = PersonLoadWorker(self.db, self.img_proc)
        self.worker.person_loaded.connect(self.add_person_row)
        self.worker.finished.connect(self.on_loading_finished)
        self.worker.start()

    def add_person_row(self, data):
        cid = data["cid"]
        current_name = data["name"]
        pix = data["pixmap"]

        row = QFrame()
        row.setObjectName("card")
        row.setFixedHeight(105)
        row_layout = QHBoxLayout(row)
        
        # 1. Checkbox
        cb = QCheckBox()
        cb.setFixedSize(30, 30)
        self.check_boxes[cid] = (cb, current_name)
        row_layout.addWidget(cb)

        # 2. Face Thumbnail
        thumb = QLabel()
        thumb.setFixedSize(80, 80)
        thumb.setStyleSheet("background-color: #0F111A; border-radius: 40px; border: 2px solid #5C6BC0;")
        thumb.setAlignment(Qt.AlignCenter)
        
        if pix:
            thumb.setPixmap(pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        
        row_layout.addWidget(thumb)

        # 3. Name Input
        vbox = QVBoxLayout()
        vbox.addWidget(QLabel(f"Person ID: {cid}"))
        
        name_input = QLineEdit(current_name if current_name else f"Person {cid}")
        name_input.setPlaceholderText("Enter name...")
        vbox.addWidget(name_input)
        row_layout.addLayout(vbox)

        # 4. Save Button
        btn_save = QPushButton("Save")
        btn_save.setFixedWidth(60)
        btn_save.clicked.connect(lambda checked=False, c=cid, i=name_input: self.save_name(c, i.text()))
        row_layout.addWidget(btn_save)
        
        self.list_layout.addWidget(row)

    def on_loading_finished(self):
        self.loading_bar.setVisible(False)

    def select_all(self):
        for cb, name in self.check_boxes.values():
            cb.setChecked(True)

    def deselect_all(self):
        for cb, name in self.check_boxes.values():
            cb.setChecked(False)

    def save_name(self, cid, new_name):
        if not new_name or not new_name.strip():
            return
            
        merged = self.db.upsert_cluster(cid, new_name.strip())
        if merged:
            QMessageBox.information(self, "Person Merged", 
                                  f"'{new_name}' already exists. These groups have been consolidated.")
            # Full refresh to update UI state
            while self.list_layout.count():
                item = self.list_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self.loading_bar.setVisible(True)
            self.start_loading()
        else:
            # Just update our local state if needed (optional)
            if cid in self.check_boxes:
                cb, _ = self.check_boxes[cid]
                self.check_boxes[cid] = (cb, new_name.strip())


    def on_batch_ignore(self):
        to_ignore = [cid for cid, (cb, name) in self.check_boxes.items() if cb.isChecked()]
        if not to_ignore:
            QMessageBox.warning(self, "No Selection", "Please select at least one person to ignore.")
            return

        confirm = QMessageBox.question(self, "Batch Ignore", 
                                     f"Are you sure you want to ignore {len(to_ignore)} selected persons?\n"
                                     "They will be hidden from all views.",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            batch_data = []
            for cid in to_ignore:
                name = self.check_boxes[cid][1] or ""
                batch_data.append((cid, name, 1)) # 1 for is_ignored
            
            self.db.upsert_clusters_batch(batch_data)
            # Re-scannnig the list is difficult with progressive loading, 
            # so we just close or hide the ignored rows. For simplicity, we refresh.
            while self.list_layout.count():
                item = self.list_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self.loading_bar.setVisible(True)
            self.start_loading()


