import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .selection_toolbar import SelectionToolbar
from .thumbnail_grid import ThumbnailGrid
from .tree_view import MediaTreeView

logger = logging.getLogger("PhotoArrange")


class LibraryView(QSplitter):
    """
    The main library view containing the sidebar tree and the thumbnail grid.
    """

    # Proxy signals for convenience
    tree_load_requested = Signal(object, str, dict)
    tree_selection_changed = Signal(object)  # item
    tree_rename_requested = Signal(str, str)  # old_name, new_name

    grid_item_double_clicked = Signal(str)
    grid_tag_clicked = Signal(str, int, str)  # file_path, cluster_id, name
    grid_context_menu_requested = Signal(str, QPoint)
    grid_more_data_requested = Signal()
    grid_selection_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.init_ui()

    def init_ui(self) -> None:
        # Left: Tree
        self.tree_view = MediaTreeView()
        self.tree_view.loadRequest.connect(self.tree_load_requested.emit)
        self.tree_view.clicked.connect(self._on_tree_clicked)
        self.tree_view.renameRequested.connect(self.tree_rename_requested.emit)
        self.addWidget(self.tree_view)

        # Right: Grid Area
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)

        # Selection Toolbar (Sub-Header)
        self.toolbar = SelectionToolbar()
        grid_layout.addWidget(self.toolbar)

        # Grid View
        self.grid_view = ThumbnailGrid()
        self.grid_view.item_double_clicked.connect(self.grid_item_double_clicked.emit)
        self.grid_view.tag_clicked.connect(self.grid_tag_clicked.emit)
        self.grid_view.context_menu_requested.connect(self.grid_context_menu_requested.emit)
        self.grid_view.selection_changed.connect(self.grid_selection_changed.emit)
        self.grid_view.near_bottom_reached.connect(self.grid_more_data_requested.emit)
        grid_layout.addWidget(self.grid_view)

        self.addWidget(grid_container)
        self.setStretchFactor(1, 4)
        self.setSizes([250, 1000])  # Ensure sidebar is not collapsed by default

    def _on_tree_clicked(self, index) -> None:
        if index.isValid():
            item = self.tree_view.model.itemFromIndex(index)
            if item:
                self.tree_selection_changed.emit(item)

    def clear_grid(self) -> None:
        self.grid_view.clear()

    def append_grid_data(self, data: list) -> None:
        self.grid_view.append_data(data)

    def get_selected_files(self) -> list[str]:
        return self.grid_view.get_selected_files()

    def select_all_visible(self) -> None:
        self.grid_view.select_all()

    def deselect_all_visible(self) -> None:
        self.grid_view.deselect_all()
