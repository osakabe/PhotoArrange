from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QScrollArea, QWidget, QFrame, QMessageBox)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QIcon
import os
from processor.image_processor import ImageProcessor

class PersonManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.img_proc = ImageProcessor()
        self.setWindowTitle("Manage People")
        self.setMinimumSize(500, 600)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        title = QLabel("👥 Registered Persons")
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 15px;")
        layout.addWidget(title)

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

        clusters = self.db.get_clusters()
        for cid, current_name in sorted(clusters, key=lambda x: x[0]):
            row = QFrame()
            row.setObjectName("card")
            row.setFixedHeight(100)
            row_layout = QHBoxLayout(row)
            
            # Thumbnail
            thumb = QLabel()
            thumb.setFixedSize(80, 80)
            thumb.setStyleSheet("background-color: #0F111A; border-radius: 40px; border: 2px solid #5C6BC0;")
            thumb.setAlignment(Qt.AlignCenter)
            
            file_path, bbox = self.db.get_cluster_representative_data(cid)
            if file_path:
                # We need the original image or at least a reasonably high-res thumbnail to crop
                # For simplicity, we load the original if it exists
                if os.path.exists(file_path):
                    from PySide6.QtCore import QRect
                    pix = QPixmap(file_path)
                    if not pix.isNull() and bbox:
                        # bbox format: [x1, y1, x2, y2]
                        x1, y1, x2, y2 = bbox
                        # Add some padding around the face (20%)
                        w = x2 - x1
                        h = y2 - y1
                        x1 = max(0, x1 - w * 0.1)
                        y1 = max(0, y1 - h * 0.1)
                        w *= 1.2
                        h *= 1.2
                        
                        crop_rect = QRect(int(x1), int(y1), int(w), int(h))
                        face_pix = pix.copy(crop_rect)
                        thumb.setPixmap(face_pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                    elif not pix.isNull():
                        # Fallback to center crop if no bbox
                        thumb.setPixmap(pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            
            row_layout.addWidget(thumb)

            # Name Input
            vbox = QVBoxLayout()
            vbox.addWidget(QLabel(f"Person ID: {cid}"))
            
            name_input = QLineEdit(current_name if current_name else f"Person {cid}")
            name_input.setPlaceholderText("Enter name...")
            vbox.addWidget(name_input)
            
            row_layout.addLayout(vbox)

            # Ignore Button
            btn_ignore = QPushButton("🙈 Ignore")
            btn_ignore.setFixedWidth(70)
            btn_ignore.setStyleSheet("background-color: #37474F; color: #ECEFF1;")
            btn_ignore.clicked.connect(lambda checked=False, c=cid: self.ignore_person(c))
            row_layout.addWidget(btn_ignore)

            # Save Button for this row
            btn_save = QPushButton("Save")
            btn_save.setFixedWidth(60)
            # Use closure to capture values
            btn_save.clicked.connect(lambda checked=False, c=cid, i=name_input: self.save_name(c, i.text()))
            row_layout.addWidget(btn_save)
            
            self.list_layout.addWidget(row)

    def save_name(self, cid, new_name):
        if new_name:
            self.db.upsert_cluster(cid, new_name)

    def ignore_person(self, cid):
        confirm = QMessageBox.question(self, "Ignore Person", 
                                     "Are you sure you want to ignore this person? They will be hidden from the UI.",
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm == QMessageBox.Yes:
            self.db.upsert_cluster(cid, "", is_ignored=True)
            self.refresh_list()
