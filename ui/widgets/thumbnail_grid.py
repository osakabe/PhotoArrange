from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QSize, QAbstractListModel, QRect, QPoint, Signal, QModelIndex
from PySide6.QtGui import QPainter, QPixmap, QColor, QFont, QPen, QBrush
import os

class MediaModel(QAbstractListModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        row = index.row()
        if role == Qt.UserRole:
            return self._data[row]
        return None

    def set_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

    def append_data(self, additional_data):
        if not additional_data: return
        first = len(self._data)
        last = first + len(additional_data) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._data.extend(additional_data)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()

class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.card_size = QSize(180, 240)
        self.img_size = QSize(164, 164)
        self.margin = 8
        self.corner_radius = 8
        self.bg_color = QColor("#1A1D2E")
        self.border_color = QColor("#2D324A")
        self.hover_color = QColor("#24283D")
        self.accent_color = QColor("#3D5AFE")
        self.text_color = QColor("#8A8EA8")
        self.img_bg_color = QColor("#0F111A")

    def paint(self, painter, option, index):
        data = index.data(Qt.UserRole)
        if not data: return
        
        # Handle Header rendering
        if data.get("is_header"):
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            rect = option.rect
            painter.setPen(QPen(self.text_color))
            painter.setFont(QFont("Inter", 10, QFont.Bold))
            hash_text = f"Duplicate Group: {data['group_hash'][:8].upper()}"
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, hash_text)
            
            # Draw a line
            line_y = rect.center().y()
            text_width = painter.fontMetrics().horizontalAdvance(hash_text)
            painter.setPen(QPen(self.border_color, 1))
            painter.drawLine(rect.left() + text_width + 10, line_y, rect.right() - 10, line_y)
            painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        is_hovered = option.state & QStyle.State_MouseOver
        is_selected = option.state & QStyle.State_Selected
        rect = option.rect.adjusted(5, 5, -5, -5)
        if is_selected:
            painter.setPen(QPen(self.accent_color, 2))
            painter.setBrush(self.hover_color)
        elif is_hovered:
            painter.setPen(QPen(self.accent_color, 1))
            painter.setBrush(self.hover_color)
        else:
            painter.setPen(QPen(self.border_color, 1))
            painter.setBrush(self.bg_color)
        painter.drawRoundedRect(rect, self.corner_radius, self.corner_radius)
        img_rect = QRect(rect.left() + self.margin, rect.top() + self.margin, self.img_size.width(), self.img_size.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.img_bg_color)
        painter.drawRoundedRect(img_rect, 4, 4)
        thumb_path = data.get("thumbnail_path")
        if thumb_path and os.path.exists(thumb_path):
            pixmap = QPixmap(thumb_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(self.img_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                px = img_rect.left() + (img_rect.width() - scaled.width()) // 2
                py = img_rect.top() + (img_rect.height() - scaled.height()) // 2
                painter.drawPixmap(px, py, scaled)
        else:
            painter.setPen(QPen(self.text_color))
            painter.drawText(img_rect, Qt.AlignCenter, "No Image")
        text_rect = QRect(rect.left() + self.margin, img_rect.bottom() + 5, self.img_size.width(), 20)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.setFont(QFont("Inter", 9, QFont.Bold))
        filename = os.path.basename(data.get("file_path", ""))
        elided = painter.fontMetrics().elidedText(filename, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignTop | Qt.AlignLeft, elided)
        meta = data.get("metadata", {})
        size_val = meta.get("size") or 0
        date_str = meta.get("date_taken") or ""
        size_text = f"{size_val / (1024*1024):.1f} MB" if size_val > 1024*1024 else f"{size_val/1024:.1f} KB"
        date_text = date_str.split(' ')[0].replace(':', '/') if date_str else "Unknown Date"
        painter.setPen(QPen(self.text_color))
        painter.setFont(QFont("Inter", 8))
        meta_rect = QRect(text_rect.left(), text_rect.bottom() + 2, text_rect.width(), 15)
        painter.drawText(meta_rect, Qt.AlignTop | Qt.AlignLeft, f"{size_text}  •  {date_text}")
        if data.get("group_hash"):
            badge_text = f"DUP: {data['group_hash'][:4].upper()}"
            badge_rect = QRect(img_rect.right() - 60, img_rect.top() + 5, 55, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#F44336")))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.setFont(QFont("Inter", 7, QFont.Bold))
            painter.drawText(badge_rect, Qt.AlignCenter, badge_text)
        painter.restore()

    def sizeHint(self, option, index):
        data = index.data(Qt.UserRole)
        if data and data.get("is_header"):
            return QSize(option.rect.width(), 40)
        return self.card_size + QSize(10, 10)

class ThumbnailGrid(QListView):
    item_clicked = Signal(str)
    request_more_data = Signal()

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
        self.verticalScrollBar().valueChanged.connect(self.check_scroll)

    def check_scroll(self, value):
        max_scroll = self.verticalScrollBar().maximum()
        if max_scroll > 0 and value >= max_scroll * 0.9:
            self.request_more_data.emit()

    def on_clicked(self, index):
        data = index.data(Qt.UserRole)
        if data: self.item_clicked.emit(data["file_path"])

    def set_data(self, media_list): self.media_model.set_data(media_list)
    def append_data(self, media_list): self.media_model.append_data(media_list)
    def clear(self): self.media_model.clear()
