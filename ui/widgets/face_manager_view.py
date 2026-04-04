import os
import json
import logging
import cv2
import numpy as np
import time
from PIL import Image
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, 
    QListView, QStyledItemDelegate, QPushButton, QMenu, QMessageBox, 
    QApplication, QSplitter, QSizePolicy, QProgressBar, QStyle, 
    QTreeWidget, QTreeWidgetItem
)
from PySide6.QtCore import (
    Qt, QSize, QThread, Signal, QRect, QPoint, Slot, QTimer, 
    QAbstractListModel, QModelIndex, QEvent, QObject
)
from PySide6.QtGui import (
    QPixmap, QImage, QAction, QColor, QPainter, QPen, QBrush, QFont
)

from core.utils import get_face_cache_dir, get_short_path_name
from processor.image_processor import ImageProcessor
from processor.person_logic import PersonManagementWorker, PersonAction
from processor.suggestion_logic import FaceSuggestionWorker

logger = logging.getLogger("PhotoArrange")

class FaceLoadWorker(QThread):
    """
    Worker thread to load faces from database and generate crops if missing.
    Batches results to avoid UI thread saturation.
    """
    faces_loaded = Signal(int, list) # cid, list of (face_info, qimage)
    has_more_available = Signal(bool)
    finished = Signal()

    def __init__(self, db, category_id, limit=200, after_date=None, after_id=None, specific_date=None):
        super().__init__()
        self.db = db
        self.category_id = category_id # -1: Unknown, -2: Ignored, else: cluster_id
        self.limit = limit
        self.after_date = after_date
        self.after_id = after_id
        self.specific_date = specific_date
        self.is_running = True
        self.cache_dir = get_face_cache_dir()

    def stop(self):
        self.is_running = False

    def run(self):
        worker_start = time.perf_counter()
        logger.info(f"FaceLoadWorker(cat={self.category_id}, after={self.after_date}/{self.after_id}) starting...")
        try:
            faces = []
            if self.category_id == -1: # Unknown
                faces = self.db.get_faces_by_category('unknown', limit=self.limit, after_date=self.after_date, after_id=self.after_id, specific_date=self.specific_date)
            elif self.category_id == -2: # Ignored
                faces = self.db.get_faces_by_category('ignored', limit=self.limit, after_date=self.after_date, after_id=self.after_id, specific_date=self.specific_date)
            else: # Specific Cluster
                faces = self.db.get_faces_by_category('person', person_id=self.category_id, limit=self.limit, after_date=self.after_date, after_id=self.after_id, specific_date=self.specific_date)

            fetch_duration = time.perf_counter() - worker_start
            logger.info(f"DB Fetch took {fetch_duration:.4f}s for {len(faces)} faces.")

            # Detect if there's possibly more data
            has_more = len(faces) >= self.limit
            self.has_more_available.emit(has_more)

            batch = []
            batch_size = 100 
            batch_count = 0

            for f in faces:
                if not self.is_running: break
                
                # Check for cache existence (fast)
                face_id = f["face_id"]
                cache_path = os.path.join(self.cache_dir, f"face_{face_id}.jpg")
                
                qimg = None
                exists = os.path.exists(cache_path)
                
                if exists:
                    # We MUST check if it can actually be loaded as an image, not just file size (FACT-BASED FIX)
                    qimg = QImage(cache_path)
                    null = qimg.isNull()
                    size = os.path.getsize(cache_path) if exists else 0
                    logger.info(f"FACT_CHECK: FaceID={face_id} Path={cache_path} Exists={exists} Size={size} isNull={null}")
                    
                    if null:
                        logger.warning(f"FaceLoadWorker: Cache corrupted for face_{face_id} (exists but unreadable). Forcing regeneration.")
                        f["needs_crop"] = True
                        qimg = None
                    else:
                        # Success: use existing cache
                        pass
                else:
                    logger.info(f"FACT_CHECK: FaceID={face_id} MISSING at {cache_path}")
                    f["needs_crop"] = True

                batch.append((f, qimg))
                
                if len(batch) >= batch_size:
                    batch_count += 1
                    b_start = time.perf_counter()
                    self.faces_loaded.emit(self.category_id, batch)
                    batch_duration = time.perf_counter() - b_start
                    logger.info(f"Batch {batch_count} emission took {batch_duration:.4f}s")
                    batch = []
                    self.msleep(5) 
            
            if batch and self.is_running:
                self.faces_loaded.emit(self.category_id, batch)
            
            total_duration = time.perf_counter() - worker_start
            logger.info(f"FaceLoadWorker(cat={self.category_id}) Total processing finished in {total_duration:.4f}s")
            self.finished.emit()
        except Exception as e:
            logger.exception(f"FaceLoadWorker Error: {e}")
            self.finished.emit()

    def get_or_generate_crop(self, face_data):
        """Generates a face crop from the original image/video frame."""
        face_id = face_data["face_id"]
        cache_path = os.path.join(self.cache_dir, f"face_{face_id}.jpg")
        
        file_path = face_data["file_path"]
        bbox = face_data["bbox"]
        frame_idx = face_data.get("frame_index", 0)
        
        if not bbox or not os.path.exists(file_path):
            return None

        try:
            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
            if is_video:
                short_path = get_short_path_name(file_path)
                cap = cv2.VideoCapture(short_path)
                if not cap.isOpened(): return None
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
                success, frame = cap.read()
                cap.release()
                if not success: return None
                img_cv = frame
            else:
                try:
                    with Image.open(file_path) as pil_img:
                        from PIL import ImageOps
                        pil_img = ImageOps.exif_transpose(pil_img)
                        img_cv = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2BGR)
                except:
                    # Fallback to cv2 if PIL fails
                    img_cv = cv2.imread(get_short_path_name(file_path))
                    if img_cv is None: return None

            ih, iw = img_cv.shape[:2]
            x1, y1, x2, y2 = bbox
            w, h = x2 - x1, y2 - y1
            
            # Pad the crop slightly (30%)
            x1 = max(0, x1 - w * 0.3)
            y1 = max(0, y1 - h * 0.3)
            x2 = min(iw, x2 + w * 0.3)
            y2 = min(ih, y2 + h * 0.3)
            
            crop = img_cv[int(y1):int(y2), int(x1):int(x2)]
            if crop.size == 0: return None
            
            crop = cv2.resize(crop, (150, 150), interpolation=cv2.INTER_AREA)
            success, buffer = cv2.imencode('.jpg', crop)
            if success:
                buffer.tofile(cache_path)
                return cache_path
            return None
        except Exception as e:
            logger.error(f"Failed to generate face crop for {face_id}: {e}")
            return None

class FaceCropWorker(QThread):
    """Generates face crops on-demand for items missing images."""
    images_ready = Signal(list) # list of (face_id, qimage)
    image_failed = Signal(int)  # face_id
    finished_batch = Signal()

    def __init__(self, db, items, manager=None):
        super().__init__()
        self.db = db
        self.items = items
        self.manager = manager # Optional reference
        self.cache_dir = get_face_cache_dir()
        self.is_running = True
        self.img_proc = ImageProcessor()

    def stop(self):
        self.is_running = False

    def run(self):
        worker_id = id(self)
        start_ts = time.perf_counter()
        logger.info(f"FaceCropWorker[{worker_id}] starting background task for {len(self.items)} items.")
        
        try:
            batch_results = []
            for item in self.items:
                if not self.is_running: break
                
                face_id = item.get("face_id")
                cache_path = os.path.join(self.cache_dir, f"face_{face_id}.jpg")
                
                # REGENERATION LOGIC (v2.3 Fix): Invalidate 0-byte or very small corrupted files
                needs_regen = not os.path.exists(cache_path) or os.path.getsize(cache_path) < 1024
                
                if needs_regen:
                    self._generate_single_crop(item, cache_path)
                
                if os.path.exists(cache_path):
                    qimg = QImage(cache_path)
                    if not qimg.isNull():
                        batch_results.append((face_id, qimg))
                    else:
                        logger.error(f"FaceCropWorker: Cache file at {cache_path} is invalid.")
                        self.image_failed.emit(face_id)
                else:
                    logger.error(f"FaceCropWorker: Failed to generate crop for {face_id}")
                    self.image_failed.emit(face_id)
                
                # Batch emission every 20 items
                if len(batch_results) >= 20:
                    self.images_ready.emit(batch_results)
                    batch_results = []
                    self.msleep(5) # Yield
            
            # Final batch
            if batch_results:
                self.images_ready.emit(batch_results)
            
            duration = time.perf_counter() - start_ts
            logger.info(f"FaceCropWorker[{worker_id}] finished in {duration:.4f}s for {len(self.items)} items")
            self.finished_batch.emit()
        except Exception as e:
            logger.exception(f"FaceCropWorker Error: {e}")

    def _generate_single_crop(self, face_data, cache_path):
        try:
            raw_path = face_data.get("file_path")
            if not raw_path: return
            file_path = os.path.normpath(os.path.normcase(raw_path))
            
            bbox = face_data.get("bbox")
            if not bbox or not file_path: return
            
            source_img = None
            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
            
            if is_video:
                # MANDATORY: Video extraction must use the EXACT frame_idx (v2.1 Fix)
                frame_idx = face_data.get("frame_index", 0)
                short_path = get_short_path_name(file_path)
                cap = cv2.VideoCapture(short_path)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
                    ret, frame = cap.read()
                    cap.release()
                    if ret: 
                        source_img = frame
                
                # FINAL FALLBACK FOR VIDEOS: Try static thumbnail if specific frame extraction fails
                if source_img is None:
                    thumb_path = self.img_proc.get_thumbnail_path(file_path)
                    if os.path.exists(thumb_path):
                        with open(thumb_path, 'rb') as f:
                            buf = np.frombuffer(f.read(), dtype=np.uint8)
                            source_img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            else:
                # IMAGES: Robust Windows Path Loading (Unicode support)
                try:
                    with open(file_path, 'rb') as f:
                        buf = np.frombuffer(f.read(), dtype=np.uint8)
                        source_img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                except Exception as e:
                    logger.error(f"FaceCropWorker: Decode error for {file_path}: {e}")

            if source_img is None:
                logger.error(f"FaceCropWorker: Source image is None for {file_path}")
                return

            ih, iw = source_img.shape[:2]
            x1, y1, x2, y2 = bbox
            
            # Add subtle padding (30%) to make the face easier to see
            w, h = x2 - x1, y2 - y1
            x1_p, y1_p = max(0, x1 - w * 0.3), max(0, y1 - h * 0.3)
            x2_p, y2_p = min(iw, x2 + w * 0.3), min(ih, y2 + h * 0.3)
            
            crop = source_img[int(y1_p):int(y2_p), int(x1_p):int(x2_p)]
            if crop.size == 0: 
                # Fallback to un-padded if padding was out of bounds
                crop = source_img[int(y1):int(y2), int(x1):int(x2)]
                if crop.size == 0: return

            crop = cv2.resize(crop, (150, 150), interpolation=cv2.INTER_AREA)
            _, buffer = cv2.imencode('.jpg', crop)
            buffer.tofile(cache_path)
        except Exception as e:
            logger.error(f"Error in _generate_single_crop for face {face_data.get('face_id')}: {e}")

class FaceCropManager(QObject):
    """
    Centralized Rendering Engine for large-scale libraries (v2.2 Overhaul).
    Manages a global FIFO queue and coordinates worker threads to prevent I/O saturation.
    """
    _instance = None
    images_ready = Signal(list)
    image_failed = Signal(int)
    queue_updated = Signal(int) # remaining count

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            if db is None: raise ValueError("DB required for first init")
            cls._instance = cls(db)
        return cls._instance

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.queue = []
        self.processing_ids = set()
        self.active_workers = []
        self.max_parallel = 3 # Optimal for balanced I/O
        self.timer = QTimer()
        self.timer.timeout.connect(self._process_queue)
        self.timer.start(100) # Check queue every 100ms

    def enqueue_items(self, items):
        """Adds new face crop requests to the global queue."""
        added = 0
        for item in items:
            fid = item.get("face_id")
            if fid not in self.processing_ids:
                self.queue.append(item)
                self.processing_ids.add(fid)
                added += 1
        if added > 0:
            self.queue_updated.emit(len(self.queue))
            logger.info(f"FaceCropManager: Enqueued {added} items. Total queue: {len(self.queue)}")

    def _process_queue(self):
        # Cleanup finished workers
        self.active_workers = [w for w in self.active_workers if w.isRunning()]
        
        if not self.queue or len(self.active_workers) >= self.max_parallel:
            return
            
        # Take a chunk of work
        chunk_size = 50
        chunk = self.queue[:chunk_size]
        self.queue = self.queue[chunk_size:]
        self.queue_updated.emit(len(self.queue))
        
        worker = FaceCropWorker(self.db, chunk, manager=self)
        worker.images_ready.connect(self.images_ready)
        worker.image_failed.connect(self.image_failed)
        worker.finished.connect(lambda: self._on_worker_finished(chunk))
        self.active_workers.append(worker)
        worker.start()

    def _on_worker_finished(self, chunk):
        for item in chunk:
            fid = item.get("face_id")
            if fid in self.processing_ids:
                self.processing_ids.remove(fid)
        self.queue_updated.emit(len(self.queue))

class SidebarLoadWorker(QThread):
    """Loads top-level sidebar counts and person list for instant UI initialization."""
    data_loaded = Signal(dict, list) # counts, persons

    def __init__(self, db):
        super().__init__()
        self.db = db

    def run(self):
        logger.info("SidebarLoadWorker: Starting run() [Instant Mode]")
        try:
            # 1. Base counts for unknowns/ignored
            logger.info("SidebarLoadWorker: Fetching face counts...")
            counts = self.db.get_face_counts()

            # 2. Persons list
            logger.info("SidebarLoadWorker: Fetching person list...")
            persons = self.db.get_person_list_with_counts()
            
            logger.info("SidebarLoadWorker: Emitting top-level data_loaded signal")
            self.data_loaded.emit(counts, persons)
        except Exception as e:
            import traceback
            logger.error(f"SidebarLoadWorker ERROR: {e}\n{traceback.format_exc()}")
            self.data_loaded.emit({}, [])

class PersonDateLoadWorker(QThread):
    """Async loader for a specific person's or category's dates when expanded."""
    dates_loaded = Signal(QTreeWidgetItem, list) # (node_item, date_list)

    def __init__(self, db, item, cid):
        super().__init__()
        self.db = db
        self.item = item
        self.cid = cid # -1: Unknown, -2: Ignored, or PersonID

    def run(self):
        logger.info(f"PersonDateLoadWorker: Loading dates for CID {self.cid}")
        try:
            category = 'person'
            pid = self.cid
            if self.cid == -1: 
                category = 'unknown'
                pid = None
            elif self.cid == -2: 
                category = 'ignored'
                pid = None
            
            dates = self.db.get_face_dates_by_category(category, pid)
            logger.info(f"PersonDateLoadWorker: Found {len(dates)} dates for CID {self.cid}")
            self.dates_loaded.emit(self.item, dates)
        except Exception as e:
            logger.error(f"PersonDateLoadWorker ERROR: {e}")
            self.dates_loaded.emit(self.item, [])

class FaceModel(QAbstractListModel):
    """Memory-efficient model for face data and headers."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._data)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        row = index.row()
        if row >= len(self._data): return None
        
        if role == Qt.UserRole:
            return self._data[row]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid(): return False
        if role == Qt.UserRole:
            self._data[index.row()] = value
            self.dataChanged.emit(index, index, [Qt.UserRole])
            return True
        return False

    def update_image_data(self, face_id, qimage):
        """Updates a face record with its cropped image pixmap without reloading everything."""
        for i in range(len(self._data)):
            item = self._data[i]
            if not item.get("is_header") and item.get("face_id") == face_id:
                item["qimage"] = qimage
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [Qt.UserRole, Qt.DecorationRole])
                break

    def mark_image_failed(self, face_id):
        """Stops the retry animation for items that cannot be loaded."""
        for i in range(len(self._data)):
            item = self._data[i]
            if not item.get("is_header") and item.get("face_id") == face_id:
                item["needs_crop"] = False
                item["failed"] = True
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [Qt.UserRole, Qt.DecorationRole])
                break

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

    def select_all_in_date_range(self, date_key):
        """Selects all items belonging to a specific date header with precision signaling."""
        in_group = False
        min_row = -1
        max_row = -1
        
        for i in range(len(self._data)):
            item = self._data[i]
            if item.get("is_header"):
                in_group = (item.get("date_header") == date_key)
                if in_group:
                    min_row = i if min_row == -1 else min_row
                continue
            
            if in_group:
                item["selected"] = True
                min_row = i if min_row == -1 else min_row
                max_row = i
        
        # Precision signal for stability and performance
        if min_row != -1 and max_row != -1:
            self.dataChanged.emit(self.index(min_row, 0), self.index(max_row, 0), [Qt.UserRole])

    def get_selection_count(self):
        return sum(1 for item in self._data if not item.get("is_header") and item.get("selected"))

class FaceDelegate(QStyledItemDelegate):
    """Custom painter for face items and date headers."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.item_size = QSize(140, 160)
        self.img_size = QSize(130, 130)
        # Modern Palette: Sleek Dark / Glass-inspired
        # Modern Palette: Sleek Dark / Library-Compliant
        self.bg_color = QColor("#121421")           # Deeper background
        self.card_bg = QColor("#1A1D2E")            # Card background
        self.header_bg = QColor("#1F2336")          # Library header standard
        self.border_color = QColor("#2D324A")       # Standard border
        self.accent_color = QColor("#3D5AFE")       # Vibrant Blue
        self.accent_glow = QColor(61, 90, 254, 100) # Increased alpha for visibility
        self.text_muted = QColor("#94A3B8")         # Slate muted text
        self.text_header = QColor("#F1F5F9")        # Primary light text
        self.separator_color = QColor("#2D324A")
        self._pixmap_cache = {}

    def paint(self, painter, option, index):
        try:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            
            data = index.data(Qt.UserRole)
            if not data:
                painter.restore()
                return

            rect = option.rect
            if data.get("is_header"):
                # Milestone 2: Exact Library-Style Header
                # 1. Background
                painter.setBrush(QBrush(self.header_bg)) 
                painter.setPen(Qt.NoPen)
                painter.drawRect(rect)
                
                # 2. Left Accent Vertical Bar (5px width)
                accent_bar = QRect(rect.left(), rect.top() + 15, 5, rect.height() - 30)
                painter.setBrush(QBrush(self.accent_color))
                painter.drawRoundedRect(accent_bar, 2, 2)
                
                # 3. Text Styling & Dynamic Divider Line
                painter.setPen(QPen(self.text_header))
                font = QFont("Inter", 12, QFont.Bold)
                painter.setFont(font)
                header_text = data.get("date_header", "不明な日付")
                
                # Calculate text width for the divider
                metrics = painter.fontMetrics()
                tw = metrics.horizontalAdvance(header_text)
                painter.drawText(rect.adjusted(30, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, header_text)
                
                # Draw divider line (ThumbnailGrid style)
                line_y = rect.center().y()
                painter.setPen(QPen(self.separator_color, 1))
                painter.drawLine(rect.left() + tw + 45, line_y, rect.right() - 180, line_y)
                
                # Top/Bottom Boundaries
                painter.setPen(QPen(self.separator_color, 1))
                painter.drawLine(rect.topLeft(), rect.topRight())
                painter.drawLine(rect.bottomLeft(), rect.bottomRight())
                
                # 4. "Select This Day" Button UI (Sophisticated Glassmorphism)
                btn_rect = QRect(rect.right() - 160, rect.top() + (rect.height() - 34)//2, 140, 34)
                is_btn_hover = bool(option.state & QStyle.State_MouseOver)
                
                painter.setBrush(QBrush(QColor(61, 90, 254, 50) if is_btn_hover else QColor(30, 30, 50, 100)))
                painter.setPen(QPen(self.accent_color, 1.5))
                painter.drawRoundedRect(btn_rect, 17, 17) # Capsule style
                
                painter.setPen(QPen(QColor("#FFFFFF")))
                painter.setFont(QFont("Inter", 8, QFont.Bold))
                painter.drawText(btn_rect, Qt.AlignCenter, "この日を全選択")
                painter.restore()
                return

            is_hovered = bool(option.state & QStyle.State_MouseOver)
            is_selected = data.get("selected", False)
            rect = option.rect.adjusted(4, 4, -4, -4)
            
            # Avoid invalid rects
            if rect.width() <= 0 or rect.height() <= 0:
                painter.restore()
                return
            
            # Draw Border & Background
            painter.setBrush(QBrush(self.card_bg if not (is_selected or is_hovered) else QColor("#24293E")))
            pen = QPen(self.accent_color if (is_selected or is_hovered) else self.border_color, 4 if is_selected else 1)
            painter.setPen(pen)
            painter.drawRoundedRect(rect, 10, 10)
            
            if is_selected:
                # Stronger selection overlay
                painter.setBrush(QBrush(self.accent_glow))
                painter.drawRoundedRect(rect, 10, 10)

            # Draw Image area
            img_rect = QRect(rect.left() + 5, rect.top() + 5, self.img_size.width(), self.img_size.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#0F111A")))
            painter.drawRoundedRect(img_rect, 4, 4)
            
            # Access pre-loaded image (QImage to QPixmap conversion is fast but we still use cache)
            face_id = data.get("face_id")
            pix = self._pixmap_cache.get(face_id)
            qimg = data.get("qimage")
            
            # Cache invalidation: If model has an image but cache doesn't (or it's different)
            if qimg and not qimg.isNull():
                if pix is None:
                    pix = QPixmap.fromImage(qimg)
                    if len(self._pixmap_cache) > 2000: self._pixmap_cache.clear()
                    self._pixmap_cache[face_id] = pix
            
            if pix:
                painter.drawPixmap(img_rect, pix)
            else:
                # Milestone 3: Live Pulsing Placeholder for missing crops
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(30, 35, 55, 200))) # Darker stylized grey
                painter.drawRoundedRect(img_rect, 4, 4)
                
                # Pulse Calculation (Subtle 1.0s period)
                try:
                    import time
                    pulse = (np.sin(time.time() * 6.28) + 1) / 2 # 0.0 to 1.0 range
                except:
                    pulse = 0.5
                
                is_failed = data.get("failed", False)
                symbol = "🚫" if is_failed else "⌛"
                label = "読込不可" if is_failed else "解析中..."
                
                # Draw Placeholder UI (Icon + Text)
                painter.setPen(QPen(QColor("#FF4B2B") if is_failed else self.accent_color, 1.5))
                painter.setOpacity(0.4 + (pulse * 0.6) if not is_failed else 0.8)
                painter.setFont(QFont("Inter", 14))
                painter.drawText(img_rect.adjusted(0, -20, 0, 0), Qt.AlignCenter, symbol)
                
                painter.setOpacity(1.0)
                painter.setPen(QPen(self.text_muted))
                painter.setFont(QFont("Inter", 8, QFont.Medium))
                painter.drawText(img_rect.adjusted(0, 30, 0, 0), Qt.AlignCenter, label)

            # Draw Capture Date / ID label
            date_str = data.get("capture_date", "日付不明")
            face_id = data.get("face_id", "?")
            display_text = f"ID: {face_id} | {date_str[:10].replace(':', '/')}"
            painter.setPen(QPen(self.text_muted))
            painter.setFont(QFont("Inter", 8, QFont.Medium))
            painter.drawText(QRect(rect.left(), img_rect.bottom() + 5, rect.width(), 20), Qt.AlignCenter, display_text)
            
            painter.restore()
        except Exception as e:
            # Safe recovery in paint
            if painter:
                try: painter.restore()
                except: pass

    def sizeHint(self, option, index):
        data = index.data(Qt.UserRole)
        if data and data.get("is_header"):
            # Library Standard Height (Milestone 2)
            # FIX: Window-Crossing / Full Width dynamic calculation
            view = self.parent()
            if view:
                # We subtract a small amount for the scrollbar/margins
                return QSize(view.viewport().width() - 20, 80)
            return QSize(800, 80)
        return self.item_size + QSize(10, 10)

class FaceManagerView(QWidget):
    """Main view for face management with sidebar and virtualized list."""
    refresh_requested = Signal()
    load_next_page = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_category_id = None
        self.current_date = None
        self.page_size = 500
        self.current_offset = 0
        self.last_item_date = None
        self.last_item_id = None
        self.is_suggestion_mode = False
        self.suggestion_worker = None
        self.target_person_id = None
        self.load_count = 0
        self.is_loading = False
        self.all_loaded = False
        self.last_date_key = None
        self.active_workers = [] # Engine Connectivity (v2.2 Overhaul)
        self.init_ui()
        
        self.render_engine = FaceCropManager.get_instance(self.db)
        self.render_engine.images_ready.connect(self._on_images_batch_ready)
        self.render_engine.image_failed.connect(self.face_model.mark_image_failed)
        self.render_engine.queue_updated.connect(self._update_engine_status)
        self.load_next_page.connect(self._trigger_load_next_chunk)
        
        # Ensure data is ready on first show (Milestone 1)
        # We use a single-shot timer to avoid blocking during view construction
        QTimer.singleShot(500, self.refresh_sidebar)
        
        # Animation Timer (Milestone 3)
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(lambda: self.list_view.viewport().update())
        self.animation_timer.start(100) # 10 FPS is enough for subtle pulsing

    def _update_engine_status(self, count):
        if count > 0:
            self.status_label.setText(f"⚙️ キュー詰まり: 残り {count} 枚")
        else:
            self.status_label.setText("✅ 処理完了")

    def _track_worker(self, worker):
        """Maintains a reference to the worker until it finishes to prevent crash 0xC0000409."""
        logger.info(f"Tracking worker: {type(worker).__name__} (ID: {id(worker)})")
        if worker not in self.active_workers:
            self.active_workers.append(worker)
            worker.finished.connect(lambda: self._cleanup_worker(worker))
            # Some workers use specific finished signals
            if hasattr(worker, 'finished_batch'):
                worker.finished_batch.connect(lambda: self._cleanup_worker(worker))
        return worker

    def _cleanup_worker(self, worker):
        """Safely removes worker from tracking list after completion."""
        try:
            if worker in self.active_workers:
                self.active_workers.remove(worker)
                logger.info(f"Worker tracking cleanup: {type(worker).__name__} (ID: {id(worker)})")
        except:
            pass

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        
        # Sidebar Panel (Tree Mode)
        sidebar_panel = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_panel)
        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setIndentation(15)
        self.sidebar.setStyleSheet("""
            QTreeWidget { background-color: #1A1D2E; color: #A0AEC0; border: none; }
            QTreeWidget::item { height: 32px; }
            QTreeWidget::item:selected { background-color: #3D5AFE; color: white; }
        """)
        self.sidebar.itemClicked.connect(self.on_sidebar_item_clicked)
        self.sidebar.itemExpanded.connect(self.on_sidebar_item_expanded)
        self.sidebar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self.show_sidebar_menu)
        sidebar_layout.addWidget(self.sidebar)
        self.btn_refresh_sidebar = QPushButton("🔄 更新")
        self.btn_refresh_sidebar.clicked.connect(self.refresh_sidebar)
        sidebar_layout.addWidget(self.btn_refresh_sidebar)
        self.splitter.addWidget(sidebar_panel)

        # Right Panel Area
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Toolbar for Person/Manual Actions (v2.3)
        self.toolbar = QWidget()
        t_layout = QHBoxLayout(self.toolbar)
        t_layout.setContentsMargins(10, 5, 10, 5)
        
        self.suggestion_btn = QPushButton("🔎 AI提案を表示")
        self.suggestion_btn.setCheckable(True)
        self.suggestion_btn.clicked.connect(self.toggle_suggestion_mode)
        
        self.select_all_btn = QPushButton("全て選択")
        self.deselect_all_btn = QPushButton("選択解除")
        self.select_all_btn.clicked.connect(self._select_all_current)
        self.deselect_all_btn.clicked.connect(self._deselect_all_current)
        
        # Action Buttons
        self.confirm_btn = QPushButton("この人物に確定")
        self.new_person_btn = QPushButton("新規人物")
        self.other_person_btn = QPushButton("別の登録人物")
        self.ignore_btn = QPushButton("無視/削除")
        
        self.confirm_btn.clicked.connect(self._on_confirm_selection)
        self.new_person_btn.clicked.connect(self._on_new_selection)
        self.other_person_btn.clicked.connect(self._on_other_person_selection)
        self.ignore_btn.clicked.connect(self._on_ignore_selection)
        
        # Styling Action Buttons
        self.confirm_btn.setStyleSheet("background: #00C853; color: white; border-radius: 4px; padding: 4px 10px;")
        self.new_person_btn.setStyleSheet("background: #2979FF; color: white; border-radius: 4px; padding: 4px 10px;")
        
        t_layout.addWidget(self.suggestion_btn)
        t_layout.addSpacing(20)
        t_layout.addWidget(self.select_all_btn)
        t_layout.addWidget(self.deselect_all_btn)
        t_layout.addStretch()
        t_layout.addWidget(self.confirm_btn)
        t_layout.addWidget(self.new_person_btn)
        t_layout.addWidget(self.ignore_btn)
        
        self.toolbar.setVisible(False)
        
        # Virtualized Grid View Performance Configuration
        self.list_view = QListView()
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setMovement(QListView.Static)
        self.list_view.setSpacing(12)
        # Disable fixed gridSize to allow sizeHint to determine header vs item height
        self.list_view.setUniformItemSizes(False) 
        self.list_view.setLayoutMode(QListView.Batched)
        self.list_view.setBatchSize(100)
        self.list_view.setStyleSheet("background-color: #0F111A; border: none; outline: none;")
        
        self.face_model = FaceModel(self)
        self.list_view.setModel(self.face_model)
        self.face_delegate = FaceDelegate(self.list_view)
        self.list_view.setItemDelegate(self.face_delegate)
        
        vp = self.list_view.viewport()
        if vp:
            vp.installEventFilter(self)
        self.list_view.verticalScrollBar().valueChanged.connect(self.on_scroll_moved)
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.list_view)
        
        self.splitter.addWidget(right_panel)
        main_layout.addWidget(self.splitter)

        self.loading_bar = QProgressBar()
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(2)
        self.loading_bar.setStyleSheet("QProgressBar { background: transparent; border: none; } QProgressBar::chunk { background: #3d5afe; }")
        
        # Engine Status Label (v2.2)
        self.status_label = QPushButton("✅ エンジン待機中")
        self.status_label.setFlat(True)
        self.status_label.setStyleSheet("color: #8899AA; font-size: 10px; border: none; margin-right: 15px;")
        
        self.loading_bar.setVisible(False)
        t_layout.addWidget(self.status_label)
        t_layout.addWidget(self.loading_bar)
        
        # Note: Sidebar refresh is triggered by MainWindow on transition.

    def eventFilter(self, source, event):
        if source is self.list_view.viewport() and event.type() == QEvent.MouseButtonPress:
            index = self.list_view.indexAt(event.pos())
            if index.isValid():
                data = index.data(Qt.UserRole)
                if not data: return False
                
                if data.get("is_header"):
                    # Click on Select All area in header
                    rect = self.list_view.visualRect(index)
                    if rect.right() - 140 <= event.pos().x() <= rect.right() - 20:
                        self.face_model.select_all_in_date_range(data.get("date_header"))
                        self.update_bulk_buttons()
                        return True
                else:
                    if event.button() == Qt.LeftButton:
                        new_data = data.copy()
                        new_data["selected"] = not data.get("selected", False)
                        self.face_model.setData(index, new_data, Qt.UserRole)
                        self.update_bulk_buttons()
                        return True
                    elif event.button() == Qt.RightButton:
                        self.show_face_menu(data["face_id"], event.globalPos())
                        return True
        return super().eventFilter(source, event)

    def showEvent(self, event):
        """Force a refresh when the view becomes visible to the user."""
        super().showEvent(event)
        logger.info("FaceManagerView: showEvent triggered")
        self.refresh_sidebar()

    def refresh_sidebar(self):
        logger.info("FaceManagerView: refresh_sidebar() called")
        # Check if we already have a running SidebarLoadWorker
        for w in self.active_workers:
            if isinstance(w, SidebarLoadWorker) and w.isRunning():
                logger.info("SidebarLoadWorker is already running. Skipping...")
                return

        # Immediate UI Feedback: show placeholder items
        self.sidebar.clear()
        self.sidebar.addTopLevelItem(QTreeWidgetItem(["⌛ 読み込み中..."]))
        
        worker = SidebarLoadWorker(self.db)
        worker.data_loaded.connect(self.on_sidebar_loaded)
        self._track_worker(worker)
        worker.start()
        logger.info(f"FaceManagerView: SidebarLoadWorker started (ID: {id(worker)})")

    @Slot(dict, list)
    def on_sidebar_loaded(self, counts, persons):
        logger.info(f"on_sidebar_loaded: Received top-level data. Persons count: {len(persons)}")
        try:
            self.sidebar.clear()
            
            # 1. Base counts (Unknown/Ignored)
            for label, cid in [("❓ 不明", -1), ("🚫 無視", -2)]:
                key_name = 'unknown' if cid == -1 else 'ignored'
                cnt = counts.get(key_name, 0)
                node = QTreeWidgetItem([f"{label} ({cnt})"])
                node.setData(0, Qt.UserRole, (cid, None)) # (category_id, date_filter)
                self.sidebar.addTopLevelItem(node)
                
                # Add dummy child to show expansion arrow if count > 0
                if cnt > 0:
                    dummy = QTreeWidgetItem(["⌛ 読み込み中..."])
                    node.addChild(dummy)
            
            # 2. People
            if not persons and not counts.get('unknown'):
                if not self.sidebar.topLevelItemCount():
                    self.sidebar.addTopLevelItem(QTreeWidgetItem(["ℹ️ 解析済みの顔はありません"]))
            
            for cid, name, count in persons:
                if count <= 0: continue # SKIP zero-count persons
                display_name = name or f"Person {cid}"
                node = QTreeWidgetItem([f"👤 {display_name} ({count})"])
                node.setData(0, Qt.UserRole, (cid, None))
                self.sidebar.addTopLevelItem(node)
                
                # Add dummy child
                if count > 0:
                    dummy = QTreeWidgetItem(["⌛ 読み込み中..."])
                    node.addChild(dummy)
            
            logger.info("on_sidebar_loaded: Instant sidebar population complete")
        except Exception as e:
            import traceback
            logger.error(f"on_sidebar_loaded ERROR: {e}\n{traceback.format_exc()}")
            self.sidebar.clear()
            self.sidebar.addTopLevelItem(QTreeWidgetItem(["❌ 読み込みエラー"]))

    def _on_confirm_selection(self):
        """Associates selected faces with the target person."""
        ids = self.get_selected_face_ids()
        if not ids or not self.target_person_id: return
        self._do_bulk_associate(ids, self.target_person_id)

    def _on_new_selection(self):
        """Registers selected faces as a completely new person."""
        ids = self.get_selected_face_ids()
        if not ids: return
        worker = PersonManagementWorker(self.db, PersonAction.REGISTER_NEW, {"face_ids": ids})
        worker.refresh_requested.connect(self.on_person_refresh_requested)
        self._track_worker(worker)
        worker.start()

    def _on_ignore_selection(self):
        """Marks selected faces as ignored."""
        ids = self.get_selected_face_ids()
        if not ids: return
        worker = PersonManagementWorker(self.db, PersonAction.IGNORE, {"face_ids": ids})
        worker.refresh_requested.connect(self.on_person_refresh_requested)
        self._track_worker(worker)
        worker.start()

    def _on_other_person_selection(self):
        """Shows a menu of existing persons to associate with."""
        ids = self.get_selected_face_ids()
        if not ids: return
        
        menu = QMenu(self)
        persons = self.db.get_person_list_with_counts()
        for pid, name, count in persons:
            action = menu.addAction(f"{name or f'Person {pid}'} ({count})")
            action.triggered.connect(lambda checked=False, p=pid: self._do_bulk_associate(ids, p))
        
        menu.exec(self.other_person_btn.mapToGlobal(QPoint(0, self.other_person_btn.height())))

    def on_sidebar_item_expanded(self, item):
        """Triggered when a user clicks the expand (▶) arrow."""
        data = item.data(0, Qt.UserRole)
        if not data: return
        cid, date_filter = data
        
        # Check if we have a dummy child (needs loading)
        if item.childCount() == 1 and item.child(0).text(0) == "⌛ 読み込み中...":
            logger.info(f"Lazy loading dates for item: {item.text(0)}")
            worker = PersonDateLoadWorker(self.db, item, cid)
            worker.dates_loaded.connect(self.on_dates_loaded)
            self._track_worker(worker)
            worker.start()

    @Slot(QTreeWidgetItem, list)
    def on_dates_loaded(self, item, dates):
        """Populates sub-nodes for a specific person/category."""
        try:
            # Clear dummy
            item.takeChild(0)
            
            data = item.data(0, Qt.UserRole)
            cid = data[0] if data else -1
            
            for dk, dcnt in dates:
                d_node = QTreeWidgetItem([f"📅 {dk} ({dcnt})"])
                # Store date filter in UserRole: (category_id, date_string)
                d_node.setData(0, Qt.UserRole, (cid, dk))
                item.addChild(d_node)
            
            logger.info(f"Lazy load complete for: {item.text(0)}")
        except Exception as e:
            logger.error(f"on_dates_loaded ERROR: {item.text(0)}: {e}")

    def on_sidebar_item_clicked(self, item, column):
        data = item.data(0, Qt.UserRole)
        if data:
            cid, date_val = data
            self.load_faces(cid, date_val)

    def toggle_suggestion_mode(self):
        """Toggles AI similarity suggestions for the currently selected person."""
        self.is_suggestion_mode = self.suggestion_btn.isChecked()
        logger.info(f"toggle_suggestion_mode: {self.is_suggestion_mode} for cat={self.current_category_id}")
        
        # Stop existing regular loaders
        for w in self.active_workers[:]:
             if isinstance(w, (FaceLoadWorker, FaceCropWorker)):
                 try: w.stop()
                 except: pass

        if self.is_suggestion_mode:
            # Validate target
            if self.current_category_id is None or self.current_category_id < 0:
                QMessageBox.warning(self, "エラー", "AI提案を表示するには、サイドバーで特定の人物を選択してください。")
                self.suggestion_btn.setChecked(False)
                self.is_suggestion_mode = False
                return

            self.face_model.clear()
            self.target_person_id = self.current_category_id
            
            self.loading_bar.setVisible(True)
            self.loading_bar.setRange(0, 0)
            
            self.suggestion_worker = FaceSuggestionWorker(self.db, self.target_person_id)
            self.suggestion_worker.suggestions_ready.connect(self.on_suggestions_ready)
            self.suggestion_worker.finished.connect(self.on_load_finished)
            self._track_worker(self.suggestion_worker)
            self.suggestion_worker.start()
        else:
            # Stop suggestion worker if running
            if self.suggestion_worker and self.suggestion_worker.isRunning():
                self.suggestion_worker.stop()
                self.suggestion_worker.wait()
            
            # Reload regular faces
            if self.current_category_id is not None:
                self.load_faces(self.current_category_id, self.current_date)

    @Slot(list)
    def on_suggestions_ready(self, suggestions):
        """Displays AI similarity results."""
        if not self.is_suggestion_mode: return
        logger.info(f"on_suggestions_ready: Received {len(suggestions)} suggestions.")
        
        formatted = []
        cache_dir = get_face_cache_dir()
        
        # We'll batch these into groups for FaceCropWorker to generate thumbnails if missing
        to_crop = []
        
        for info in suggestions:
            face_id = info["face_id"]
            cache_path = os.path.join(cache_dir, f"face_{face_id}.jpg")
            
            qimg = None
            if os.path.exists(cache_path):
                qimg = QImage(cache_path)
                if qimg.isNull(): 
                    logger.warning(f"on_suggestions_ready: Cache corrupted for face_{face_id}. Forcing regeneration.")
                    info["needs_crop"] = True
                    to_crop.append(info)
                    qimg = None
            else:
                info["needs_crop"] = True
                to_crop.append(info)
            
            item_data = info.copy()
            item_data.update({"qimage": qimg, "selected": False})
            formatted.append(item_data)
            
        self.face_model.append_data(formatted)
        
        if to_crop:
            self._start_crop_worker(to_crop)
        
        # Hide loading bar and stop indeterminate mode
        self.on_load_finished()
        self.all_loaded = True

    def load_faces(self, category_id, specific_date=None):
        if self.is_loading and self.current_category_id == category_id and self.current_date == specific_date: 
            return
        
        # Milestone 1: Non-blocking transitions (Crash Fix)
        # We stop active workers gracefully without wait() to prevent UI hang
        for w in self.active_workers[:]:
            if isinstance(w, (FaceLoadWorker, FaceCropWorker)):
                try: 
                    w.stop()
                    # We keep the reference in active_workers so it can exit safely
                except: pass

        self.face_model.clear()
        self.last_date_key = None
        self.current_category_id = category_id
        self.current_date = specific_date
        self.last_item_date = None
        self.last_item_id = None
        self.load_count = 0
        self.all_loaded = False
        self.is_loading = True
        self.loading_bar.setVisible(True)
        self.loading_bar.setRange(0, 0)
        
        # Milestone 3.4: Manage Toolbar & Suggestion State
        is_person = (category_id >= 0)
        self.toolbar.setVisible(is_person)
        if self.is_suggestion_mode:
            self.suggestion_btn.setChecked(False)
            self.is_suggestion_mode = False
            if self.suggestion_worker:
                self.suggestion_worker.stop()
                self.suggestion_worker = None
        
        worker = FaceLoadWorker(self.db, category_id, limit=500, 
                                after_date=None, after_id=None, specific_date=specific_date)
        worker.faces_loaded.connect(self.add_face_batch)
        worker.finished.connect(self.on_load_finished)
        self._track_worker(worker)
        worker.start(QThread.LowPriority)

    def on_load_finished(self):
        self.is_loading = False
        self.loading_bar.setVisible(False)
        self.loading_bar.setRange(0, 100) # Reset from indeterminate mode
        if self.load_count < self.page_size: self.all_loaded = True
        print(f"DEBUG: Load finished. current_offset={self.current_offset}, load_count={self.load_count}")

    def add_face_batch(self, cid, batch):
        ui_start = time.perf_counter()
        if self.current_category_id != cid: return
        formatted = []
        for info, qimg in batch:
            ds = info.get("capture_date")
            dk = ds[:10].replace(":", "/") if ds and len(ds) >= 10 else "日付不明"
            if dk != self.last_date_key:
                formatted.append({"is_header": True, "date_header": dk})
                self.last_date_key = dk
            
            item_data = info.copy()
            item_data.update({"qimage": qimg, "selected": False})
            formatted.append(item_data)
            self.load_count += 1
        
        self.face_model.append_data(formatted)
        if formatted:
            # Update Keysets from the last element that IS NOT a header
            for i in reversed(formatted):
                if not i.get("is_header"):
                    self.last_item_date = i.get("capture_date")
                    self.last_item_id = i.get("face_id")
                    break

        ui_duration = time.perf_counter() - ui_start
        logger.info(f"UI Batch formatting/append took {ui_duration:.4f}s for {len(formatted)} elements.")
        
        # Milestone 2: Trigger background crop for items missing images
        needing_crop = [f for f, img in batch if f.get("needs_crop")]
        if needing_crop:
            self._start_crop_worker(needing_crop)

    def _start_crop_worker(self, items):
        """Requests crops from the centralized render engine."""
        self.render_engine.enqueue_items(items)

    @Slot(list)
    def _on_images_batch_ready(self, results):
        if not self.face_model: return
        for face_id, qimg in results:
            self.face_model.update_image_data(face_id, qimg)

    def on_scroll_moved(self, value):
        if self.is_suggestion_mode: return # Do not paginate in suggestion mode
        if not self.is_loading and not self.all_loaded:
            sb = self.list_view.verticalScrollBar()
            if sb.maximum() > 0 and value >= sb.maximum() * 0.8:
                self._trigger_load_next_chunk()

    def _trigger_load_next_chunk(self):
        if self.is_loading or self.all_loaded: return
        self.is_loading = True
        self.loading_bar.setVisible(True)
        worker = FaceLoadWorker(self.db, self.current_category_id, limit=500, 
                                after_date=self.last_item_date, after_id=self.last_item_id,
                                specific_date=self.current_date)
        worker.faces_loaded.connect(self.add_face_batch)
        worker.finished.connect(self.on_load_finished)
        self._track_worker(worker)
        worker.start(QThread.LowPriority)

    def update_bulk_buttons(self):
        count = self.face_model.get_selection_count()
        # v2.3: Use new button names
        for b in [self.confirm_btn, self.new_person_btn, self.other_person_btn, self.ignore_btn]:
            b.setEnabled(count > 0)

    def get_selected_face_ids(self):
        return [i["face_id"] for i in self.face_model._data if not i.get("is_header") and i.get("selected")]

    def _select_all_current(self):
        """Selects all currently visible face cards (Milestone 2.3)."""
        model = self.face_model
        for i in range(model.rowCount()):
            item = model.get_item(i)
            if item and not item.get("is_header"):
                item["selected"] = True
        model.dataChanged.emit(model.index(0,0), model.index(model.rowCount()-1,0), [Qt.UserRole])

    def _deselect_all_current(self):
        model = self.face_model
        for i in range(model.rowCount()):
            item = model.get_item(i)
            if item and not item.get("is_header"):
                item["selected"] = False
        model.dataChanged.emit(model.index(0,0), model.index(model.rowCount()-1,0), [Qt.UserRole])

    def _on_confirm_selection(self):
        """Associates selected faces with the target person."""
        ids = self.get_selected_face_ids()
        if not ids or not self.target_person_id: return
        self._do_bulk_associate(ids, self.target_person_id)

    def _on_new_selection(self):
        """Registers selected faces as a completely new person."""
        ids = self.get_selected_face_ids()
        if not ids: return
        worker = PersonManagementWorker(self.db, PersonAction.REGISTER_NEW, {"face_ids": ids})
        worker.refresh_requested.connect(self.on_person_refresh_requested)
        self._track_worker(worker)
        worker.start()

    def _on_ignore_selection(self):
        """Marks selected faces as ignored."""
        ids = self.get_selected_face_ids()
        if not ids: return
        worker = PersonManagementWorker(self.db, PersonAction.IGNORE, {"face_ids": ids})
        worker.refresh_requested.connect(self.on_person_refresh_requested)
        self._track_worker(worker)
        worker.start()

    def _on_other_person_selection(self):
        """Shows a menu of existing persons to associate with."""
        ids = self.get_selected_face_ids()
        if not ids: return
        
        menu = QMenu(self)
        persons = self.db.get_person_list_with_counts()
        for pid, name, count in persons:
            action = menu.addAction(f"{name or f'Person {pid}'} ({count})")
            # Bind p=pid to avoid late binding closure issues
            action.triggered.connect(lambda checked=False, p=pid: self._do_bulk_associate(ids, p))
        
        btn_pos = self.other_person_btn.mapToGlobal(QPoint(0, self.other_person_btn.height()))
        menu.exec(btn_pos)

    def _do_bulk_associate(self, ids, cid):
        worker = PersonManagementWorker(self.db, PersonAction.ASSOCIATE_EXISTING, {"face_ids": ids, "cluster_id": cid})
        worker.refresh_requested.connect(self.on_person_refresh_requested)
        self._track_worker(worker)
        worker.start()

    def on_person_refresh_requested(self):
        """Called when person metadata/clusters are changed. Refreshes BOTH sidebar and current grid."""
        logger.info(f"on_person_refresh_requested: Refreshing current cat={self.current_category_id}")
        self.refresh_sidebar()
        if self.current_category_id is not None:
            self.load_faces(self.current_category_id, self.current_date)
        self.refresh_requested.emit()

    def show_face_menu(self, fid, pos):
        ids = self.get_selected_face_ids()
        if fid not in ids: ids = [fid]
        menu = QMenu(self)
        
        count = len(ids)
        a1 = menu.addAction(f"✨ 新規登録 ({count}枚)")
        a1.triggered.connect(lambda: self._bulk_register_new_with_ids(ids))
        
        sub = menu.addMenu(f"🔄 人物へ結合")
        persons = self.db.get_person_list_with_counts()
        for cid, name, count_val in persons:
            act = sub.addAction(f"{name or cid} ({count_val})")
            act.triggered.connect(lambda checked=False, target=cid: self._do_bulk_associate(ids, target))
            
        a2 = menu.addAction(f"🚫 無視リストへ")
        a2.triggered.connect(lambda: self._bulk_ignore_with_ids(ids))
        menu.exec(pos)

    def _bulk_register_new_with_ids(self, ids):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新規登録", f"{len(ids)}枚を新規人物として登録:")
        if ok and name.strip():
            worker = PersonManagementWorker(self.db, PersonAction.REGISTER_NEW, {"face_ids": ids, "name": name.strip()})
            worker.refresh_requested.connect(self.on_person_refresh_requested)
            self._track_worker(worker)
            worker.start()

    def _bulk_ignore_with_ids(self, ids):
        if QMessageBox.question(self, "無視登録", f"{len(ids)}枚を無視しますか？") == QMessageBox.Yes:
            worker = PersonManagementWorker(self.db, PersonAction.IGNORE_FACE, {"face_ids": ids})
            worker.refresh_requested.connect(self.on_person_refresh_requested)
            self._track_worker(worker)
            worker.start()

    def bulk_register_new(self):
        self._bulk_register_new_with_ids(self.get_selected_face_ids())
    def bulk_associate_existing(self):
        ids = self.get_selected_face_ids()
        if not ids: return
        menu = QMenu(self)
        for cid, name, count in self.db.get_person_list_with_counts():
            act = menu.addAction(f"👤 {name or cid}")
            act.triggered.connect(lambda checked=False, target=cid: self._do_bulk_associate(ids, target))
        menu.exec(self.btn_bulk_move.mapToGlobal(QPoint(0, self.btn_bulk_move.height())))
    def bulk_ignore(self):
        self._bulk_ignore_with_ids(self.get_selected_face_ids())
    
    def show_sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        if not item: return
        cid = item.data(Qt.UserRole)
        if cid is None or cid < 0: return 

        menu = QMenu(self)
        rename_act = menu.addAction("✏️ 名前を変更")
        rename_act.triggered.connect(lambda: self.rename_person(item, cid))
        
        ignore_act = menu.addAction("🚫 この人物を無視リストへ")
        ignore_act.triggered.connect(lambda: self.ignore_cluster(cid))
        
        menu.exec(self.sidebar.mapToGlobal(pos))

    def rename_person(self, item, cid):
        from PySide6.QtWidgets import QInputDialog
        # Extract name from item text
        old_full = item.text()
        old_name = old_full.split('(')[0].strip()
        if old_name.startswith('👤 '): old_name = old_name[2:]
        
        new_name, ok = QInputDialog.getText(self, "名前の変更", "新しい名前:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            worker = PersonManagementWorker(self.db, PersonAction.RENAME_PERSON, {"cluster_id": cid, "name": new_name.strip()})
            worker.refresh_requested.connect(self.on_person_refresh_requested)
            self._track_worker(worker)
            worker.start()

    def ignore_cluster(self, cid):
        if QMessageBox.warning(self, "人物を無視", "この人物（クラスタ全体）を無視リストに移動しますか？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            worker = PersonManagementWorker(self.db, PersonAction.IGNORE_CLUSTER, {"cluster_id": cid})
            worker.refresh_requested.connect(self.on_person_refresh_requested)
            self._track_worker(worker)
            worker.start()

    def add_face_item(self, cid, info, path):
        self.add_face_batch(cid, [(info, path)])
