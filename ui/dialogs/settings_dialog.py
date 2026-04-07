from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    settings_changed = Signal(int)  # Legacy Face threshold (Deprecated/Repurposed)
    face_det_thresh_changed = Signal(int)
    face_min_samples_changed = Signal(int)
    face_cluster_eps_changed = Signal(int)
    face_merge_threshold_changed = Signal(int)
    face_data_reset_requested = Signal()

    dup_threshold_changed = Signal(int)  # Duplicate threshold
    dup_threshold_stage2_changed = Signal(int)  # Duplicate threshold Stage 2
    force_reanalyze_changed = Signal(bool)
    include_trash_changed = Signal(bool)
    data_reset = Signal()

    def __init__(
        self,
        current_threshold,
        face_det_thresh=35,
        face_min_samples=2,
        face_cluster_eps=42,
        face_merge_threshold=55,
        current_dup_threshold=6,
        current_dup_threshold_stage2=95,
        force_reanalyze=False,
        include_trash=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 設定")
        self.setMinimumWidth(500)
        self.threshold = current_threshold
        self.face_det_thresh = face_det_thresh
        self.face_min_samples = face_min_samples
        self.face_cluster_eps = face_cluster_eps
        self.face_merge_threshold = face_merge_threshold

        self.dup_threshold = current_dup_threshold
        self.dup_threshold_stage2 = current_dup_threshold_stage2
        self.force_reanalyze = force_reanalyze
        self.include_trash = include_trash
        self.init_ui()

    def init_ui(self):
        from PySide6.QtWidgets import QScrollArea, QWidget

        main_layout = QVBoxLayout(self)

        # Scroll Area for many settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Title
        title = QLabel("アプリケーション設定")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #3D5AFE;")
        layout.addWidget(title)

        # --- General Options ---
        options_frame = QFrame()
        options_frame.setObjectName("section")
        options_layout = QVBoxLayout(options_frame)

        self.chk_force = QCheckBox("🎯 AI解析時に常に強制再解析を行う (Force Re-analyze)")
        self.chk_force.setChecked(self.force_reanalyze)
        self.chk_force.toggled.connect(self.on_force_toggled)
        options_layout.addWidget(self.chk_force)

        self.chk_trash = QCheckBox("🗑️ 検索時にゴミ箱フォルダ (.trash) を含める (Include Trash)")
        self.chk_trash.setChecked(self.include_trash)
        self.chk_trash.toggled.connect(self.on_trash_toggled)
        options_layout.addWidget(self.chk_trash)

        layout.addWidget(options_frame)

        # --- Face Recognition Section ---
        face_title = QLabel("👤 顔認識と人物整理")
        face_title.setStyleSheet("font-weight: bold; color: #3D5AFE; margin-top: 10px;")
        layout.addWidget(face_title)

        face_frame = QFrame()
        face_frame.setObjectName("section")
        face_layout = QVBoxLayout(face_frame)

        # 1. Face Detection Confidence
        det_header = QHBoxLayout()
        det_header.addWidget(QLabel("🎯 顔検出の確信度:"))
        self.det_val_label = QLabel(f"{self.face_det_thresh / 100.0:.2f}")
        self.det_val_label.setStyleSheet("color: #3D5AFE; font-weight: bold;")
        det_header.addStretch()
        det_header.addWidget(self.det_val_label)
        face_layout.addLayout(det_header)

        self.det_slider = QSlider(Qt.Horizontal)
        self.det_slider.setRange(5, 95)
        self.det_slider.setValue(self.face_det_thresh)
        self.det_slider.valueChanged.connect(self.on_det_slider_change)
        face_layout.addWidget(self.det_slider)
        face_layout.addWidget(
            QLabel("※低いほど多くの顔を検出しますが、誤検知も増えます。標準は0.35です。")
        )

        face_layout.addSpacing(10)

        # 2. Min Person Occurrence
        occ_header = QHBoxLayout()
        occ_header.addWidget(QLabel("👥 人物登録の最小出現頻度:"))
        self.occ_val_label = QLabel(f"{self.face_min_samples}回")
        self.occ_val_label.setStyleSheet("color: #3D5AFE; font-weight: bold;")
        occ_header.addStretch()
        occ_header.addWidget(self.occ_val_label)
        face_layout.addLayout(occ_header)

        self.occ_slider = QSlider(Qt.Horizontal)
        self.occ_slider.setRange(1, 20)
        self.occ_slider.setValue(self.face_min_samples)
        self.occ_slider.valueChanged.connect(self.on_occ_slider_change)
        face_layout.addWidget(self.occ_slider)
        face_layout.addWidget(
            QLabel(
                "※これ以上の回数出現した顔を、自動的に新しい人物として登録します。標準は2回です。"
            )
        )

        face_layout.addSpacing(10)

        # 3. Auto Merge Similarity (eps)
        eps_header = QHBoxLayout()
        eps_header.addWidget(QLabel("🤝 同一人物判定の類似度 (自動):"))
        self.eps_val_label = QLabel(f"{1.0 - self.face_cluster_eps / 100.0:.2f}")
        self.eps_val_label.setStyleSheet("color: #3D5AFE; font-weight: bold;")
        eps_header.addStretch()
        eps_header.addWidget(self.eps_val_label)
        face_layout.addLayout(eps_header)

        self.eps_slider = QSlider(Qt.Horizontal)
        self.eps_slider.setRange(10, 90)
        self.eps_slider.setValue(self.face_cluster_eps)
        self.eps_slider.valueChanged.connect(self.on_eps_slider_change)
        face_layout.addWidget(self.eps_slider)
        face_layout.addWidget(
            QLabel("※自動的にマージする閾値です。高いほど厳密（似ている必要がある）になります。")
        )

        face_layout.addSpacing(10)

        # 4. Suggestion Similarity
        sug_header = QHBoxLayout()
        sug_header.addWidget(QLabel("💡 同一人物判定の類似度 (提案):"))
        self.sug_val_label = QLabel(f"{1.0 - self.face_merge_threshold / 100.0:.2f}")
        self.sug_val_label.setStyleSheet("color: #3D5AFE; font-weight: bold;")
        sug_header.addStretch()
        sug_header.addWidget(self.sug_val_label)
        face_layout.addLayout(sug_header)

        self.sug_slider = QSlider(Qt.Horizontal)
        self.sug_slider.setRange(10, 90)
        self.sug_slider.setValue(self.face_merge_threshold)
        self.sug_slider.valueChanged.connect(self.on_sug_slider_change)
        face_layout.addWidget(self.sug_slider)
        face_layout.addWidget(
            QLabel("※「確認エリア」に送る閾値です。自動マージよりも広い範囲をカバーします。")
        )

        face_layout.addSpacing(15)

        self.btn_reset_faces = QPushButton("👤 顔認識データのみをリセット")
        self.btn_reset_faces.setStyleSheet("""
            QPushButton {
                background-color: #FFF3E0;
                color: #E65100;
                border: 1px solid #FFE0B2;
                padding: 8px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FFE0B2;
            }
        """)
        self.btn_reset_faces.setFixedHeight(40)
        self.btn_reset_faces.clicked.connect(self.confirm_face_reset)
        face_layout.addWidget(self.btn_reset_faces)

        layout.addWidget(face_frame)

        # --- Duplicate Detection Section ---
        dup_title = QLabel("👯 重複検知の設定")
        dup_title.setStyleSheet("font-weight: bold; color: #3D5AFE; margin-top: 10px;")
        layout.addWidget(dup_title)

        dup_frame = QFrame()
        dup_frame.setObjectName("section")
        dup_layout = QVBoxLayout(dup_frame)

        dup_header = QHBoxLayout()
        dup_label = QLabel("⚡ 重複検知の感度 (AI):")
        dup_label.setStyleSheet("font-weight: bold;")
        self.dup_val_label = QLabel(f"{self.dup_threshold / 10.0:.1f}")
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
        dup_layout.addWidget(QLabel("※大きいほど判定がゆるくなります。標準は0.6です。"))

        dup_layout.addSpacing(10)

        dup2_header = QHBoxLayout()
        dup2_label = QLabel("🔍 詳細検証の厳密度:")
        dup2_label.setStyleSheet("font-weight: bold;")
        self.dup2_val_label = QLabel(f"{self.dup_threshold_stage2 / 100.0:.2f}")
        self.dup2_val_label.setStyleSheet("color: #3D5AFE; font-size: 16px; font-weight: bold;")
        dup2_header.addWidget(dup2_label)
        dup2_header.addStretch()
        dup2_header.addWidget(self.dup2_val_label)
        dup_layout.addLayout(dup2_header)

        self.dup2_slider = QSlider(Qt.Horizontal)
        self.dup2_slider.setRange(80, 100)
        self.dup2_slider.setValue(self.dup_threshold_stage2)
        self.dup2_slider.valueChanged.connect(self.on_dup_stage2_slider_change)
        dup_layout.addWidget(self.dup2_slider)
        dup_layout.addWidget(QLabel("※大きいほど厳密に比較します。標準は0.95です。"))

        layout.addWidget(dup_frame)

        # --- Danger Zone ---
        danger_label = QLabel("⚠️ 危険な操作")
        danger_label.setStyleSheet("color: #FF5252; font-weight: bold; margin-top: 20px;")
        layout.addWidget(danger_label)

        self.btn_reset = QPushButton("🗑️ すべてのデータをキャッシュクリア・削除")
        self.btn_reset.setObjectName("danger")
        self.btn_reset.setFixedHeight(40)
        self.btn_reset.clicked.connect(self.confirm_reset)
        layout.addWidget(self.btn_reset)

        # --- Close Button ---
        btn_close = QPushButton("閉じる")
        btn_close.setFixedHeight(40)
        btn_close.clicked.connect(self.accept)
        main_layout.addWidget(btn_close)

    def on_det_slider_change(self, value):
        self.face_det_thresh = value
        self.det_val_label.setText(f"{value / 100.0:.2f}")
        self.face_det_thresh_changed.emit(value)

    def on_occ_slider_change(self, value):
        self.face_min_samples = value
        self.occ_val_label.setText(f"{value}回")
        self.face_min_samples_changed.emit(value)

    def on_eps_slider_change(self, value):
        self.face_cluster_eps = value
        self.eps_val_label.setText(f"{1.0 - value / 100.0:.2f}")  # Display as similarity
        self.face_cluster_eps_changed.emit(value)

    def on_sug_slider_change(self, value):
        self.face_merge_threshold = value
        self.sug_val_label.setText(f"{1.0 - value / 100.0:.2f}")  # Display as similarity
        self.face_merge_threshold_changed.emit(value)

    def on_dup_slider_change(self, value):
        self.dup_threshold = value
        self.dup_val_label.setText(f"{value / 10.0:.1f}")
        self.dup_threshold_changed.emit(value)

    def on_dup_stage2_slider_change(self, value):
        self.dup_threshold_stage2 = value
        self.dup2_val_label.setText(f"{value / 100.0:.2f}")
        self.dup_threshold_stage2_changed.emit(value)

    def on_force_toggled(self, checked):
        self.force_reanalyze = checked
        self.force_reanalyze_changed.emit(checked)

    def on_trash_toggled(self, checked):
        self.include_trash = checked
        self.include_trash_changed.emit(checked)

    def confirm_reset(self):
        reply = QMessageBox.warning(
            self,
            "データの削除",
            "スキャン済みのデータ（顔情報、解析結果、サムネイル）をすべて削除しますか？\nこの操作は取り消せません。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.data_reset.emit()
            QMessageBox.information(
                self, "完了", "すべてのデータを削除しました。再度スキャンを行ってください。"
            )
            self.accept()

    def confirm_face_reset(self):
        reply = QMessageBox.warning(
            self,
            "顔認識データのリセット",
            "登録済みの人物、顔グループ、ラベル情報をすべて削除しますか？\n（元の画像ファイルは削除されません）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.face_data_reset_requested.emit()
            QMessageBox.information(self, "完了", "顔認識データをリセットしました。")
            # Close dialog if user feels reset is significant, but prompt implies we might want to stay open?
            # Usually resetting data is a big move that leads to closure or stay.
            # I'll stay open for now to allow other settings or just accept.
            # Actually resetting usually means the current view is stale. I'll accept() to be safe.
            self.accept()
