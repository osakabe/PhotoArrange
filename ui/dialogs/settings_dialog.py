from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                 QSlider, QPushButton, QMessageBox, QFrame)
from PySide6.QtCore import Qt, Signal

class SettingsDialog(QDialog):
    settings_changed = Signal(int) # Signal for threshold change
    data_reset = Signal()          # Signal for data reset

    def __init__(self, current_threshold, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 設定")
        self.setMinimumWidth(400)
        self.threshold = current_threshold
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # Title
        title = QLabel("アプリケーション設定")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #3D5AFE;")
        layout.addWidget(title)

        # --- Threshold Section ---
        thresh_frame = QFrame()
        thresh_frame.setObjectName("section")
        thresh_layout = QVBoxLayout(thresh_frame)
        
        thresh_header = QHBoxLayout()
        thresh_label = QLabel("🎯 顔認識の類似度閾値:")
        thresh_label.setStyleSheet("font-weight: bold;")
        self.val_label = QLabel(str(self.threshold))
        self.val_label.setStyleSheet("color: #3D5AFE; font-size: 16px; font-weight: bold;")
        thresh_header.addWidget(thresh_label)
        thresh_header.addStretch()
        thresh_header.addWidget(self.val_label)
        thresh_layout.addLayout(thresh_header)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 20)
        self.slider.setValue(self.threshold)
        self.slider.valueChanged.connect(self.on_slider_change)
        thresh_layout.addWidget(self.slider)

        thresh_desc = QLabel("※値が小さいほど厳密に一致する顔を探します。通常は5前後が推奨されます。")
        thresh_desc.setStyleSheet("color: #8A8EA8; font-size: 11px;")
        thresh_layout.addWidget(thresh_desc)
        
        layout.addWidget(thresh_frame)

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

    def on_slider_change(self, value):
        self.threshold = value
        self.val_label.setText(str(value))
        self.settings_changed.emit(value)

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
