import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QPushButton

logger = logging.getLogger("PhotoArrange")


class SelectionToolbar(QFrame):
    """
    Toolbar for contextual actions when media items are selected.
    """

    select_all_requested = Signal()
    deselect_all_requested = Signal()
    clear_tags_requested = Signal()
    cleanup_duplicates_requested = Signal()
    release_from_group_requested = Signal()
    delete_selected_requested = Signal()
    duplicate_filter_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sub_header")
        self.setFixedHeight(36)
        self.init_ui()

    def init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(10)

        self.btn_select_all = QPushButton("✅ Select All")
        self.btn_select_all.setObjectName("flat")
        self.btn_select_all.setFixedWidth(110)
        self.btn_select_all.clicked.connect(self.select_all_requested.emit)
        layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("❌ Deselect All")
        self.btn_deselect_all.setObjectName("flat")
        self.btn_deselect_all.setFixedWidth(120)
        self.btn_deselect_all.clicked.connect(self.deselect_all_requested.emit)
        layout.addWidget(self.btn_deselect_all)

        self.btn_clear_tags = QPushButton("🏷️ Clear Tags")
        self.btn_clear_tags.setObjectName("flat")
        self.btn_clear_tags.setFixedWidth(110)
        self.btn_clear_tags.setEnabled(False)
        self.btn_clear_tags.clicked.connect(self.clear_tags_requested.emit)
        layout.addWidget(self.btn_clear_tags)

        self.combo_dup_filter = QComboBox()
        self.combo_dup_filter.addItems(
            ["すべて（統合表示）", "MD5（完全一致）", "AI（視覚的類似性）"]
        )
        self.combo_dup_filter.setFixedWidth(200)
        self.combo_dup_filter.setVisible(False)
        self.combo_dup_filter.currentIndexChanged.connect(self.duplicate_filter_changed.emit)
        layout.addWidget(self.combo_dup_filter)

        layout.addStretch()

        self.btn_cleanup = QPushButton("🧹 Cleanup Duplicates")
        self.btn_cleanup.setObjectName("danger")
        self.btn_cleanup.setFixedWidth(180)
        self.btn_cleanup.setVisible(False)
        self.btn_cleanup.clicked.connect(self.cleanup_duplicates_requested.emit)
        layout.addWidget(self.btn_cleanup)

        self.btn_release_from_group = QPushButton("🔗 重複から除外")
        self.btn_release_from_group.setFixedWidth(140)
        self.btn_release_from_group.setEnabled(False)
        self.btn_release_from_group.clicked.connect(self.release_from_group_requested.emit)
        layout.addWidget(self.btn_release_from_group)

        self.btn_delete_selected = QPushButton("🗑️ Delete")
        self.btn_delete_selected.setObjectName("danger")
        self.btn_delete_selected.setFixedWidth(100)
        self.btn_delete_selected.setEnabled(False)
        self.btn_delete_selected.clicked.connect(self.delete_selected_requested.emit)
        layout.addWidget(self.btn_delete_selected)

    def set_selection_actions_enabled(self, has_selection: bool) -> None:
        self.btn_clear_tags.setEnabled(has_selection)
        self.btn_release_from_group.setEnabled(has_selection)
        self.btn_delete_selected.setEnabled(has_selection)

    def set_duplicate_mode(self, enabled: bool) -> None:
        self.combo_dup_filter.setVisible(enabled)
        self.btn_cleanup.setVisible(enabled)
        self.btn_clear_tags.setVisible(not enabled)
