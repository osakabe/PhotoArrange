import logging

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton

logger = logging.getLogger("PhotoArrange")


class MainHeader(QFrame):
    """
    The top header bar containing navigation, AI operations, and settings.
    """

    folder_selection_requested = Signal()
    duplicate_analysis_requested = Signal()
    duplicate_regroup_requested = Signal()
    face_analysis_requested = Signal()
    face_clustering_requested = Signal()
    face_manager_toggled = Signal(bool)
    settings_requested = Signal()
    force_reanalyze_toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("header")
        self.setFixedHeight(40)
        self.init_ui()

    def init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 2, 15, 2)
        layout.setSpacing(15)

        # Logo & App Name
        title_icon = QLabel("📸")
        title_icon.setStyleSheet("font-size: 18px;")
        layout.addWidget(title_icon)

        title_label = QLabel("PhotoArrange")
        title_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #3D5AFE; margin-right: 15px;"
        )
        layout.addWidget(title_label)

        # Folder Selection
        self.btn_select = QPushButton("📂 Open Folder")
        self.btn_select.setFixedWidth(140)
        self.btn_select.setObjectName("flat")
        self.btn_select.setToolTip("スキャン対象のフォルダを選択します。")
        self.btn_select.clicked.connect(self.folder_selection_requested.emit)
        layout.addWidget(self.btn_select)

        layout.addStretch()

        # AI Operations Menu
        self.btn_ai_ops = QPushButton("✨ AI機能")
        self.btn_ai_ops.setFixedWidth(110)
        self.btn_ai_ops.setObjectName("primary")

        ai_menu = QMenu(self)

        self.act_dup_analysis = QAction("➕ AI分析 & 重複発見", self)
        self.act_dup_analysis.triggered.connect(self.duplicate_analysis_requested.emit)
        ai_menu.addAction(self.act_dup_analysis)

        self.act_dup_regroup = QAction("🔄 AIグループ化のみ", self)
        self.act_dup_regroup.triggered.connect(self.duplicate_regroup_requested.emit)
        ai_menu.addAction(self.act_dup_regroup)

        ai_menu.addSeparator()

        self.act_face_analysis = QAction("👤 顔認識の実行", self)
        self.act_face_analysis.triggered.connect(self.face_analysis_requested.emit)
        self.act_face_analysis.setEnabled(False)
        ai_menu.addAction(self.act_face_analysis)

        self.act_face_clustering = QAction("👥 人物グループ化", self)
        self.act_face_clustering.triggered.connect(self.face_clustering_requested.emit)
        self.act_face_clustering.setEnabled(False)
        ai_menu.addAction(self.act_face_clustering)

        ai_menu.addSeparator()

        self.act_force_toggle = QAction("🎯 強制再解析を有効にする", self)
        self.act_force_toggle.setCheckable(True)
        self.act_force_toggle.toggled.connect(self.force_reanalyze_toggled.emit)
        ai_menu.addAction(self.act_force_toggle)

        self.btn_ai_ops.setMenu(ai_menu)
        layout.addWidget(self.btn_ai_ops)

        layout.addStretch()

        # View Switching
        self.btn_faces = QPushButton("👤 顔・人物")
        self.btn_faces.setObjectName("flat")
        self.btn_faces.setFixedWidth(120)
        self.btn_faces.setCheckable(True)
        self.btn_faces.clicked.connect(lambda checked: self.face_manager_toggled.emit(checked))
        layout.addWidget(self.btn_faces)

        layout.addStretch()

        # Settings
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setObjectName("flat")
        self.btn_settings.setFixedWidth(40)
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.btn_settings)

    def set_ai_actions_enabled(self, enabled: bool) -> None:
        self.act_face_analysis.setEnabled(enabled)
        self.act_face_clustering.setEnabled(enabled)

    def set_force_reanalyze(self, checked: bool) -> None:
        self.act_force_toggle.setChecked(checked)

    def set_face_manager_active(self, active: bool) -> None:
        """Updates the Face Manager toggle button display."""
        if active:
            self.btn_faces.setText("🖼️ ライブラリ")
            self.btn_faces.setToolTip("ライブラリ表示に戻ります。")
            self.btn_faces.setChecked(True)
        else:
            self.btn_faces.setText("👤 顔・人物")
            self.btn_faces.setToolTip("顔・人物管理画面を表示します。")
            self.btn_faces.setChecked(False)
