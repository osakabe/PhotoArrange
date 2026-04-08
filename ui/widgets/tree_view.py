import logging
import threading
import traceback
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QInputDialog, QMenu, QTreeView

from core.utils import Profiler

logger = logging.getLogger("PhotoArrange")


class MediaTreeView(QTreeView):
    renameRequested = Signal(str, str)
    loadRequest = Signal(object, str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Person/Period"])
        self.setModel(self.model)
        self.setHeaderHidden(False)

        logger.info(f"Tree[{id(self)}]: Initialized. Thread: {threading.current_thread().name}")

        self.setEditTriggers(QTreeView.NoEditTriggers)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Track expansion state
        self.expanded_keys = set()
        self._target_scroll_val = 0
        self.expanded.connect(self.on_item_expanded)
        self.collapsed.connect(self.on_item_collapsed)

    def get_item_key(self, item):
        """Generates a stable unique key for identifying tree nodes across refreshes."""
        itype = item.data(Qt.UserRole + 2)
        if itype == "category":
            cid = item.data(Qt.UserRole)
            return f"cat:{cid}"
        elif itype == "years":
            cid = item.parent().data(Qt.UserRole) if item.parent() else "None"
            year = item.data(Qt.UserRole + 4)
            return f"year:{cid}:{year}"
        elif itype == "months":
            data = item.data(Qt.UserRole + 1)
            if data and len(data) >= 3:
                cid, year, month = data[:3]
                return f"month:{cid}:{year}:{month}"
        return None

    def show_context_menu(self, position):
        index = self.indexAt(position)
        if not index.isValid():
            return
        item = self.model.itemFromIndex(index)
        if item.parent() is None:
            text = item.text()
            if text not in [
                "All Photos",
                "🚫 No Faces Detected",
                "Duplicates",
                "☣️ Corrupted Media",
            ]:
                menu = QMenu()
                rename_action = QAction("Rename...", self)
                rename_action.triggered.connect(lambda: self.request_rename(item))
                menu.addAction(rename_action)
                menu.exec(self.viewport().mapToGlobal(position))

    def request_rename(self, item):
        old_name = item.data(Qt.UserRole + 10) or item.text()
        new_name, ok = QInputDialog.getText(
            self, "Rename", f"New name for {old_name}:", text=old_name
        )
        if ok and new_name and new_name != old_name:
            self.renameRequested.emit(old_name, new_name)

    def initialize_categories(self, categories: list[tuple], add_defaults: bool = True) -> None:
        """
        Initializes the tree with root categories with extreme logging.
        """
        try:
            # 1. Thread Safety Check
            is_main = threading.current_thread() is threading.main_thread()
            logger.info(
                f"Tree[{id(self)}]: initialize_categories ENTER. MainThread={is_main}, ItemCount={len(categories)}"
            )
            if not is_main:
                logger.error(
                    f"Tree[{id(self)}]: CRITICAL - initialize_categories called from WRONG THREAD: {threading.current_thread().name}"
                )

            # 2. Visibility Audit
            win = self.window()
            parent = self.parent()
            logger.info(
                f"Tree[{id(self)}]: Visibility Status - Self:{self.isVisible()}, Parent:{parent.isVisible() if parent else 'None'}, "
                f"Window:{win.isVisible() if win else 'None'}, Geometry:{self.geometry()}"
            )

            # Capture current scroll position
            self._target_scroll_val = self.verticalScrollBar().value()

            self.model.layoutAboutToBeChanged.emit()

            # 3. Model Update
            old_rows = self.model.rowCount()
            self.model.removeRows(0, old_rows)
            self.model.setHorizontalHeaderLabels(["Person/Period"])
            logger.info(f"Tree[{id(self)}]: Model cleared (Old Rows: {old_rows}).")

            if add_defaults:
                self.add_category_node("All Photos", None)
                self.add_category_node("🚫 No Faces Detected", -1)
                self.add_category_node("Duplicates", -2)
                self.add_category_node("☣️ Corrupted Media", -3)

            for i, item_data in enumerate(categories):
                try:
                    if len(item_data) >= 3:
                        name, cluster_id, count = item_data[:3]
                        label = f"{name} ({count:,})" if count is not None else name
                    elif len(item_data) == 2:
                        name, cluster_id = item_data[:2]
                        label = name
                    else:
                        logger.warning(
                            f"Tree[{id(self)}]: Invalid category data at {i}: {item_data}"
                        )
                        continue
                    self.add_category_node(label, cluster_id, name_only=name)
                except Exception as e:
                    logger.error(
                        f"Tree[{id(self)}]: Failed to add node {item_data}: {e}\n{traceback.format_exc()}"
                    )

            self.model.layoutChanged.emit()

            # 4. UI Force Refresh
            self.show()
            self.raise_()
            self.viewport().update()
            self.repaint()  # Synchronous repaint for diagnostics

            # Use Timer for column resize to ensure it happens after layout
            QTimer.singleShot(50, lambda: self.resizeColumnToContents(0))

            logger.info(
                f"Tree[{id(self)}]: initialize_categories COMPLETE. New Rows: {self.model.rowCount()}"
            )

        except Exception as e:
            logger.error(
                f"Tree[{id(self)}]: FATAL ERROR in initialize_categories: {e}\n{traceback.format_exc()}"
            )

    def add_category_node(
        self, display_text: str, cluster_id: Optional[int], name_only: str = ""
    ) -> QStandardItem:
        try:
            item = QStandardItem(display_text)
            item.setData(cluster_id, Qt.UserRole)
            item.setData(name_only or display_text, Qt.UserRole + 10)
            item.setData("category", Qt.UserRole + 2)
            item.setData(False, Qt.UserRole + 3)
            item.appendRow(QStandardItem("Loading..."))
            self.model.appendRow(item)

            key = self.get_item_key(item)
            if key and key in self.expanded_keys:
                self.expand(item.index())
            return item
        except Exception as e:
            logger.error(f"Tree[{id(self)}]: add_category_node error for '{display_text}': {e}")
            return None

    def find_category_item(self, cluster_id):
        """Finds a top-level category item by its cluster_id."""
        for i in range(self.model.rowCount()):
            item = self.model.item(i)
            if item and item.data(Qt.UserRole) == cluster_id:
                return item
        return None

    def on_item_expanded(self, index):
        item = self.model.itemFromIndex(index)
        key = self.get_item_key(item)
        if key:
            self.expanded_keys.add(key)

        if item.data(Qt.UserRole + 3):
            return  # Already loaded

        if item.rowCount() > 0 and item.child(0).text() == "Loading...":
            item.removeRow(0)

        itype = item.data(Qt.UserRole + 2)
        if itype == "category":
            cid = item.data(Qt.UserRole)
            self.loadRequest.emit(item, "years", {"cluster_id": cid})
        elif itype == "years":
            cid = item.parent().data(Qt.UserRole) if item.parent() else None
            year = item.data(Qt.UserRole + 4)
            self.loadRequest.emit(item, "months", {"cluster_id": cid, "year": year})
        elif itype == "months":
            data = item.data(Qt.UserRole + 1)
            if data and len(data) >= 3:
                cid, year, month = data[:3]
                self.loadRequest.emit(
                    item, "locations", {"cluster_id": cid, "year": year, "month": month}
                )

        item.setData(True, Qt.UserRole + 3)

    def on_item_collapsed(self, index):
        item = self.model.itemFromIndex(index)
        key = self.get_item_key(item)
        if key in self.expanded_keys:
            self.expanded_keys.remove(key)

    def _get_item_val(self, obj: Any) -> Any:
        """Extracts the value (year/month/city) from dataclasses or tuples."""
        if hasattr(obj, "year"):
            return obj.year
        if hasattr(obj, "month"):
            return obj.month
        if hasattr(obj, "city"):
            return obj.city
        return obj[0]

    def _get_item_count(self, obj: Any) -> Optional[int]:
        """Extracts the count from dataclasses or tuples."""
        if hasattr(obj, "count"):
            return obj.count
        return obj[1] if isinstance(obj, (list, tuple)) and len(obj) > 1 else None

    def add_sub_items(self, parent_item: QStandardItem, items: list[Any], level: str) -> None:
        """Adds sub-items (years, months, locations) with counts. Asynchronous result handler."""
        with Profiler(f"MediaTreeView.add_sub_items (level={level}, count={len(items)})"):
            try:
                # 1. Sort items based on level
                sorted_items = sorted(
                    items, key=lambda x: str(self._get_item_val(x)), reverse=(level == "years")
                )

                for item_obj in sorted_items:
                    val = self._get_item_val(item_obj)
                    count = self._get_item_count(item_obj)
                    self._create_sub_item(parent_item, val, count, level)

                parent_item.setData(True, Qt.UserRole + 3)  # Mark as loaded
                self.restore_scroll()
            except Exception as e:
                logger.error(f"Tree[{id(self)}]: add_sub_items error: {e}")

    def _create_sub_item(
        self, parent: QStandardItem, val: Any, count: Optional[int], level: str
    ) -> None:
        display_text = f"{val} ({count:,})" if count is not None else str(val)
        sub_item = QStandardItem(display_text)
        sub_item.setData(level, Qt.UserRole + 2)
        sub_item.setData(False, Qt.UserRole + 3)  # Is loaded
        sub_item.setData(val, Qt.UserRole + 11)  # raw_value

        if level == "years":
            sub_item.setData(val, Qt.UserRole + 4)  # year_val
            sub_item.appendRow(QStandardItem("Loading..."))
        elif level == "months":
            p = parent.parent()
            cid = p.data(Qt.UserRole) if p else parent.data(Qt.UserRole)
            year = parent.data(Qt.UserRole + 4)
            sub_item.setData((cid, year, val), Qt.UserRole + 1)
            sub_item.appendRow(QStandardItem("Loading..."))
        elif level == "locations":
            data = parent.data(Qt.UserRole + 1)
            if data and len(data) >= 3:
                cid, year, month = data[:3]
                sub_item.setData((cid, year, month, val), Qt.UserRole + 1)

        parent.appendRow(sub_item)
        key = self.get_item_key(sub_item)
        if key and key in self.expanded_keys:
            self.expand(sub_item.index())

    def restore_scroll(self):
        """Attempts to restore the scroll position after a short delay to account for UI layout."""
        QTimer.singleShot(10, lambda: self.verticalScrollBar().setValue(self._target_scroll_val))
