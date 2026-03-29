from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                 QSlider, QPushButton, QMessageBox, QFrame)
from PySide6.QtCore import Qt, Signal

class SettingsDialog(QDialog):
    settings_changed = Signal(int) # Face threshold
    dup_threshold_changed = Signal(int) # Duplicate threshold
    data_reset = Signal()

    def __init__(self, current_threshold, current_dup_threshold=6, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 設定")
        self.setMinimumWidth(450)
        self.threshold = current_threshold
        self.dup_threshold = current_dup_threshold
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # Title
        title = QLabel("アプリケーション設定")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #3D5AFE;")
        layout.addWidget(title)

        # --- Face Threshold Section ---
        face_frame = QFrame()
        face_frame.setObjectName("section")
        face_layout = QVBoxLayout(face_frame)
        
        face_header = QHBoxLayout()
        face_label = QLabel("🎯 顔認識の類似度閾値:")
        face_label.setStyleSheet("font-weight: bold;")
        self.face_val_label = QLabel(str(self.threshold))
        self.face_val_label.setStyleSheet("color: #3D5AFE; font-size: 16px; font-weight: bold;")
        face_header.addWidget(face_label)
        face_header.addStretch()
        face_header.addWidget(self.face_val_label)
        face_layout.addLayout(face_header)

        self.face_slider = QSlider(Qt.Horizontal)
        self.face_slider.setRange(0, 20)
        self.face_slider.setValue(self.threshold)
        self.face_slider.valueChanged.connect(self.on_face_slider_change)
        face_layout.addWidget(self.face_slider)

        face_desc = QLabel("※小さいほど厳密。通常は5前後が推奨されます。")
        face_desc.setStyleSheet("color: #8A8EA8; font-size: 11px;")
        face_layout.addWidget(face_desc)
        layout.addWidget(face_frame)

        # --- Duplicate Threshold Section ---
        dup_frame = QFrame()
        dup_frame.setObjectName("section")
        dup_layout = QVBoxLayout(dup_frame)
        
        dup_header = QHBoxLayout()
        dup_label = QLabel("👯 重複検知の感度 (AI):")
        dup_label.setStyleSheet("font-weight: bold;")
        self.dup_val_label = QLabel(f"{self.dup_threshold/10.0:.1f}")
        self.dup_val_label.setStyleSheet("color: #3D5AFE; font-size: 16px; font-weight: bold;")
        dup_header.addWidget(dup_label)
        dup_header.addStretch()
        dup_header.addWidget(self.dup_val_label)
        dup_layout.addLayout(dup_header)

        self.dup_slider = QSlider(Qt.Horizontal)
        self.dup_slider.setRange(1, 15)
        self.dup_slider.setValue(self.dup_threshold)
        self.dup_slider.valueChanged.connect(self.on_dup_slider_change)
        dup_layout.addWidget(self.dup_slider)

        dup_desc = QLabel("※大きいほど「似ている」と判定されやすくなります。標準は0.6です。")
        dup_desc.setStyleSheet("color: #8A8EA8; font-size: 11px;")
        dup_layout.addWidget(dup_desc)
        layout.addWidget(dup_frame)

        layout.addStretch()

        # --- Danger Zone ---
        danger_label = QLabel("⚠️ 危険な操作")
        danger_label.setStyleSheet("color: #FF5252; font-weight: bold; margin-top: 10px;")
        layout.addWidget(danger_label)

        self.btn_reset = QPushButton("🗑️ すべてのデータを削除")
        self.btn_reset.setObjectName("danger")
        self.btn_reset.setFixedHeight(40)
        self.btn_reset.clicked.connect(self.confirm_reset)
        layout.addWidget(self.btn_reset)

        # --- Close Button ---
        btn_close = QPushButton("閉じる")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def on_face_slider_change(self, value):
        self.threshold = value
        self.face_val_label.setText(str(value))
        self.settings_changed.emit(value)

    def on_dup_slider_change(self, value):
        self.dup_threshold = value
        self.dup_val_label.setText(f"{value/10.0:.1f}")
        self.dup_threshold_changed.emit(value)

    def confirm_reset(self):
        reply = QMessageBox.warning(
            self, 
            "データの削除", 
            "スキャン済みのデータ（顔情報、解析結果、サムネイル）をすべて削除しますか？\nこの操作は取り消せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.data_reset.emit()
            QMessageBox.information(self, "完了", "すべてのデータを削除しました。再度スキャンを行ってください。")
            self.accept()
