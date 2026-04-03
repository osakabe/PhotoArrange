from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QFrame, QMessageBox, QWidget)
from PySide6.QtCore import Qt, QRect, QThread, Signal
from PySide6.QtGui import QPixmap
import os
from PIL import Image
from processor.image_processor import ImageProcessor

class FaceLoadWorker(QThread):
    loaded = Signal(int, QPixmap, str)  # frame_idx, pixmap, name

    def __init__(self, db, img_proc, suggestions, current_index):
        super().__init__()
        self.db = db
        self.img_proc = img_proc
        self.suggestions = suggestions
        self.current_index = current_index

    def run(self):
        if self.current_index >= len(self.suggestions):
            return
            
        c1, c2, sim = self.suggestions[self.current_index]
        self._load_person(c1, 0)
        self._load_person(c2, 1)

    def _load_person(self, cid, frame_idx):
        # Fetch name
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT custom_name FROM clusters WHERE cluster_id = ?", (cid,))
            row = cursor.fetchone()
            name = row[0] if row and row[0] else f"Person {cid}"
            
        # Fetch representative image
        file_path, bbox, metadata = self.db.get_cluster_representative_data(cid)
        pix = None
        if file_path and os.path.exists(file_path):
            thumb_path = self.img_proc.get_thumbnail_path(file_path)
            if not os.path.exists(thumb_path):
                thumb_path = self.img_proc.generate_thumbnail(file_path)
            
            if os.path.exists(thumb_path):
                pix = QPixmap(thumb_path)
                if not pix.isNull() and bbox:
                    try:
                        orig_w = metadata.get("width")
                        orig_h = metadata.get("height")
                        if not orig_w:
                            # Avoid heavy Image.open if possible, but inside worker it's OK
                            with Image.open(file_path) as img: orig_w, orig_h = img.size
                        
                        scale = min(256 / orig_w, 256 / orig_h)
                        x1, y1, x2, y2 = bbox
                        nx1, ny1 = x1 * scale, y1 * scale
                        nw, nh = (x2 - x1) * scale, (y2 - y1) * scale
                        nx1, ny1 = max(0, nx1 - nw * 0.1), max(0, ny1 - nh * 0.1)
                        nw, nh = nw * 1.2, nh * 1.2
                        crop_rect = QRect(int(nx1), int(ny1), int(nw), int(nh))
                        pix = pix.copy(crop_rect)
                    except: pass
        
        self.loaded.emit(frame_idx, pix if pix else QPixmap(), name)

class FaceVerificationDialog(QDialog):
    def __init__(self, db, suggestions, parent=None):
        super().__init__(parent)
        self.db = db
        self.suggestions = suggestions
        self.current_index = 0
        self.img_proc = ImageProcessor()
        
        self.setWindowTitle("同一人物の確認")
        self.setMinimumSize(800, 500)
        self.init_ui()
        self.show_current_suggestion()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        self.title_label = QLabel("以下の人物は同一人物ですか？")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #3D5AFE; margin-bottom: 10px;")
        layout.addWidget(self.title_label)
        
        # Comparison area
        comp_layout = QHBoxLayout()
        comp_layout.setSpacing(40)
        
        # Person A
        self.frame_a = self.create_person_frame("人物 A")
        comp_layout.addWidget(self.frame_a)
        
        vs_label = QLabel("VS")
        vs_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #2D324A;")
        comp_layout.addWidget(vs_label, alignment=Qt.AlignCenter)
        
        # Person B
        self.frame_b = self.create_person_frame("人物 B")
        comp_layout.addWidget(self.frame_b)
        
        layout.addLayout(comp_layout)
        
        self.sim_label = QLabel("類似度: --")
        self.sim_label.setAlignment(Qt.AlignCenter)
        self.sim_label.setStyleSheet("font-size: 14px; color: #64748B; font-weight: bold;")
        layout.addWidget(self.sim_label)
        
        layout.addStretch()

        # Controls
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(20)
        btn_layout.addStretch()
        
        self.btn_no = QPushButton("✖ いいえ (スキップ)")
        self.btn_no.setFixedWidth(200)
        self.btn_no.setFixedHeight(45)
        self.btn_no.setObjectName("flat")
        self.btn_no.setStyleSheet("font-size: 14px;")
        self.btn_no.clicked.connect(self.on_skip)
        btn_layout.addWidget(self.btn_no)
        
        self.btn_yes = QPushButton("✔ はい (統合する)")
        self.btn_yes.setFixedWidth(200)
        self.btn_yes.setFixedHeight(45)
        self.btn_yes.setObjectName("primary")
        self.btn_yes.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_yes.clicked.connect(self.on_merge)
        btn_layout.addWidget(self.btn_yes)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        self.progress_label = QLabel("0 / 0")
        self.progress_label.setStyleSheet("color: #64748B;")
        layout.addWidget(self.progress_label, alignment=Qt.AlignRight)

    def create_person_frame(self, title):
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(15, 15, 15, 15)
        
        name_label = QLabel(title)
        name_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #E2E8F0;")
        name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_label)
        
        img_label = QLabel()
        img_label.setFixedSize(250, 250)
        img_label.setStyleSheet("background-color: #0F111A; border: 2px solid #2D324A; border-radius: 10px;")
        img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(img_label)
        
        frame.name_label = name_label
        frame.img_label = img_label
        return frame

    def show_current_suggestion(self):
        if self.current_index >= len(self.suggestions):
            QMessageBox.information(self, "完了", "すべての提案を確認しました。")
            self.accept()
            return
            
        c1, c2, sim = self.suggestions[self.current_index]
        self.sim_label.setText(f"AI予測による類似度: {sim*100:.1f}%")
        self.progress_label.setText(f"提案 {self.current_index + 1} / {len(self.suggestions)}")
        
        self.frame_a.img_label.setText("読み込み中...")
        self.frame_b.img_label.setText("読み込み中...")
        
        self.worker = FaceLoadWorker(self.db, self.img_proc, self.suggestions, self.current_index)
        self.worker.loaded.connect(self.on_face_loaded)
        self.worker.start()

    def on_face_loaded(self, frame_idx, pix, name):
        target_frame = self.frame_a if frame_idx == 0 else self.frame_b
        target_frame.name_label.setText(name)
        target_frame.person_name = name
        
        if pix and not pix.isNull():
            target_frame.img_label.setPixmap(pix.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            target_frame.img_label.setText("画像なし")

    def on_merge(self):
        c1, c2, sim = self.suggestions[self.current_index]
        name_a = self.frame_a.person_name
        name_b = self.frame_b.person_name
        
        target_name = None
        # Inherit custom name if one exists
        if f"Person {c1+1}" != name_a: target_name = name_a
        elif f"Person {c2+1}" != name_b: target_name = name_b
        
        self.db.merge_clusters(c2, c1, target_name=target_name)
        self.current_index += 1
        self.show_current_suggestion()

    def on_skip(self):
        self.current_index += 1
        self.show_current_suggestion()
