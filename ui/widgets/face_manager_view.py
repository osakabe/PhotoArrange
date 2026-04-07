import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from PySide6.QtCore import QModelIndex, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.database import Database
from core.models import ClusterInfo, FaceCountsResult, FaceDisplayItem, FaceInfo
from core.repositories.face_repository import FaceRepository
from core.utils import Profiler, get_face_cache_dir
from processor.suggestion_logic import FaceSuggestionWorker
from processor.workers import (
    FaceCropResult,
    FaceCropWorker,
    FaceLoadResult,
    FaceLoadWorker,
    PersonAction,
    PersonManagementWorker,
    PersonOptimizationWorker,
    SidebarLoadWorker,
)
from ui.ui_utils import group_media_by_date_and_location

from .thumbnail_grid import ThumbnailGrid
from .tree_view import MediaTreeView

logger = logging.getLogger("PhotoArrange")


@dataclass
class FaceUIItem:
    info: FaceInfo
    qimage: Optional[QImage] = None
    selected: bool = False
    needs_crop: bool = True


class FaceReviewDialog(QDialog):
    """Dialog for visual confirmation of outliers before removal."""
    def __init__(self, faces: list[FaceInfo], title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"成長の連鎖から外れている可能性がある写真が {len(faces)} 枚見つかりました。\n"
            "画像を確認し、人物から解除（『不明』に戻す）するものを選んでください。"
        ))
        
        self.grid = ThumbnailGrid()
        self.grid.set_crop_mode(True)
        layout.addWidget(self.grid)
        
        # Populate grid items (pre-selected by default)
        display_items = []
        cache_dir = get_face_cache_dir()
        for f in faces:
            cp = os.path.join(cache_dir, f"face_{f.face_id}.jpg")
            item = FaceDisplayItem(face=f, image=cp if os.path.exists(cp) else None)
            item.selected = True # Pre-select for review
            display_items.append(item)
        
        self.grid.append_data(display_items)
        
        # Generate missing crops in background
        self.crop_worker = FaceCropWorker(faces)
        self.crop_worker.batch_finished.connect(self.grid.media_model.update_face_image_batch)
        self.crop_worker.start()
        
        btns = QHBoxLayout()
        btn_ok = QPushButton("選択した写真を『不明』に戻す")
        btn_ok.setMinimumHeight(40)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.setMinimumHeight(40)
        btn_cancel.clicked.connect(self.reject)
        
        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        layout.addLayout(btns)

    def get_selected_ids(self) -> list[int]:
        return [
            item.face.face_id 
            for item in self.grid.media_model._data 
            if isinstance(item, FaceDisplayItem) and item.selected
        ]


class FaceManagerView(QWidget):
    refresh_requested = Signal()

    def __init__(self, db: Database, repo: FaceRepository) -> None:
        super().__init__()
        self.db = db
        self.repo = repo
        self.is_suggestion_mode = False
        self.current_category_id = -1
        self.current_threshold = 0.8
        self.person_centroid = None
        self.active_workers: list[QThread] = []

        # Seek Markers
        self.last_capture_date: Optional[str] = None
        self.last_face_id: Optional[int] = None
        self.has_more = True
        self.is_loading = False
        self.last_key: Optional[tuple[str, str]] = None

        self.init_ui()

    def init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)

        # Left: Sidebar
        self.sidebar = MediaTreeView()
        self.sidebar.clicked.connect(self._on_sidebar_selected)
        splitter.addWidget(self.sidebar)

        # Right: Content
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Toolbar
        t_layout = QHBoxLayout()
        self.suggestion_btn = QPushButton("🔎 AI提案を表示")
        self.suggestion_btn.setCheckable(True)
        self.suggestion_btn.clicked.connect(self.toggle_suggestion_mode)
        t_layout.addWidget(self.suggestion_btn)

        # --- Suggestion Bulk Actions Toolbar ---
        self.bulk_container = QWidget()
        self.bulk_layout = QHBoxLayout(self.bulk_container)
        self.bulk_layout.setContentsMargins(10, 0, 0, 0)
        self.bulk_layout.setSpacing(5)

        btn_all = QPushButton("全選択")
        btn_none = QPushButton("選択解除")

        btn_all.clicked.connect(lambda: self.face_grid.select_all(True))
        btn_none.clicked.connect(lambda: self.face_grid.select_all(False))

        # --- Sort Order ---
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["日付 (新しい順)", "日付 (古い順)", "類似度 (高い順)", "類似度 (低い順)"])
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.bulk_layout.addWidget(QLabel("並べ替え:"))
        self.bulk_layout.addWidget(self.sort_combo)

        # --- Threshold Selection ---
        self.btn_set_threshold = QPushButton(f"しきい値を設定 ({self.current_threshold:.2f})")
        self.btn_set_threshold.clicked.connect(self._on_set_threshold_clicked)
        self.bulk_layout.addWidget(self.btn_set_threshold)

        self.btn_select_thresh = QPushButton("しきい値以上を選択")
        self.btn_select_thresh.clicked.connect(self.select_by_threshold)
        self.bulk_layout.addWidget(self.btn_select_thresh)

        self.bulk_layout.addWidget(btn_all)
        self.bulk_layout.addWidget(btn_none)

        # --- Optimization ---

        self.btn_optimize = QPushButton("人物の再編・最適化")
        self.btn_optimize.clicked.connect(self._on_optimize_person_clicked)
        self.bulk_layout.addWidget(self.btn_optimize)

        self.bulk_container.setVisible(True) # Persistent by default
        t_layout.addWidget(self.bulk_container)
        t_layout.addStretch()
        right_layout.addLayout(t_layout)

        # Grid
        self.face_grid = ThumbnailGrid()
        self.face_grid.tag_clicked.connect(self.on_tag_clicked)
        self.face_grid.near_bottom_reached.connect(self.load_more_faces)
        self.face_grid.context_menu_requested.connect(self._show_context_menu)
        self.face_grid.item_clicked.connect(self._open_original_media)
        self.face_grid.set_crop_mode(True)
        right_layout.addWidget(self.face_grid)

        self.loading_bar = QProgressBar()
        self.loading_bar.setVisible(False)
        right_layout.addWidget(self.loading_bar)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([250, 1000])
        layout.addWidget(splitter)

    def refresh_sidebar(self) -> None:
        with Profiler("FaceManagerView.refresh_sidebar"):
            defaults = [("❓ 不明", -1, 0), ("🚫 無視", -2, 0)]
            self.sidebar.initialize_categories(defaults, add_defaults=False)
            worker = SidebarLoadWorker(self.repo)
            worker.result_ready.connect(self.on_sidebar_loaded)
            self._track_worker(worker)
            worker.start()

    @Slot(object)
    def on_sidebar_loaded(self, res: Any) -> None:
        with Profiler("FaceManagerView.on_sidebar_loaded"):
            try:
                counts: Optional[FaceCountsResult] = getattr(res, "counts", None)
                persons: list[ClusterInfo] = getattr(res, "persons", [])
                categories: list[tuple[str, int, int]] = []

                if counts:
                    categories.append(("❓ 不明", -1, counts.unknown))
                    categories.append(("🚫 無視", -2, counts.ignored))
                    person_counts = counts.persons
                else:
                    categories.append(("❓ 不明", -1, 0))
                    categories.append(("🚫 無視", -2, 0))
                    person_counts = {}

                for p in persons:
                    label = p.custom_name or f"Person {p.cluster_id}"
                    count = person_counts.get(p.cluster_id, 0)
                    categories.append((label, p.cluster_id, count))

                if hasattr(self.sidebar, "initialize_categories"):
                    self.sidebar.initialize_categories(categories, add_defaults=False)
            except Exception as e:
                logger.error(f"FaceManager: on_sidebar_loaded failed: {e}")

    def _on_sidebar_selected(self, index: QModelIndex) -> None:
        item = self.sidebar.model.itemFromIndex(index)
        if item:
            self.load_faces(int(item.data(Qt.UserRole)))

    def load_faces(self, category_id: int) -> None:
        with Profiler(f"FaceManager.load_faces (cat={category_id})"):
            self._cancel_active_workers()
            self.current_category_id = category_id
            self.last_key, self.last_capture_date, self.last_face_id = None, None, None
            self.has_more, self.is_loading = True, False
            self.person_centroid = None
            self.face_grid.clear()
            self.suggestion_btn.setChecked(False)
            self.is_suggestion_mode = False
            self.bulk_container.setVisible(True)
            self.btn_set_threshold.setVisible(False)
            self.btn_select_thresh.setVisible(False)
            
            # Update ToolBar Visibility (Similarity Sort only for persons, not Unknown/Ignore)
            is_person = self.current_category_id >= 0
            self.sort_combo.setVisible(is_person)
            self.btn_optimize.setVisible(is_person)
            
            self._fetch_chunk()

    def load_more_faces(self) -> None:
        if not self.is_loading and self.has_more and not self.suggestion_btn.isChecked():
            self._fetch_chunk()

    def _fetch_chunk(self) -> None:
        self.is_loading = True
        self.loading_bar.setVisible(True)
        self.loading_bar.setRange(0, 0)
        worker = FaceLoadWorker(
            self.repo,
            self.current_category_id,
            limit=100,
            last_capture_date=self.last_capture_date,
            last_face_id=self.last_face_id,
        )
        worker.chunk_ready.connect(self._on_faces_chunk_ready)
        worker.result_ready.connect(self._on_faces_loaded)
        worker.finished.connect(
            lambda: (self.loading_bar.setVisible(False), setattr(self, "is_loading", False))
        )
        self._track_worker(worker)
        worker.start()

    @Slot(list)
    def _on_faces_chunk_ready(self, faces: list[FaceInfo]) -> None:
        with Profiler(f"FaceManagerView._on_faces_chunk_ready (count={len(faces)})"):
            if self.is_suggestion_mode:
                logger.info("FaceManagerView: Ignoring normal face chunk (suggestion mode active).")
                return

            display_items: list[FaceDisplayItem] = []
            cache_dir = get_face_cache_dir()
            for f in faces:
                cp = os.path.join(cache_dir, f"face_{f.face_id}.jpg")
                display_items.append(
                    FaceDisplayItem(face=f, image=cp if os.path.exists(cp) else None)
                )
            grouped, self.last_key = group_media_by_date_and_location(display_items, self.last_key)
            self.face_grid.append_data(grouped)
            crop_worker = FaceCropWorker(faces)
            crop_worker.batch_finished.connect(self._on_crops_ready)
            self._track_worker(crop_worker)
            crop_worker.start()

    @Slot(object)
    def _on_faces_loaded(self, result: FaceLoadResult) -> None:
        if self.is_suggestion_mode:
            logger.info("FaceManagerView: Ignoring load_faces completion (suggestion mode active).")
            return

        if result.category_id == self.current_category_id:
            self.has_more, self.last_capture_date, self.last_face_id = (
                result.has_more,
                result.last_capture_date,
                result.last_face_id,
            )
            # Prefetch from 0% scroll: as soon as one chunk finishes, start the next one
            # to keep the buffer full. We limit background prefetching to a reasonable
            # amount (e.g., 500 items) to prevent excessive memory use for very large collections.
            if self.has_more and len(self.face_grid.media_model._data) < 500:
                QTimer.singleShot(200, self.load_more_faces)

    @Slot(list)
    def _on_crops_ready(self, results: list[FaceCropResult]) -> None:
        """Update the model with memory-cached face images using batch optimization."""
        batch = [(res.face_id, res.image) for res in results]
        self.face_grid.media_model.update_face_image_batch(batch)

    def toggle_suggestion_mode(self) -> None:
        is_on = self.suggestion_btn.isChecked()
        self.is_suggestion_mode = is_on
        if is_on:
            if self.current_category_id < 0:
                QMessageBox.warning(self, "AI提案", "特定の人物を選択してください。")
                self.suggestion_btn.setChecked(False)
                self.is_suggestion_mode = False
                return
            self.face_grid.clear()
            self.last_key = None
            self.loading_bar.setVisible(True)
            self.loading_bar.setRange(0, 0)
            self.bulk_container.setVisible(True)
            self.btn_set_threshold.setVisible(True)
            self.btn_select_thresh.setVisible(True)
            self.sort_combo.setVisible(True)
            self.btn_optimize.setVisible(False) # Optimization not applicable in suggestion mode
            worker = FaceSuggestionWorker(self.db, self.current_category_id)
            worker.suggestions_ready.connect(self._on_suggestions_ready)
            worker.finished.connect(lambda: self.loading_bar.setVisible(False))
            self._track_worker(worker)
            worker.start()
        else:
            self.load_faces(self.current_category_id)

    @Slot(list)
    def _on_suggestions_ready(self, suggestions: list[dict[str, Any]]) -> None:
        count = len(suggestions)
        with Profiler(f"FaceManagerView._on_suggestions_ready (count={count})"):
            if not self.is_suggestion_mode:
                return
            
            if count == 0 and len(self.face_grid.media_model._data) == 0:
                logger.info("FaceManagerView: No suggestions found for this person.")
                # We show a small message box only once when the worker completes with 0 results
                # The worker.finished signal can also be used for this.
                return

            display_items: list[FaceDisplayItem] = []
            f_for_crop: list[FaceInfo] = []
            cache_dir = get_face_cache_dir()
            for s in suggestions:
                f = FaceInfo(
                    face_id=s["face_id"],
                    file_path=s["file_path"],
                    bbox=s["bbox"],
                    frame_index=s.get("frame_index", 0),
                    capture_date=s.get("capture_date"),
                    similarity=s.get("similarity"),
                    distance=s.get("distance"),
                    metadata=s.get("metadata", {}),
                )
                cp = os.path.join(cache_dir, f"face_{f.face_id}.jpg")
                display_items.append(
                    FaceDisplayItem(face=f, image=cp if os.path.exists(cp) else None)
                )
                f_for_crop.append(f)
            grouped, self.last_key = group_media_by_date_and_location(display_items, self.last_key)
            self.face_grid.append_data(grouped)
            if f_for_crop:
                w = FaceCropWorker(f_for_crop)
                w.batch_finished.connect(self._on_crops_ready)
                self._track_worker(w)
                w.start()

    def get_selected_face_ids(self) -> list[int]:
        return [
            i.face.face_id
            for i in self.face_grid.media_model._data
            if isinstance(i, FaceDisplayItem) and i.selected
        ]

    def _execute_bulk_action(self, action_type: str, params: dict[str, Any]) -> None:
        self.loading_bar.setVisible(True)
        self.loading_bar.setRange(0, 0)
        worker = PersonManagementWorker(self.db, action_type, params)
        worker.finished_task.connect(self._on_bulk_action_finished)
        self._track_worker(worker)
        worker.start()

    def _on_bulk_action_finished(self, success: bool, message: str) -> None:
        self.loading_bar.setVisible(False)
        logger.info(f"FaceManagerView: Bulk action finished (success={success}). Msg: {message}")
        if success:
            if self.suggestion_btn.isChecked():
                # Directly restart suggestion worker without toggling normal mode to avoid race conditions
                logger.info("FaceManagerView: Refreshing suggestions directly.")
                self.face_grid.clear()
                self.last_key = None
                self.loading_bar.setVisible(True)
                self.loading_bar.setRange(0, 0)
                worker = FaceSuggestionWorker(self.db, self.current_category_id)
                worker.suggestions_ready.connect(self._on_suggestions_ready)
                worker.finished.connect(lambda: self.loading_bar.setVisible(False))
                self._track_worker(worker)
                worker.start()
            else:
                self.load_faces(self.current_category_id)
            self.refresh_requested.emit()
        else:
            QMessageBox.critical(self, "エラー", f"失敗しました: {message}")

    def select_by_threshold(self) -> None:
        """Select all suggested faces that meet or exceed the similarity threshold."""
        thresh = self.current_threshold
        count = 0
        for item in self.face_grid.media_model._data:
            if isinstance(item, FaceDisplayItem):
                if item.face.similarity is not None and item.face.similarity >= thresh:
                    item.selected = True
                    count += 1
                else:
                    item.selected = False
        
        # Trigger model update to refresh checkboxes in UI
        self.face_grid.media_model.layoutChanged.emit()
        logger.info(f"Threshold selection: Selected {count} faces with similarity >= {thresh}")

    def _on_set_threshold_clicked(self) -> None:
        """Open a dialog to set the similarity threshold."""
        val, ok = QInputDialog.getDouble(
            self, "しきい値を設定", "類似度しきい値 (0.0 - 1.0):",
            self.current_threshold, 0.0, 1.0, 2
        )
        if ok:
            self.current_threshold = val
            self.btn_set_threshold.setText(f"しきい値を設定 ({self.current_threshold:.2f})")
            logger.info(f"Threshold set to: {self.current_threshold}")

    def _on_sort_changed(self) -> None:
        """Sort the current grid items based on the selection."""
        idx = self.sort_combo.currentIndex()
        if idx < 0: return

        # Isolate items from headers to avoid attribute errors
        items = [x for x in self.face_grid.media_model._data if isinstance(x, FaceDisplayItem)]
        if not items:
            return

        if idx in (0, 1): # Date ASC/DESC
            reverse = (idx == 0)
            items.sort(
                key=lambda x: (getattr(x.face, "capture_date", "") or "", x.face.face_id),
                reverse=reverse
            )
        elif idx in (2, 3): # Similarity HIGH/LOW
            if self.current_category_id < 0:
                return 
                
            # If not in suggestion mode, we need a reference centroid
            if self.person_centroid is None:
                self.person_centroid = self._calculate_current_centroid()
                
            if self.person_centroid is None:
                return

            import numpy as np
            centroid = self.person_centroid
            for item in items:
                face = item.face
                with self.db.get_connection() as conn:
                    row = conn.execute("SELECT vector_blob FROM faces WHERE face_id = ?", (face.face_id,)).fetchone()
                    if row and row[0]:
                        emb = np.frombuffer(row[0], dtype=np.float32)
                        norm = np.linalg.norm(emb)
                        sim = float(np.dot(emb / norm, centroid)) if norm > 0 else 0.0
                        object.__setattr__(face, "similarity", sim)
            
            reverse = (idx == 2)
            items.sort(
                key=lambda x: (getattr(x.face, "similarity", 0.0) or 0.0, x.face.face_id),
                reverse=reverse
            )

        # Clear and re-populate with headers
        self.face_grid.clear()
        self.last_key = None
        grouped, self.last_key = group_media_by_date_and_location(items, None)
        self.face_grid.append_data(grouped)
        logger.info(f"Re-sorted and re-grouped items by Mode {idx}")

    def _calculate_current_centroid(self):
        """Calculates centroid of the currently selected person."""
        import numpy as np
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT vector_blob FROM faces WHERE cluster_id = ? AND vector_blob IS NOT NULL",
                (self.current_category_id,),
            ).fetchall()
            if not rows: return None
            embs = []
            for r in rows:
                emb = np.frombuffer(r[0], dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0: embs.append(emb / norm)
            if not embs: return None
            mean_vec = np.mean(embs, axis=0)
            norm_c = np.linalg.norm(mean_vec)
            return (mean_vec / norm_c) if norm_c > 0 else mean_vec

    def _on_optimize_person_clicked(self) -> None:
        """Trigger similarity-chain analysis for the current person."""
        if self.current_category_id < 0:
            return

        reply = QMessageBox.question(
            self, "人物の再編・最適化",
            "この人物に登録されている全写真を分析し、成長過程の連鎖から外れている写真（登録間違いの可能性）を探します。\n実行しますか？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            return

        self.loading_bar.setVisible(True)
        self.loading_bar.setRange(0, 0)
        
        worker = PersonOptimizationWorker(self.db, self.current_category_id)
        worker.result_ready.connect(self._on_optimization_results)
        worker.finished.connect(lambda: self.loading_bar.setVisible(False))
        self._track_worker(worker)
        worker.start()

    @Slot(dict)
    def _on_optimization_results(self, res: dict) -> None:
        """Handle results of person optimization analysis."""
        outlier_ids = res.get("outlier_ids", [])
        total = res.get("total_count", 0)
        stages = res.get("stages_count", 0)

        if not outlier_ids:
            QMessageBox.information(
                self, "分析完了",
                f"分析が完了しました（全 {total} 枚、{stages} つの成長ステージ）。\n現在の連鎖から外れている写真は見つかりませんでした。"
            )
            return

        # Fetch full FaceInfo for outliers to show them visually
        outlier_faces = self.repo.get_faces_by_ids(outlier_ids)
        
        # Show review dialog
        dialog = FaceReviewDialog(
            outlier_faces, 
            f"最適化の提案（全 {total} 枚、{stages} ステージ）", 
            self
        )
        if dialog.exec_() == QDialog.Accepted:
            selected_ids = dialog.get_selected_ids()
            if selected_ids:
                # Re-use PersonManagementWorker to unregister outliers
                mgr_worker = PersonManagementWorker(
                    self.db, PersonAction.UNREGISTER, {"face_ids": selected_ids, "cluster_id": -1}
                )
                mgr_worker.finished_task.connect(
                    lambda ok, m: (self.refresh_requested.emit()) if ok else None
                )
                self._track_worker(mgr_worker)
                mgr_worker.start()

    def _open_original_media(self, file_path: str) -> None:
        """Opens the original file using system default application."""
        if not file_path:
            return
        abs_p = os.path.normpath(file_path)
        if os.path.exists(abs_p):
            try:
                os.startfile(abs_p)
            except Exception as e:
                logger.error(f"Failed to open {abs_p}: {e}")

    def _show_context_menu(self, file_path: str, pos: Any) -> None:
        """Shows registration/management context menu for selected items."""
        ids = self.get_selected_face_ids()
        # If nothing selected, select the right-clicked item temporarily?
        # Actually, let's keep it strictly for selections or prompt if empty.
        if not ids:
            return

        menu = QMenu(self)
        
        # 1. Register to suggested/current person
        if self.current_category_id >= 0:
            act_curr = menu.addAction(f"【登録】選択している人物へ")
            act_curr.triggered.connect(self.bulk_register_current)
        
        menu.addSeparator()
        
        # 2. Other actions
        act_new = menu.addAction("【新規】新しい人物として登録...")
        act_new.triggered.connect(self.bulk_register_new)
        
        act_exist = menu.addAction("【選択】既存の人物から選ぶ...")
        act_exist.triggered.connect(self.bulk_register_existing)
        
        menu.addSeparator()
        
        act_ignore = menu.addAction("除外（無視リストへ）")
        act_ignore.triggered.connect(self.bulk_ignore)
        
        menu.exec_(pos)

    def bulk_register_current(self) -> None:
        items = [
            i for i in self.face_grid.media_model._data 
            if isinstance(i, FaceDisplayItem) and i.selected
        ]
        if not items:
            return

        # Skip faces that are already assigned to this person
        ids = [i.face.face_id for i in items if i.face.cluster_id != self.current_category_id]
        
        if not ids:
            logger.info("FaceManagerView: All selected faces are already registered to this person.")
            QMessageBox.information(self, "登録済み", "選択された写真は既にこの人物に登録されています。")
            return

        logger.info(f"FaceManagerView: Attempting bulk registration of {len(ids)} faces to category={self.current_category_id}")
        if (
            ids
            and QMessageBox.question(self, "一括登録", f"{len(ids)}件を現在の人物に登録しますか？")
            == QMessageBox.Yes
        ):
            self.loading_bar.setVisible(True)
            self.loading_bar.setRange(0, 0)
            self._execute_bulk_action(
                PersonAction.ASSOCIATE_EXISTING,
                {"face_ids": ids, "cluster_id": self.current_category_id},
            )

    def bulk_register_new(self) -> None:
        ids = self.get_selected_face_ids()
        if ids:
            name, ok = QInputDialog.getText(
                self, "新規人物", f"{len(ids)}件を新しい人物として作成:"
            )
            if ok and name.strip():
                self._execute_bulk_action(
                    PersonAction.REGISTER_NEW, {"face_ids": ids, "name": name.strip()}
                )

    def bulk_register_existing(self) -> None:
        ids = self.get_selected_face_ids()
        if not ids:
            return
        d = QDialog(self)
        d.setWindowTitle("人物選択")
        v = QVBoxLayout(d)
        lw = QListWidget()
        for p in self.repo.get_clusters():
            lw.addItem(p.custom_name or f"Person {p.cluster_id}")
            lw.item(lw.count() - 1).setData(Qt.UserRole, p.cluster_id)
        v.addWidget(lw)
        btn = QPushButton("登録")
        btn.clicked.connect(d.accept)
        v.addWidget(btn)
        if d.exec_() == QDialog.Accepted and lw.currentItem():
            self._execute_bulk_action(
                PersonAction.ASSOCIATE_EXISTING,
                {"face_ids": ids, "cluster_id": lw.currentItem().data(Qt.UserRole)},
            )

    def bulk_ignore(self) -> None:
        ids = self.get_selected_face_ids()
        if (
            ids
            and QMessageBox.question(self, "無視", f"{len(ids)}件を除外しますか？")
            == QMessageBox.Yes
        ):
            self._execute_bulk_action(PersonAction.IGNORE_FACE, {"face_ids": ids})

    def _track_worker(self, worker: QThread) -> None:
        self.active_workers.append(worker)
        worker.finished.connect(lambda: self._remove_worker(worker))

    def _remove_worker(self, worker: QThread) -> None:
        if worker in self.active_workers:
            self.active_workers.remove(worker)

    def _cancel_active_workers(self) -> None:
        for w in self.active_workers.copy():
            if hasattr(w, "stop"):
                w.stop()
            try:
                if hasattr(w, "chunk_ready"):
                    w.chunk_ready.disconnect()
            except Exception:
                pass

    @Slot(str, int, str)
    def on_tag_clicked(self, file_path: str, cluster_id: int, name: str) -> None:
        if cluster_id >= 0:
            self.load_faces(cluster_id)
