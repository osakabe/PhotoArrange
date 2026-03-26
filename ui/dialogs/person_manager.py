from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QScrollArea, QWidget, QFrame, QMessageBox, QCheckBox)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QIcon
import os
from PIL import Image
from processor.image_processor import ImageProcessor

class PersonManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.img_proc = ImageProcessor()
        self.size_cache = {} # Cache for orig_w, orig_h
        self.check_boxes = {} # cid -> (checkbox, name)
        self.setWindowTitle("Manage People")
        self.setMinimumSize(540, 600)
        self.init_ui()

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
        
        batch_layout.addStretch()
        layout.addLayout(batch_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        self.list_layout = QVBoxLayout(content_widget)
        self.list_layout.setAlignment(Qt.AlignTop)
        
        self.refresh_list()
        
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)

        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def refresh_list(self):
        # Clear existing
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.check_boxes = {}
        clusters = self.db.get_clusters()
        for cid, current_name in sorted(clusters, key=lambda x: x[0]):
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
            
            file_path, bbox = self.db.get_cluster_representative_data(cid)
            if file_path and os.path.exists(file_path):
                thumb_path = self.img_proc.get_thumbnail_path(file_path)
                if os.path.exists(thumb_path):
                    from PySide6.QtCore import QRect
                    pix = QPixmap(thumb_path)
                    if not pix.isNull() and bbox:
                        try:
                            if file_path in self.size_cache:
                                orig_w, orig_h = self.size_cache[file_path]
                            else:
                                with Image.open(file_path) as orig:
                                    orig_w, orig_h = orig.size
                                self.size_cache[file_path] = (orig_w, orig_h)
                            
                            scale = min(256 / orig_w, 256 / orig_h)
                            x1, y1, x2, y2 = bbox
                            nx1, ny1 = x1 * scale, y1 * scale
                            nw, nh = (x2 - x1) * scale, (y2 - y1) * scale
                            
                            nx1 = max(0, nx1 - nw * 0.1)
                            ny1 = max(0, ny1 - nh * 0.1)
                            nw *= 1.2
                            nh *= 1.2
                            
                            crop_rect = QRect(int(nx1), int(ny1), int(nw), int(nh))
                            face_pix = pix.copy(crop_rect)
                            thumb.setPixmap(face_pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                        except:
                            thumb.setPixmap(pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                    elif not pix.isNull():
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

    def select_all(self):
        for cb, name in self.check_boxes.values():
            cb.setChecked(True)

    def save_name(self, cid, new_name):
        if new_name:
            self.db.upsert_cluster(cid, new_name)

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
            for cid in to_ignore:
                name = self.check_boxes[cid][1] or ""
                self.db.upsert_cluster(cid, name, is_ignored=True)
            self.refresh_list()
