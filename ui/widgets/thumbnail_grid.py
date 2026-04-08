import logging
import os
from typing import Any, Callable, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.models import FaceDisplayItem, LibraryViewHeader, LibraryViewItem
from ui.constants import Metrics, Theme

logger = logging.getLogger("PhotoArrange")


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
        logger.info(
            f"MediaModel: Appending {len(additional_data)} items (Index {first} to {last}). Previous total: {len(self._data)}"
        )
        self.beginInsertRows(QModelIndex(), first, last)
        self._data.extend(additional_data)
        self.endInsertRows()
        logger.info(f"MediaModel: Append completed. Total items: {len(self._data)}")

    def update_face_image(self, face_id: int, image: QImage):
        """Standardized method to update a face image from memory."""
        self.update_face_image_batch([(face_id, image)])

    def update_face_image_batch(self, updates):
        """Optimized batch update for multiple face images.
        Handles both list[tuple[int, QImage]] and list[FaceCropResult].
        """
        if not updates:
            return

        id_to_img = {}
        for item in updates:
            if hasattr(item, "face_id") and hasattr(item, "image"):
                id_to_img[item.face_id] = item.image
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                id_to_img[item[0]] = item[1]
            else:
                logger.warning(f"MediaModel: Unexpected update item type: {type(item)}")
        min_row, max_row = -1, -1

        for i, item in enumerate(self._data):
            if isinstance(item, FaceDisplayItem) and item.face.face_id in id_to_img:
                object.__setattr__(item, "image", id_to_img[item.face.face_id])
                if min_row == -1:
                    min_row = i
                max_row = i

        if min_row != -1:
            self.dataChanged.emit(self.index(min_row), self.index(max_row), [Qt.DecorationRole])

    def update_media_image_batch(self, updates: list[tuple[str, QImage]]):
        """Standardized pre-loader injection for LibraryViewItems."""
        if not updates:
            return

        path_to_img = {path: img for path, img in updates}
        min_row, max_row = -1, -1

        for i, item in enumerate(self._data):
            if isinstance(item, LibraryViewItem) and item.media.file_path in path_to_img:
                # Store the QImage in the thumbnail_path attribute (handled by delegate)
                object.__setattr__(item.media, "thumbnail_path", path_to_img[item.media.file_path])
                if min_row == -1:
                    min_row = i
                max_row = i

        if min_row != -1:
            self.dataChanged.emit(self.index(min_row), self.index(max_row), [Qt.DecorationRole])

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()

    def select_all(self, checked: bool) -> None:
        """Sets the selected state for all non-header items."""
        if not self._data:
            return
        self.beginResetModel()
        for item in self._data:
            if not item.is_header and hasattr(item, "selected"):
                item.selected = checked
        self.endResetModel()

    def select_where(self, predicate: Callable[[Any], bool]) -> None:
        """
        Generic selection strategy. Sets 'selected=True' for all items
        that satisfy the given predicate.
        """
        self.beginResetModel()
        for item in self._data:
            if not item.is_header and predicate(item):
                item.selected = True
        self.endResetModel()

    def select_contiguous_group(self, start_index: int) -> None:
        """
        Selects all items starting from start_index + 1 until
        the next header or end of list.
        """
        if start_index < 0 or start_index >= len(self._data):
            return

        self.beginResetModel()
        for i in range(start_index + 1, len(self._data)):
            item = self._data[i]
            if getattr(item, "is_header", False):
                break
            if hasattr(item, "selected"):
                object.__setattr__(item, "selected", True)
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_size = QSize(Metrics.CARD_WIDTH, Metrics.CARD_HEIGHT)
        self.img_size = QSize(Metrics.THUMB_SIZE, Metrics.THUMB_SIZE)
        self.is_crop_mode = False

    def set_crop_mode(self, enabled: bool):
        self.is_crop_mode = enabled
        if enabled:
            self.card_size = QSize(Metrics.CROP_CARD_WIDTH, Metrics.CROP_CARD_HEIGHT)
            self.img_size = QSize(Metrics.CROP_THUMB_SIZE, Metrics.CROP_THUMB_SIZE)
        else:
            self.card_size = QSize(Metrics.CARD_WIDTH, Metrics.CARD_HEIGHT)
            self.img_size = QSize(Metrics.THUMB_SIZE, Metrics.THUMB_SIZE)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        data = index.data(Qt.UserRole)
        if not data:
            return

        if isinstance(data, LibraryViewHeader):
            self._draw_header(painter, option.rect, data)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. Setup Data & State
        is_hovered = bool(option.state & QStyle.State_MouseOver)
        is_selected = bool(option.state & QStyle.State_Selected)
        rect = option.rect.adjusted(5, 5, -5, -5)

        media = data.media if isinstance(data, LibraryViewItem) else None
        face_info = data.face if isinstance(data, FaceDisplayItem) else None
        is_in_trash = bool(media.is_in_trash) if media else False
        is_duplicate = bool(media.group_id) if media else False

        # 2. Draw Background & Border
        self._draw_card_background(
            painter, rect, is_selected, is_hovered, is_duplicate, is_in_trash
        )

        img_rect = QRect(
            rect.left() + Metrics.MARGIN,
            rect.top() + Metrics.MARGIN,
            self.img_size.width(),
            self.img_size.height(),
        )
        thumb_source = media.thumbnail_path if media else data.image
        self._draw_thumbnail(painter, img_rect, thumb_source)

        # 4. Draw Metadata (Filename, Size, Date)
        meta_bottom = self._draw_metadata(painter, rect, img_rect, media, face_info)

        # 5. Draw Tags (Chips)
        if media and (tags_raw := getattr(media, "person_tags", None)):
            self._draw_tags(painter, rect, meta_bottom, tags_raw)

        # 6. Draw Checkbox & Badges
        self._draw_selection_checkbox(painter, rect, bool(data.selected))

        ui_group_id = getattr(data, "ui_group_id", None)
        if is_duplicate and ui_group_id:
            self._draw_group_badge(painter, img_rect, ui_group_id)

        painter.restore()

    def _draw_header(self, painter: QPainter, rect: QRect, data: LibraryViewHeader):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # Distinct background for the header row to create clear separation
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(Theme.HEADER_BG))
        painter.drawRect(rect)

        painter.setPen(QPen(Theme.TEXT_SECONDARY))
        painter.setFont(QFont("Inter", Metrics.HEADER_FONT_SIZE, QFont.Bold))

        if data.ui_group_id:
            header_text = f"Duplicate Group #{data.ui_group_id}"
        elif data.suggestion_label:
            header_text = data.suggestion_label
        else:
            loc = data.location_header or "Unknown Location"

            date = data.date_header or ""
            if date and date != "Unknown Date":
                # Format date: 2024-03-29 -> 2024/03/29
                fmt_date = date.replace("-", "/")
                header_text = f"{loc}  •  {fmt_date}"
            else:
                header_text = loc

        painter.setPen(QPen(Theme.ACCENT if data.ui_group_id else Theme.TEXT_SECONDARY))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, header_text)

        # Draw a prominent divider line
        line_y = rect.center().y()
        text_width = painter.fontMetrics().horizontalAdvance(header_text)
        painter.setPen(QPen(Theme.BORDER, 1))
        painter.drawLine(rect.left() + text_width + 25, line_y, rect.right() - 150, line_y)

        # "Select Group" Hint
        painter.setPen(QPen(Theme.ACCENT, 0.8))
        painter.setFont(QFont("Inter", 9, QFont.Medium))
        painter.drawText(
            rect.adjusted(0, 0, -120, 0),
            Qt.AlignVCenter | Qt.AlignRight,
            "Click to Select Group",
        )
        painter.restore()

    def _draw_card_background(
        self,
        painter: QPainter,
        rect: QRect,
        is_selected: bool,
        is_hovered: bool,
        is_duplicate: bool,
        is_in_trash: bool,
    ):
        is_survivor = not is_in_trash

        if is_selected:
            painter.setPen(QPen(Theme.ACCENT, 2))
            painter.setBrush(Theme.CARD_HOVER)
        elif is_survivor and is_duplicate:
            # Highlight non-trash survivors with a RED border in Duplicates view
            painter.setPen(QPen(Theme.DUPLICATE, 3))  # Bold red
            painter.setBrush(Theme.CARD_BG)
        elif is_hovered:
            painter.setPen(QPen(Theme.ACCENT, 1))
            painter.setBrush(Theme.CARD_HOVER)
        else:
            painter.setPen(QPen(Theme.BORDER, 1))
            painter.setBrush(Theme.CARD_BG)

        painter.drawRoundedRect(rect, Metrics.CORNER_RADIUS, Metrics.CORNER_RADIUS)

    def _draw_thumbnail(self, painter: QPainter, img_rect: QRect, source: Any):
        # 1. Background for image area
        painter.setBrush(Theme.BACKGROUND)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(img_rect, 4, 4)

        # 2. Draw Image (only if pre-loaded)
        pixmap = None
        if source:
            if isinstance(source, (QPixmap, QImage)):
                pixmap = QPixmap.fromImage(source) if isinstance(source, QImage) else source

            if pixmap and not pixmap.isNull():
                mode = Qt.KeepAspectRatioByExpanding if self.is_crop_mode else Qt.KeepAspectRatio
                scaled = pixmap.scaled(self.img_size, mode, Qt.SmoothTransformation)

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
            painter.setPen(QPen(Theme.TEXT_SECONDARY))
            painter.drawText(img_rect, Qt.AlignCenter, "No Image")

    def _draw_metadata(
        self,
        painter: QPainter,
        rect: QRect,
        img_rect: QRect,
        media: Optional[Any],
        face_info: Optional[Any],
    ) -> int:
        text_rect = QRect(
            rect.left() + Metrics.MARGIN, img_rect.bottom() + 5, self.img_size.width(), 20
        )
        painter.setPen(QPen(Theme.TEXT_PRIMARY))
        painter.setFont(QFont("Inter", 9, QFont.Bold))

        fname = os.path.basename(
            media.file_path if media else face_info.file_path if face_info else ""
        )
        elided = painter.fontMetrics().elidedText(fname, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignTop | Qt.AlignLeft, elided)

        meta_bottom = text_rect.bottom()
        if media and not self.is_crop_mode:
            meta = media.metadata or {}
            size_val = meta.get("size") or 0
            date_str = media.capture_date or ""

            size_text = (
                f"{size_val / (1024 * 1024):.1f} MB"
                if size_val > 1024 * 1024
                else f"{size_val / 1024:.1f} KB"
            )
            date_text = date_str.split(" ")[0].replace(":", "/") if date_str else "Unknown Date"

            painter.setPen(QPen(Theme.TEXT_SECONDARY))
            painter.setFont(QFont("Inter", 8))
            meta_rect = QRect(text_rect.left(), text_rect.bottom() + 2, text_rect.width(), 15)
            painter.drawText(meta_rect, Qt.AlignTop | Qt.AlignLeft, f"{size_text}  •  {date_text}")
            meta_bottom = meta_rect.bottom()

        return meta_bottom

    def _draw_tags(self, painter: QPainter, rect: QRect, top_y: int, tags_raw: str):
        tags = []
        for t in tags_raw.split(","):
            parts = t.split(":", 1)
            if len(parts) == 2:
                cid, name = parts
                if not name:
                    name = f"Person {cid}" if cid != "-1" else "Unknown"
                tags.append({"id": cid, "name": name})

        tags.sort(key=lambda x: x["name"])
        tag_x = rect.left() + Metrics.MARGIN
        tag_y = top_y + 5

        painter.setFont(QFont("Inter", 8, QFont.Medium))
        for tag in tags:
            name = tag["name"]
            tw = painter.fontMetrics().horizontalAdvance(name) + 12
            if tag_x + tw > rect.right() - Metrics.MARGIN:
                tag_x = rect.left() + Metrics.MARGIN
                tag_y += 20

            if tag_y + 18 > rect.bottom() - 5:
                break

            chip_rect = QRect(tag_x, tag_y, tw, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(Theme.CARD_HOVER))
            painter.drawRoundedRect(chip_rect, 4, 4)

            painter.setPen(QPen(Theme.TEXT_PRIMARY))
            painter.drawText(chip_rect, Qt.AlignCenter, name)
            tag_x += tw + 4

        # Add "+" Button
        plus_text = " ＋ "
        tw = painter.fontMetrics().horizontalAdvance(plus_text) + 12
        if tag_x + tw > rect.right() - Metrics.MARGIN:
            tag_x = rect.left() + Metrics.MARGIN
            tag_y += 20

        if tag_y + 18 <= rect.bottom() - 5:
            plus_rect = QRect(tag_x, tag_y, tw, 18)
            painter.setBrush(QBrush(Theme.CARD_BG))
            painter.setPen(QPen(Theme.ACCENT, 1, Qt.DashLine))
            painter.drawRoundedRect(plus_rect, 4, 4)
            painter.setPen(QPen(Theme.ACCENT))
            painter.drawText(plus_rect, Qt.AlignCenter, "＋")

    def _draw_selection_checkbox(self, painter: QPainter, rect: QRect, is_selected: bool):
        cb_rect = QRect(rect.left() + 10, rect.top() + 10, 20, 20)
        painter.setPen(QPen(Theme.TEXT_SECONDARY, 2))
        painter.setBrush(QBrush(Theme.CARD_BG))
        painter.drawRoundedRect(cb_rect, 4, 4)
        if is_selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(Theme.ACCENT))
            painter.drawRoundedRect(cb_rect.adjusted(3, 3, -3, -3), 2, 2)

    def _draw_group_badge(self, painter: QPainter, img_rect: QRect, group_id: Any):
        badge_text = f"GROUP #{group_id}"
        badge_rect = QRect(img_rect.right() - 75, img_rect.top() + 5, 70, 18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(Theme.DUPLICATE))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QPen(Theme.TEXT_PRIMARY))
        painter.setFont(QFont("Inter", 7, QFont.Bold))
        painter.drawText(badge_rect, Qt.AlignCenter, badge_text)

    def sizeHint(self, option, index):
        data = index.data(Qt.UserRole)
        if data and getattr(data, "is_header", False):
            # Ensure headers span the full width in IconMode
            view = self.parent()
            width = view.viewport().width() if view else 800
            return QSize(width - 10, Metrics.HEADER_HEIGHT)
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
        return sum(1 for item in self.media_model._data if item.selected)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.pos())
        if not index.isValid():
            super().mousePressEvent(event)
            return

        data = index.data(Qt.UserRole)
        if not data:
            super().mousePressEvent(event)
            return

        if data.is_header:
            self.media_model.select_contiguous_group(index.row())
            self.viewport().update()
            self.selection_changed.emit(self.get_selection_count())
            return

        file_path = data.file_path
        if event.button() == Qt.RightButton:
            self.context_menu_requested.emit(file_path, event.globalPos())
            return

        rect = self.visualRect(index)
        local_pos = event.pos() - rect.topLeft()

        if self._handle_checkbox_click(index, data, local_pos):
            return

        if self._handle_tag_and_plus_clicks(data, local_pos, event.globalPos()):
            return

        # Generic left-click: notify
        self.item_clicked.emit(file_path)
        super().mousePressEvent(event)

    def _handle_checkbox_click(self, index: QModelIndex, data: Any, local_pos: QPoint) -> bool:
        if 10 <= local_pos.x() <= 35 and 10 <= local_pos.y() <= 35:
            new_val = not data.selected
            self.media_model.setData(
                index, Qt.Checked if new_val else Qt.Unchecked, Qt.CheckStateRole
            )
            self.viewport().update()
            self.selection_changed.emit(self.get_selection_count())
            return True
        return False

    def _handle_tag_and_plus_clicks(self, data: Any, local_pos: QPoint, global_pos: QPoint) -> bool:
        tags_raw = getattr(data.media if hasattr(data, "media") else data, "person_tags", None)
        if not tags_raw:
            return False

        tags = self._parse_person_tags(tags_raw)
        # Calculate Y starting position for tags based on card layout
        # (Image height + Margin + Name height + date/size meta height)
        tag_y_start = Metrics.THUMB_SIZE + Metrics.MARGIN + 5 + 20 + 2 + 15 + 5
        tag_x, tag_y = Metrics.MARGIN + 5, tag_y_start

        metrics = QFontMetrics(QFont("Inter", 8, QFont.Medium))

        card_width = Metrics.CARD_WIDTH
        if getattr(self.delegate, "is_crop_mode", False):
            card_width = Metrics.CROP_CARD_WIDTH

        for t_item in tags:
            cid, name = t_item["id"], t_item["name"]
            tw = metrics.horizontalAdvance(name) + 12
            if tag_x + tw > card_width - Metrics.MARGIN:
                tag_x, tag_y = Metrics.MARGIN + 5, tag_y + 20

            if QRect(tag_x, tag_y, tw, 18).contains(local_pos):
                try:
                    self.tag_clicked.emit(data.file_path, int(cid), name)
                except ValueError:
                    self.tag_clicked.emit(data.file_path, -1, name)
                return True
            tag_x += tw + 4

        # Check for "+" Add Button Click
        plus_text = " ＋ "
        tw = metrics.horizontalAdvance(plus_text) + 12
        if tag_x + tw > card_width - Metrics.MARGIN:
            tag_x, tag_y = Metrics.MARGIN + 5, tag_y + 20

        if QRect(tag_x, tag_y, tw, 18).contains(local_pos):
            self.context_menu_requested.emit(data.file_path, global_pos)
            return True
        return False

    def _parse_person_tags(self, tags_raw: str) -> list[dict[str, str]]:
        tags = []
        for t in tags_raw.split(","):
            parts = t.split(":", 1)
            if len(parts) == 2:
                cid, name = parts
                if not name:
                    name = f"Person {cid}" if cid != "-1" else "Unknown"
                tags.append({"id": cid, "name": name})
        tags.sort(key=lambda x: x["name"])
        return tags

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            data = index.data(Qt.UserRole)
            file_path = data.file_path
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
            if not item.is_header and item.selected:
                selected.append(item.file_path)
        return selected

    def set_data(self, media_list):
        self.media_model.set_data(media_list)
