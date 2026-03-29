from PySide6.QtWidgets import QTreeView, QMenu, QInputDialog
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction
from PySide6.QtCore import Qt, Signal

class MediaTreeView(QTreeView):
    renameRequested = Signal(str, str)
    loadRequest = Signal(object, str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Person/Period"])
        self.setModel(self.model)
        self.setHeaderHidden(False)
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
        if not index.isValid(): return
        item = self.model.itemFromIndex(index)
        if item.parent() is None:
            text = item.text()
            if text not in ["All Photos", "🚫 No Faces Detected", "Duplicates", "☣️ Corrupted Media"]:
                menu = QMenu()
                rename_action = QAction("Rename...", self)
                rename_action.triggered.connect(lambda: self.request_rename(item))
                menu.addAction(rename_action)
                menu.exec(self.viewport().mapToGlobal(position))

    def request_rename(self, item):
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "Rename", f"New name for {old_name}:", text=old_name)
        if ok and new_name and new_name != old_name:
            self.renameRequested.emit(old_name, new_name)

    def initialize_categories(self, categories):
        # Capture current scroll position
        self._target_scroll_val = self.verticalScrollBar().value()
        
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Person/Period"])
        
        # Re-add category nodes and restore their expansion state
        self.add_category_node("All Photos", None)
        self.add_category_node("🚫 No Faces Detected", -1)
        self.add_category_node("Duplicates", -2)
        self.add_category_node("☣️ Corrupted Media", -3)

        for name, cluster_id in sorted(categories):
            if cluster_id is not None and cluster_id >= 0:
                self.add_category_node(name, cluster_id)
        
        self.restore_scroll()

    def add_category_node(self, name, cluster_id):
        item = QStandardItem(name)
        item.setData(cluster_id, Qt.UserRole)
        item.setData("category", Qt.UserRole + 2)
        item.setData(False, Qt.UserRole + 3)
        item.appendRow(QStandardItem("Loading..."))
        self.model.appendRow(item)
        
        # Restore expansion if it was previously open
        key = self.get_item_key(item)
        if key in self.expanded_keys:
            self.expand(item.index())
            
        return item

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
        if key: self.expanded_keys.add(key)
        
        if item.data(Qt.UserRole + 3): return # Already loaded
        
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
                self.loadRequest.emit(item, "locations", {"cluster_id": cid, "year": year, "month": month})
        
        item.setData(True, Qt.UserRole + 3)

    def on_item_collapsed(self, index):
        item = self.model.itemFromIndex(index)
        key = self.get_item_key(item)
        if key in self.expanded_keys:
            self.expanded_keys.remove(key)

    def add_sub_items(self, parent_item, items, level):
        for val in sorted(items, reverse=(level == "years")):
            display_text = str(val) if level != "months" else f"{val}m"
            sub_item = QStandardItem(display_text)
            sub_item.setData(level, Qt.UserRole + 2)
            sub_item.setData(False, Qt.UserRole + 3)
            
            if level == "years":
                sub_item.setData(val, Qt.UserRole + 4)
                sub_item.appendRow(QStandardItem("Loading..."))
            elif level == "months":
                p = parent_item.parent()
                cid = p.data(Qt.UserRole) if p else parent_item.data(Qt.UserRole)
                year = parent_item.data(Qt.UserRole + 4)
                sub_item.setData((cid, year, val), Qt.UserRole + 1)
                sub_item.appendRow(QStandardItem("Loading..."))
            elif level == "locations":
                # Location data
                data = parent_item.data(Qt.UserRole + 1)
                if data and len(data) >= 3:
                    cid, year, month = data[:3]
                    sub_item.setData((cid, year, month, val), Qt.UserRole + 1)
                
            parent_item.appendRow(sub_item)
            
            # Auto-re-expand if needed
            key = self.get_item_key(sub_item)
            if key and key in self.expanded_keys:
                self.expand(sub_item.index())
        
        self.restore_scroll()

    def restore_scroll(self):
        """Attempts to restore the scroll position after a short delay to account for UI layout."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(10, lambda: self.verticalScrollBar().setValue(self._target_scroll_val))
