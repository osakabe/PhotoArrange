from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QScrollArea, QWidget, QFrame)
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
            thumb.setStyleSheet("background-color: #0F111A; border-radius: 4px;")
            
            file_path = self.db.get_cluster_representative_path(cid)
            if file_path:
                thumb_path = self.img_proc.get_thumbnail_path(file_path)
                if os.path.exists(thumb_path):
                    pix = QPixmap(thumb_path).scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    thumb.setPixmap(pix)
            
            row_layout.addWidget(thumb)

            # Name Input
            vbox = QVBoxLayout()
            vbox.addWidget(QLabel(f"Person ID: {cid}"))
            
            name_input = QLineEdit(current_name if current_name else f"Person {cid}")
            name_input.setPlaceholderText("Enter name...")
            vbox.addWidget(name_input)
            
            row_layout.addLayout(vbox)

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
            # We don't refresh the whole list to avoid focus loss, 
            # but usually, Pyside updates are enough
