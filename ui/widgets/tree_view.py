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
        self.expanded.connect(self.on_item_expanded)

    def show_context_menu(self, position):
        index = self.indexAt(position)
        if not index.isValid(): return
        item = self.model.itemFromIndex(index)
        if item.parent() is None:
            text = item.text()
            if text not in ["All Photos", "Unclassified", "Duplicates"]:
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
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Person/Period"])
        self.add_category_node("All Photos", None)
        self.add_category_node("Unclassified", -1)
        self.add_category_node("Duplicates", -2)
        for name, cluster_id in sorted(categories):
            if cluster_id is not None and cluster_id >= 0:
                self.add_category_node(name, cluster_id)

    def add_category_node(self, name, cluster_id):
        item = QStandardItem(name)
        item.setData(cluster_id, Qt.UserRole)
        item.setData("category", Qt.UserRole + 2)
        item.setData(False, Qt.UserRole + 3)
        item.appendRow(QStandardItem("Loading..."))
        self.model.appendRow(item)
        return item

    def on_item_expanded(self, index):
        item = self.model.itemFromIndex(index)
        if item.data(Qt.UserRole + 3): return
        if item.rowCount() > 0 and item.child(0).text() == "Loading...":
            item.removeRow(0)
        itype = item.data(Qt.UserRole + 2)
        if itype == "category":
            cid = item.data(Qt.UserRole)
            self.loadRequest.emit(item, "years", {"cluster_id": cid})
        elif itype == "year":
            cid = item.parent().data(Qt.UserRole)
            year = item.data(Qt.UserRole + 4)
            self.loadRequest.emit(item, "months", {"cluster_id": cid, "year": year})
        item.setData(True, Qt.UserRole + 3)

    def add_sub_items(self, parent_item, items, level):
        for val in sorted(items, reverse=(level == "years")):
            display_text = str(val) if level == "years" else f"{val}m"
            sub_item = QStandardItem(display_text)
            sub_item.setData(level, Qt.UserRole + 2)
            sub_item.setData(False, Qt.UserRole + 3)
            if level == "years":
                sub_item.setData(val, Qt.UserRole + 4)
                sub_item.appendRow(QStandardItem("Loading..."))
            else:
                p = parent_item.parent()
                cid = p.data(Qt.UserRole) if p else parent_item.data(Qt.UserRole)
                year = parent_item.data(Qt.UserRole + 4)
                sub_item.setData((cid, year, val), Qt.UserRole + 1)
            parent_item.appendRow(sub_item)
