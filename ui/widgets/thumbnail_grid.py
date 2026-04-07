import os

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from core.models import FaceDisplayItem, LibraryViewHeader, LibraryViewItem
from ui.ui_utils import get_item_grouping_keys


def get_grid_field(data, key, default=None):
    if hasattr(data, "get"):
        return data.get(key, default)
    if key == "file_path":
        if hasattr(data, "media"):
            return data.media.file_path
        if hasattr(data, "face"):
            return data.face.file_path
    if key == "is_duplicate" and hasattr(data, "media"):
        return getattr(data.media, "group_id", None) is not None
    if key == "group_id" and hasattr(data, "media"):
        return getattr(data.media, "group_id", default)
    if key == "person_tags" and hasattr(data, "media"):
        return getattr(data.media, "person_tags", default)
    return getattr(data, key, default)


class MediaModel(QAbstractListModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        item = self._data[row]
        if role == Qt.UserRole:
            return item
        if role == Qt.CheckStateRole:
            selected = getattr(item, "selected", False)
            return Qt.Checked if selected else Qt.Unchecked
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if index.isValid() and role == Qt.CheckStateRole:
            item = self._data[index.row()]
            if hasattr(item, "selected"):
                # DataClasses are mutable or we can use setattr
                object.__setattr__(item, "selected", value == Qt.Checked)
                self.dataChanged.emit(index, index, [Qt.CheckStateRole])
                return True
        return False

    def set_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

    def append_data(self, additional_data):
        if not additional_data:
            return
        first = len(self._data)
        last = first + len(additional_data) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._data.extend(additional_data)
        self.endInsertRows()

    def update_face_image(self, face_id: int, image: QImage):
        """Standardized method to update a face image from memory."""
        self.update_face_image_batch([(face_id, image)])

    def update_face_image_batch(self, updates: list[tuple[int, QImage]]):
        """Optimized batch update for multiple face images to reduce UI signal overhead."""
        if not updates:
            return
        
        id_to_img = {uid: img for uid, img in updates}
        min_row, max_row = -1, -1
        
        for i, item in enumerate(self._data):
            if isinstance(item, FaceDisplayItem) and item.face.face_id in id_to_img:
                item.image = id_to_img[item.face.face_id]
                if min_row == -1: min_row = i
                max_row = i
                
        if min_row != -1:
            self.dataChanged.emit(self.index(min_row), self.index(max_row), [Qt.DecorationRole])

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()

    def select_all(self, checked):
        if not self._data:
            return
        self.beginResetModel()
        for item in self._data:
            if not getattr(item, "is_header", False) and hasattr(item, "selected"):
                object.__setattr__(item, "selected", checked)
        self.endResetModel()

    def select_group(self, group_key, is_duplicate=False, date_key=None):
        self.beginResetModel()
        for item in self._data:
            if getattr(item, "is_header", False):
                continue
            
            if is_duplicate:
                if isinstance(item, LibraryViewItem) and item.ui_group_id == group_key:
                    object.__setattr__(item, "selected", True)
            else:
                if not isinstance(item, (LibraryViewItem, FaceDisplayItem)):
                    continue
                
                date_str, loc_label = get_item_grouping_keys(item)
                
                if loc_label == group_key:
                    if date_key is None or date_str == date_key:
                        object.__setattr__(item, "selected", True)
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_size = QSize(180, 270)  # Increased height for tags
        self.img_size = QSize(164, 164)

        self.margin = 8
        self.corner_radius = 8
        self.bg_color = QColor("#1A1D2E")
        self.border_color = QColor("#2D324A")
        self.hover_color = QColor("#24283D")
        self.accent_color = QColor("#3D5AFE")
        self.text_color = QColor("#8A8EA8")
        self.img_bg_color = QColor("#0F111A")
        self.is_crop_mode = False

    def set_crop_mode(self, enabled: bool):
        self.is_crop_mode = enabled
        if enabled:
            self.card_size = QSize(160, 190)  # Smaller for crops
            self.img_size = QSize(144, 144)
        else:
            self.card_size = QSize(180, 270)
            self.img_size = QSize(164, 164)

    def paint(self, painter, option, index):
        data = index.data(Qt.UserRole)
        if not data:
            return

        # Handle Header rendering
        if isinstance(data, LibraryViewHeader):
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            rect = option.rect

            # Distinct background for the header row to create clear separation
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#1F2336")))
            painter.drawRect(rect)

            painter.setPen(QPen(self.text_color))
            painter.setFont(QFont("Inter", 11, QFont.Bold))

            if data.ui_group_id:
                header_text = f"Duplicate Group #{data.ui_group_id}"
            else:
                loc = data.location_header or "Unknown Location"
                date = data.date_header or ""
                if date and date != "Unknown Date":
                    # Format date: 2024-03-29 -> 2024/03/29
                    fmt_date = date.replace("-", "/")
                    header_text = f"{loc}  •  {fmt_date}"
                else:
                    header_text = loc

            painter.setPen(QPen(self.accent_color if data.ui_group_id else self.text_color))
            painter.drawText(
                rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, header_text
            )
            # Draw a prominent divider line
            line_y = rect.center().y()
            text_width = painter.fontMetrics().horizontalAdvance(header_text)
            painter.setPen(QPen(self.border_color, 1))
            painter.drawLine(rect.left() + text_width + 25, line_y, rect.right() - 150, line_y)

            # "Select Group" Hint
            painter.setPen(QPen(self.accent_color, 0.8))
            painter.setFont(QFont("Inter", 9, QFont.Medium))
            painter.drawText(
                rect.adjusted(0, 0, -120, 0),
                Qt.AlignVCenter | Qt.AlignRight,
                "Click to Select Group",
            )

            painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        is_hovered = option.state & QStyle.State_MouseOver
        is_selected = option.state & QStyle.State_Selected
        rect = option.rect.adjusted(5, 5, -5, -5)

        # Extract base properties from specific display item types
        m = None
        face_info = None
        thumb_path = None
        is_duplicate = False
        is_in_trash = False

        if isinstance(data, LibraryViewItem):
            m = data.media
            thumb_path = m.thumbnail_path
            is_duplicate = bool(m.group_id)
            is_in_trash = bool(m.is_in_trash)
        elif isinstance(data, FaceDisplayItem):
            face_info = data.face
            thumb_path = data.image  # CAN BE a string path OR a QImage/QPixmap
            # IMPORTANT: Do not set thumb_path to None if it is a QImage! 
            # We will handle both cases in the drawing section below.

        is_survivor = not is_in_trash

        if is_selected:
            painter.setPen(QPen(self.accent_color, 2))
            painter.setBrush(self.hover_color)
        elif is_survivor and is_duplicate:
            # Highlight non-trash survivors with a RED border in Duplicates view
            painter.setPen(QPen(QColor("#F44336"), 3))  # Bold red
            painter.setBrush(self.bg_color)
        elif is_hovered:
            painter.setPen(QPen(self.accent_color, 1))
            painter.setBrush(self.hover_color)
        else:
            painter.setPen(QPen(self.border_color, 1))
            painter.setBrush(self.bg_color)
        painter.drawRoundedRect(rect, self.corner_radius, self.corner_radius)
        img_rect = QRect(
            rect.left() + self.margin,
            rect.top() + self.margin,
            self.img_size.width(),
            self.img_size.height(),
        )
        painter.setBrush(self.img_bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(img_rect, 4, 4)

        # Draw Image
        pixmap = None
        if thumb_path:
            if isinstance(thumb_path, (QPixmap, QImage)):
                pixmap = QPixmap.fromImage(thumb_path) if isinstance(thumb_path, QImage) else thumb_path
            elif isinstance(thumb_path, str) and os.path.exists(thumb_path):
                pixmap = QPixmap(thumb_path)
            
            if pixmap and not pixmap.isNull():
                mode = Qt.KeepAspectRatioByExpanding if self.is_crop_mode else Qt.KeepAspectRatio
                scaled = pixmap.scaled(self.img_size, mode, Qt.SmoothTransformation)

                # Center crop if needed
                if self.is_crop_mode:
                    target_img = QImage(self.img_size, QImage.Format_ARGB32)
                    target_img.fill(Qt.transparent)
                    p = QPainter(target_img)
                    p.setRenderHint(QPainter.Antialiasing)
                    path_clip = QPainterPath()
                    path_clip.addRoundedRect(QRect(QPoint(0, 0), self.img_size), 4, 4)
                    p.setClipPath(path_clip)

                    sx = (scaled.width() - self.img_size.width()) // 2
                    sy = (scaled.height() - self.img_size.height()) // 2
                    p.drawPixmap(-sx, -sy, scaled)
                    p.end()
                    painter.drawImage(img_rect.topLeft(), target_img)
                else:
                    px = img_rect.left() + (img_rect.width() - scaled.width()) // 2
                    py = img_rect.top() + (img_rect.height() - scaled.height()) // 2
                    painter.drawPixmap(px, py, scaled)
        else:
            painter.setPen(QPen(self.text_color))
            painter.drawText(img_rect, Qt.AlignCenter, "No Image")

        # Text Metadata
        text_rect = QRect(
            rect.left() + self.margin, img_rect.bottom() + 5, self.img_size.width(), 20
        )
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.setFont(QFont("Inter", 9, QFont.Bold))

        fname = ""
        if m:
            fname = os.path.basename(m.file_path)
        elif face_info:
            fname = os.path.basename(face_info.file_path)

        elided = painter.fontMetrics().elidedText(fname, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignTop | Qt.AlignLeft, elided)

        if m and not self.is_crop_mode:
            meta = m.metadata or {}
            size_val = meta.get("size") or 0
            date_str = m.capture_date or ""
            size_text = (
                f"{size_val / (1024 * 1024):.1f} MB"
                if size_val > 1024 * 1024
                else f"{size_val / 1024:.1f} KB"
            )
            date_text = date_str.split(" ")[0].replace(":", "/") if date_str else "Unknown Date"
            painter.setPen(QPen(self.text_color))
            painter.setFont(QFont("Inter", 8))
            meta_rect = QRect(text_rect.left(), text_rect.bottom() + 2, text_rect.width(), 15)
            painter.drawText(meta_rect, Qt.AlignTop | Qt.AlignLeft, f"{size_text}  •  {date_text}")

        # --- Person Tags (Chips) ---
        tags_raw = m.person_tags if m else ""
        if tags_raw:
            tags = []
            for t in tags_raw.split(","):
                parts = t.split(":", 1)
                if len(parts) == 2:
                    cid, name = parts
                    if not name:
                        name = f"Person {cid}" if cid != "-1" else "Unknown"
                    tags.append({"id": cid, "name": name})

            tags.sort(key=lambda x: x["name"])

            tag_x = rect.left() + self.margin

            tag_y = meta_rect.bottom() + 5

            painter.setFont(QFont("Inter", 8, QFont.Medium))
            for tag in tags:
                name = tag["name"]
                tw = painter.fontMetrics().horizontalAdvance(name) + 12
                if tag_x + tw > rect.right() - self.margin:
                    tag_x = rect.left() + self.margin
                    tag_y += 20

                if tag_y + 18 > rect.bottom() - 5:
                    break  # Out of space

                chip_rect = QRect(tag_x, tag_y, tw, 18)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor("#24283D")))
                painter.drawRoundedRect(chip_rect, 4, 4)

                painter.setPen(QPen(QColor("#E2E4EB")))
                painter.drawText(chip_rect, Qt.AlignCenter, name)
                tag_x += tw + 4

            # --- Draw "+" Add Button Chip ---
            plus_text = " ＋ "
            tw = painter.fontMetrics().horizontalAdvance(plus_text) + 12
            if tag_x + tw > rect.right() - self.margin:
                tag_x = rect.left() + self.margin
                tag_y += 20

            if tag_y + 18 <= rect.bottom() - 5:
                plus_rect = QRect(tag_x, tag_y, tw, 18)
                painter.setBrush(QBrush(QColor("#1A1D2E")))
                painter.setPen(QPen(QColor("#3D5AFE"), 1, Qt.DashLine))
                painter.drawRoundedRect(plus_rect, 4, 4)
                painter.setPen(QPen(QColor("#3D5AFE")))
                painter.drawText(plus_rect, Qt.AlignCenter, "＋")

        # --- Checkbox ---
        cb_rect = QRect(rect.left() + 10, rect.top() + 10, 20, 20)
        painter.setPen(QPen(self.text_color, 2))
        painter.setBrush(QBrush(QColor("#1A1D2E")))
        painter.drawRoundedRect(cb_rect, 4, 4)
        if get_grid_field(data, "selected"):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self.accent_color))
            painter.drawRoundedRect(cb_rect.adjusted(3, 3, -3, -3), 2, 2)

        if get_grid_field(data, "is_duplicate") and get_grid_field(data, "ui_group_id"):
            ui_group_id = get_grid_field(data, "ui_group_id")
            badge_text = f"GROUP #{ui_group_id}"
            badge_rect = QRect(img_rect.right() - 75, img_rect.top() + 5, 70, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#F44336")))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.setFont(QFont("Inter", 7, QFont.Bold))
            painter.drawText(badge_rect, Qt.AlignCenter, badge_text)
        painter.restore()

    def sizeHint(self, option, index):
        data = index.data(Qt.UserRole)
        if data and get_grid_field(data, "is_header"):
            # Ensure headers span the full width in IconMode
            view = self.parent()
            width = view.viewport().width() if view else 800
            return QSize(width - 10, 50)
        return self.card_size + QSize(10, 10)


class ThumbnailGrid(QListView):
    item_clicked = Signal(str)
    item_double_clicked = Signal(str)
    tag_clicked = Signal(str, int, str)  # file_path, cluster_id, name

    context_menu_requested = Signal(str, QPoint)
    selection_changed = Signal(int)  # Emits number of selected items
    near_bottom_reached = Signal()

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(5)
        self.setUniformItemSizes(False)
        self.media_model = MediaModel()
        self.setModel(self.media_model)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setStyleSheet("QListView { background-color: #0F111A; border: none; outline: none; }")

        self.clicked.connect(self.on_clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.verticalScrollBar().valueChanged.connect(self.check_scroll)

    def set_crop_mode(self, enabled: bool):
        self.itemDelegate().set_crop_mode(enabled)
        self.viewport().update()

    def check_scroll(self, value):
        sb = self.verticalScrollBar()
        max_scroll = sb.maximum()
        # If we are at 10% of scroll depth, request more (Very aggressive prefetching)
        if max_scroll > 0 and value >= max_scroll * 0.10:
            self.near_bottom_reached.emit()

    def clear(self):
        self.media_model.clear()
        self.selection_changed.emit(0)

    def append_data(self, data):
        self.media_model.append_data(data)

    def select_all(self, checked):
        self.media_model.select_all(checked)
        self.selection_changed.emit(self.get_selection_count())

    def get_selection_count(self):
        return sum(1 for item in self.media_model._data if get_grid_field(item, "selected"))

    def mousePressEvent(self, event):
        index = self.indexAt(event.pos())
        if not index.isValid():
            super().mousePressEvent(event)
            return

        data = index.data(Qt.UserRole)
        if not data:
            super().mousePressEvent(event)
            return

        if get_grid_field(data, "is_header"):
            rect = self.visualRect(index)
            local_pos = event.pos() - rect.topLeft()

            if get_grid_field(data, "group_id"):
                self.media_model.select_group(get_grid_field(data, "group_id"), is_duplicate=True)
            elif get_grid_field(data, "ui_group_id"):
                # Use raw group_id if ui_group_id is present (backwards compatibility)
                self.media_model.select_group(get_grid_field(data, "group_id"), is_duplicate=True)
            else:
                loc = get_grid_field(data, "location_header", "Unknown Location")
                date = get_grid_field(data, "date_header", "")
                self.media_model.select_group(loc, is_duplicate=False, date_key=date)

            self.viewport().update()
            self.selection_changed.emit(self.get_selection_count())
            return

        file_path = get_grid_field(data, "file_path", "")

        if event.button() == Qt.RightButton:
            self.context_menu_requested.emit(file_path, event.globalPos())
            return

        rect = self.visualRect(index)
        local_pos = event.pos() - rect.topLeft()

        # Check if checkbox was clicked
        if 10 <= local_pos.x() <= 35 and 10 <= local_pos.y() <= 35:
            new_val = not get_grid_field(data, "selected")
            self.media_model.setData(
                index, Qt.Checked if new_val else Qt.Unchecked, Qt.CheckStateRole
            )
            self.viewport().update()
            self.selection_changed.emit(self.get_selection_count())
            return

        # Check if a tag was clicked
        tags_raw = get_grid_field(data, "person_tags")
        if tags_raw:
            # Need to re-calculate tag layout to detect click (same logic as paint)
            tags = []
            for t in tags_raw.split(","):
                parts = t.split(":", 1)
                if len(parts) == 2:
                    cid, name = parts
                    if not name:
                        name = f"Person {cid}" if cid != "-1" else "Unknown"
                    tags.append({"id": cid, "name": name})

            tags.sort(key=lambda x: x["name"])

            tag_y_start = 164 + 8 + 5 + 20 + 2 + 15 + 5  # Matches paint logic
            tag_x = 8 + 5
            tag_y = tag_y_start

            painter = QPainter()  # Dummy for font metrics
            painter.setFont(QFont("Inter", 8, QFont.Medium))
            metrics = painter.fontMetrics()

            for t_item in tags:
                cid, name = t_item["id"], t_item["name"]
                tw = metrics.horizontalAdvance(name) + 12
                if tag_x + tw > 180 - 8:
                    tag_x = 8 + 5
                    tag_y += 20

                chip_rect = QRect(tag_x, tag_y, tw, 18)
                if chip_rect.contains(local_pos):
                    try:
                        self.tag_clicked.emit(get_grid_field(data, "file_path"), int(cid), name)
                    except ValueError:
                        self.tag_clicked.emit(get_grid_field(data, "file_path"), -1, name)
                    return
                tag_x += tw + 4

            # --- Check for "+" Add Button Click ---
            plus_text = " ＋ "
            tw = metrics.horizontalAdvance(plus_text) + 12
            if tag_x + tw > 180 - 8:
                tag_x = 8 + 5
                tag_y += 20

            plus_rect = QRect(tag_x, tag_y, tw, 18)
            if plus_rect.contains(local_pos):
                self.context_menu_requested.emit(get_grid_field(data, "file_path"), event.globalPos())
                return

        # Generic left-click: Select item and notify
        self.item_clicked.emit(file_path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            data = index.data(Qt.UserRole)
            file_path = get_grid_field(data, "file_path", "")
            if file_path:
                self.item_double_clicked.emit(file_path)
        else:
            super().mouseDoubleClickEvent(event)

    def on_clicked(self, index):

        # We handle clicks in mousePressEvent for specific areas,
        # so we might want to disable this signal-based click if needed,
        # but let's keep it for generic selection.
        pass

    def get_selected_files(self):
        selected = []
        for item in self.media_model._data:
            if get_grid_field(item, "selected"):
                selected.append(get_grid_field(item, "file_path"))
        return selected

    def set_data(self, media_list):
        self.media_model.set_data(media_list)

