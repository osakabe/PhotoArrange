import logging
import os
import sys
import time
from dataclasses import replace
from typing import Any, Optional

from PySide6.QtCore import QPoint, Qt, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig

# Local imports
from core.database import Database
from core.models import LibraryViewItem, MediaRecord
from core.utils import Profiler, fix_dll_search_path
from processor.image_processor import ImageProcessor
from processor.workers import (
    BatchFileDeleteWorker,
    BatchFileReleaseWorker,
    CleanupWorker,
    DatabaseSyncWorker,
    DataLoaderWorker,
    DuplicateAnalysisWorker,
    DuplicateRegroupingWorker,
    FaceClusteringWorker,
    FaceRecognitionWorker,
    FileSyncWorker,
    LibrarySidebarResult,
    LibrarySidebarWorker,
    LibraryThumbnailWorker,
    MediaLoadResult,
    SearchWorker,
    TreeDataLoadResult,
    TreeDataLoadWorker,
)
from ui.theme import get_style_sheet
from ui.ui_utils import group_media_by_date_and_location
from ui.widgets.face_manager_view import FaceManagerView
from ui.widgets.library_view import LibraryView
from ui.widgets.main_header import MainHeader

# CRITICAL: Fix DLL search paths before AI imports
fix_dll_search_path()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("app_debug.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("PhotoArrange")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PhotoArrange - Professional Photo Organizer")
        self.setMinimumSize(1200, 850)

        self.db = Database()
        self.img_proc = ImageProcessor()
        self.current_folder: Optional[str] = None

        # Load typed config
        self.config = AppConfig.load(self.db.settings_repo)

        # Pagination state
        self.current_filter: dict[str, Any] = {
            "cluster_id": None,
            "year": None,
            "month": None,
            "location": None,
        }
        self.page_size = 100
        self.current_offset = 0
        self.is_loading_more = False
        self.has_more = True

        # UI state
        self.last_hash: Optional[str] = None
        self.last_loc: Optional[str] = None
        self.last_date: Optional[str] = None
        self.hash_to_id: dict[str, int] = {}
        self.next_group_id = 1
        self.next_group_id = 1
        self.active_workers: list[Any] = []
        self.last_header_key = None

        self.init_ui()
        self.apply_theme()

        # [RESTORED] Background synchronization for capture_date denormalization
        self.sync_worker = DatabaseSyncWorker(self.db)
        self.sync_worker.finished_task.connect(self._on_db_sync_finished)
        self.sync_worker.start()
        self._track_worker(self.sync_worker)

    @Slot(bool, str)
    def _on_db_sync_finished(self, success: bool, message: str) -> None:
        logger.info(f"MainWindow: Database synchronization finished (Success={success})")
        # Trigger refresh to show updated counts and dates
        self.initialize_tree()
        self.show_images_paged()

    def init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Header
        self.header = MainHeader()
        self.header.folder_selection_requested.connect(self.select_folder)
        self.header.duplicate_analysis_requested.connect(self.run_duplicate_analysis)
        self.header.duplicate_regroup_requested.connect(self.run_duplicate_regrouping)
        self.header.face_analysis_requested.connect(self.run_face_analysis)
        self.header.face_clustering_requested.connect(self.run_face_clustering)
        self.header.face_manager_toggled.connect(self.toggle_face_manager)
        self.header.settings_requested.connect(self.show_settings)
        self.header.force_reanalyze_toggled.connect(self.update_force_reanalyze)
        self.header.set_force_reanalyze(self.config.force_reanalyze)
        main_layout.addWidget(self.header)

        # 2. Main Stack
        self.central_stack = QStackedWidget()

        # View 0: Library View
        self.library_view = LibraryView()
        self.library_view.tree_load_requested.connect(self.on_tree_load_request)
        self.library_view.tree_selection_changed.connect(self.on_tree_selection)
        self.library_view.tree_rename_requested.connect(self.on_rename_person)

        self.library_view.grid_item_double_clicked.connect(self.open_file)
        self.library_view.grid_tag_clicked.connect(self.manage_tag)
        self.library_view.grid_context_menu_requested.connect(self.show_thumbnail_context_menu)
        self.library_view.grid_more_data_requested.connect(self.load_next_page)
        self.library_view.grid_selection_changed.connect(self.update_selection_ui)

        # Toolbar Proxy Connections
        self.library_view.toolbar.select_all_requested.connect(self.select_all_visible)
        self.library_view.toolbar.deselect_all_requested.connect(self.deselect_all_visible)
        self.library_view.toolbar.clear_tags_requested.connect(self.clear_selected_tags)
        self.library_view.toolbar.cleanup_duplicates_requested.connect(self.cleanup_duplicates)
        self.library_view.toolbar.release_from_group_requested.connect(
            self.release_selected_from_groups
        )
        self.library_view.toolbar.delete_selected_requested.connect(self.delete_selected)
        self.library_view.toolbar.duplicate_filter_changed.connect(self.on_dup_filter_changed)

        self.central_stack.addWidget(self.library_view)

        # View 1: Face Manager
        self.face_manager = FaceManagerView(self.db, self.db.face_repo)

        self.face_manager.refresh_requested.connect(self.initialize_tree)
        self.central_stack.addWidget(self.face_manager)

        main_layout.addWidget(self.central_stack)

        # 3. Footer
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet(
            "background-color: #1A1D2E; border-top: 1px solid #2D324A; color: #64748B;"
        )

        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #3D5AFE; font-weight: bold; margin-left: 20px;")
        self.status_bar.addWidget(self.info_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

    def apply_theme(self) -> None:
        self.setStyleSheet(get_style_sheet())

    def _track_worker(self, worker: Any) -> None:
        self.active_workers.append(worker)
        # Use finished_task signal if available, else standard finished
        if hasattr(worker, "finished_task"):
            worker.finished_task.connect(
                lambda *args: (
                    self.active_workers.remove(worker) if worker in self.active_workers else None
                )
            )
        else:
            worker.finished.connect(
                lambda *args: (
                    self.active_workers.remove(worker) if worker in self.active_workers else None
                )
            )

    def select_folder(self) -> None:
        with Profiler("MainWindow.select_folder"):
            folder = QFileDialog.getExistingDirectory(self, "Select Folder")
            if folder:
                self.current_folder = folder
                self.face_manager.current_folder = folder
                self.status_label.setText(f"Scanning: {folder}...")
                self.progress_bar.setVisible(True)
                self.progress_bar.setRange(0, 100)
                self.set_buttons_enabled(False)

                worker = FileSyncWorker(
                    folder, self.db, include_trash_folders=self.config.include_trash
                )
                worker.progress_val.connect(self.progress_bar.setValue)
                worker.phase_status.connect(self.status_label.setText)
                worker.finished_task.connect(self.on_sync_finished)
                self._track_worker(worker)
                worker.start()

                # Parallel-First: Show existing data immediately while scanning in background
                self.initialize_tree()
                self.show_images_paged()
                self.face_manager.refresh_sidebar()

                # Re-sync dates after file discovery complete
                worker.finished_task.connect(
                    lambda: self.sync_worker.start() if hasattr(self, "sync_worker") else None
                )

    def on_sync_finished(self, success: bool, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.set_buttons_enabled(True)
        if success:
            # Final refresh to catch any newly discovered files
            self.initialize_tree()
            self.show_images_paged()
            self.status_label.setText("Ready")
            self.header.set_ai_actions_enabled(True)
        else:
            QMessageBox.critical(self, "Sync Error", message)

    def set_buttons_enabled(self, enabled: bool) -> None:
        self.header.setEnabled(enabled)
        self.library_view.toolbar.setEnabled(enabled)

    def run_duplicate_analysis(self) -> None:
        if not self.current_folder:
            return
        self.set_buttons_enabled(False)
        self.progress_bar.setVisible(True)
        worker = DuplicateAnalysisWorker(
            self.current_folder,
            self.db,
            include_trash_folders=self.config.include_trash,
            force_reanalyze=self.config.force_reanalyze,
            threshold=self.config.dup_threshold / 10.0,
            stage2_threshold=self.config.dup_threshold_stage2 / 100.0,
        )
        worker.progress_val.connect(self.progress_bar.setValue)
        worker.phase_status.connect(self.status_label.setText)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def run_duplicate_regrouping(self) -> None:
        if not self.current_folder:
            return
        self.set_buttons_enabled(False)
        self.progress_bar.setVisible(True)
        worker = DuplicateRegroupingWorker(
            self.db,
            include_trash=self.config.include_trash,
            threshold=self.config.dup_threshold / 10.0,
            stage2_threshold=self.config.dup_threshold_stage2 / 100.0,
        )
        worker.progress_val.connect(self.progress_bar.setValue)
        worker.phase_status.connect(self.status_label.setText)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def run_face_analysis(self) -> None:
        if not self.current_folder:
            return
        self.set_buttons_enabled(False)
        self.progress_bar.setVisible(True)
        worker = FaceRecognitionWorker(
            self.current_folder,
            self.db,
            include_trash_folders=self.config.include_trash,
            force_reanalyze=self.config.force_reanalyze,
            min_samples=self.config.face_min_samples,
            eps=self.config.face_cluster_eps / 100.0,
            det_thresh=self.config.face_det_thresh / 100.0,
        )
        worker.progress_val.connect(self.progress_bar.setValue)
        worker.phase_status.connect(self.status_label.setText)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def run_face_clustering(self) -> None:
        if not self.current_folder:
            return
        self.set_buttons_enabled(False)
        self.progress_bar.setVisible(True)
        worker = FaceClusteringWorker(
            self.current_folder,
            self.db,
            min_samples=self.config.face_min_samples,
            eps=self.config.face_cluster_eps / 100.0,
            det_thresh=self.config.face_det_thresh / 100.0,
        )
        worker.progress_val.connect(self.progress_bar.setValue)
        worker.phase_status.connect(self.status_label.setText)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def initialize_tree(self) -> None:
        """Asynchronously triggers the library sidebar count updates."""
        logger.info("MainWindow: Initializing library tree (Async skeleton)...")
        start_time = time.perf_counter()

        # 1. Immediate UI Feedback: Show defaults with 0/loading state
        self.library_view.tree_view.initialize_categories([])

        # 2. Start heavy background query
        worker = LibrarySidebarWorker(self.db, self.current_folder)
        worker.result_ready.connect(self._on_sidebar_loaded)
        worker.finished.connect(
            lambda: logger.info(
                f"PROFILER: initialize_tree took {time.perf_counter() - start_time:.4f}s"
            )
        )
        self._track_worker(worker)
        worker.start()

    @Slot(object)
    def _on_sidebar_loaded(self, res: LibrarySidebarResult) -> None:
        root_counts = res.root_counts
        persons = res.persons

        categories = [
            ("All Photos", None, root_counts.get("all", 0)),
            ("🚫 No Faces Detected", -1, root_counts.get("no_faces", 0)),
            ("Duplicates", -2, root_counts.get("duplicates", 0)),
            ("☣️ Corrupted Media", -3, root_counts.get("corrupted", 0)),
        ]
        for p in persons:
            categories.append(
                (p.custom_name or f"Person {p.cluster_id}", p.cluster_id, p.face_count)
            )

        self.library_view.tree_view.initialize_categories(categories)

    def on_tree_load_request(self, item: Any, level: str, params: dict) -> None:
        """Asynchronously loads sub-items for a tree node to prevent UI freeze."""
        logger.info(f"MainWindow: Loading tree sub-items (Level={level})...")
        worker = TreeDataLoadWorker(
            self.db, item, level, params, include_trash=self.config.include_trash
        )
        worker.data_ready.connect(self._on_tree_data_loaded)
        self._track_worker(worker)
        worker.start()

    @Slot(object)
    def _on_tree_data_loaded(self, res: TreeDataLoadResult) -> None:
        if not res.success:
            logger.error(f"MainWindow: Failed to load tree data for {res.level}: {res.message}")
            return

        self.library_view.tree_view.add_sub_items(res.item, res.data, res.level)

    def on_tree_selection(self, item: Any) -> None:
        with Profiler("MainWindow.on_tree_selection"):
            itype = item.data(Qt.UserRole + 2)
            cid, year, month, location = None, None, None, None
            if itype == "category":
                cid = item.data(Qt.UserRole)
            elif itype == "years":
                cid = item.parent().data(Qt.UserRole) if item.parent() else None
                year = item.data(Qt.UserRole + 4)
            elif itype == "months":
                d = item.data(Qt.UserRole + 1)
                if d:
                    cid, year, month = d[:3]
            elif itype == "locations":
                d = item.data(Qt.UserRole + 1)
                if d:
                    cid, year, month, location = d[:4]

            self.current_filter = {
                "cluster_id": cid,
                "year": year,
                "month": month,
                "location": location,
            }
            self.library_view.toolbar.set_duplicate_mode(cid == -2)
            self.show_images_paged()

    def on_dup_filter_changed(self, index: int) -> None:
        self.on_tree_selection(self.library_view.tree_view.currentItem())

    def show_images_paged(self) -> None:
        self.current_offset = 0
        self.last_capture_date = None
        self.last_file_path = None
        self.library_view.clear_grid()
        self.has_more = True
        self.is_loading_more = False
        self.hash_to_id = {}
        self.next_group_id = 1
        self.last_header_key = None
        self.load_next_page()

    def load_next_page(self) -> None:
        if self.is_loading_more or not self.has_more:
            return
        self.is_loading_more = True

        # Use Seek-Markers for explosive performance
        logger.info(f"DataLoader: Requesting next page (Seek: {bool(self.last_capture_date)})")
        start_time = time.perf_counter()

        worker = DataLoaderWorker(
            self.db,
            self.current_filter,
            self.page_size,
            self.current_offset,
            self.config.include_trash,
            self.current_folder,
            self._get_dup_filter(),
            last_capture_date=self.last_capture_date,
            last_file_path=self.last_file_path,
        )
        worker.chunk_ready.connect(self.on_data_chunk_ready)
        worker.finished.connect(
            lambda res: (
                self.on_data_loaded(res),
                logger.info(
                    f"PROFILER: load_next_page (seek={bool(self.last_capture_date)}) took {time.perf_counter() - start_time:.4f}s"
                ),
            )
        )
        worker.error.connect(
            lambda e: (
                setattr(self, "is_loading_more", False),
                self.status_label.setText(f"Error: {e}"),
            )
        )
        self._track_worker(worker)
        worker.start()

    def _get_dup_filter(self) -> Optional[str]:
        idx = self.library_view.toolbar.combo_dup_filter.currentIndex()
        return {1: "exact", 2: "ai_local"}.get(idx)

    @Slot(list)
    def on_data_chunk_ready(self, media_list: list[MediaRecord]) -> None:
        """Handles incremental data arrival for smoother rendering."""
        display_items = []
        for m in media_list:
            thumb_path = m.thumbnail_path
            if not thumb_path:
                thumb_path = self.img_proc.get_thumbnail_path(m.file_path)
                m = replace(m, thumbnail_path=thumb_path)

            ui_group_id = None
            if m.is_duplicate and m.group_id:
                gid = m.group_id.strip().lower()
                if gid not in self.hash_to_id:
                    self.hash_to_id[gid] = self.next_group_id
                    self.next_group_id += 1
                ui_group_id = self.hash_to_id[gid]

            item = LibraryViewItem(media=m, ui_group_id=ui_group_id, selected=False)
            display_items.append(item)

        grouped, self.last_header_key = group_media_by_date_and_location(
            display_items, self.last_header_key
        )
        self.library_view.append_grid_data(grouped)
        self.current_offset += len(media_list)

        # [NEW] Explosive Speed: Trigger asynchronous thumbnail loading into memory
        loader = LibraryThumbnailWorker(media_list)
        loader.batch_finished.connect(
            self.library_view.grid_view.media_model.update_media_image_batch
        )
        self._track_worker(loader)
        loader.start()

    def on_data_loaded(self, res: MediaLoadResult) -> None:
        """Finalizes a page load and updates seek markers."""
        self.has_more = res.has_more
        self.is_loading_more = False

        # Update markers for next seek
        self.last_capture_date = res.last_capture_date
        self.last_file_path = res.last_file_path

        # Automatic next load if it was the very first small page (UX padding)
        if self.current_offset < 50 and self.has_more:
            self.load_next_page()

    def cleanup_duplicates(self) -> None:
        worker = SearchWorker(
            self.db,
            include_trash=self.config.include_trash,
            threshold=self.config.dup_threshold / 10.0,
        )
        worker.finished.connect(self.on_search_finished)
        self._track_worker(worker)
        worker.start()

    def on_search_finished(self, groups: list) -> None:
        if not groups:
            return
        if QMessageBox.Yes == QMessageBox.question(
            self, "Cleanup", f"Process {len(groups)} groups?", QMessageBox.Yes
        ):
            worker = CleanupWorker(groups, self.db, self.current_folder)
            worker.finished.connect(lambda c: (self.initialize_tree(), self.show_images_paged()))
            self._track_worker(worker)
            worker.start()

    def release_selected_from_groups(self) -> None:
        selected = self.library_view.get_selected_files()
        if not selected:
            return

        self.progress_bar.setVisible(True)
        self.set_buttons_enabled(False)
        worker = BatchFileReleaseWorker(self.db, selected)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def on_analysis_finished(self, success: bool, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.set_buttons_enabled(True)
        self.initialize_tree()
        self.show_images_paged()
        if not success:
            QMessageBox.critical(self, "Error", message)

    def on_rename_person(self, old_name: str, new_name: str) -> None:
        for p in self.db.face_repo.get_clusters():
            if (p.custom_name or f"Person {p.cluster_id}") == old_name:
                self.db.face_repo.upsert_cluster(p.cluster_id, new_name)
                self.initialize_tree()
                break

    def open_file(self, path: str) -> None:
        if os.name == "nt":
            os.startfile(path)

    def manage_tag(self, path: str, cid: int, name: str) -> None:
        pass

    def show_thumbnail_context_menu(self, path: str, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("📂 Open File", lambda: self.open_file(path))
        menu.addAction("🗑️ Delete", lambda: self._execute_batch_delete([path]))
        menu.exec(pos)

    def delete_single_file(self, path: str) -> None:
        self._execute_batch_delete([path])

    def delete_selected(self) -> None:
        selected = self.library_view.get_selected_files()
        if not selected:
            QMessageBox.information(self, "No Selection", "Please check at least one photo first.")
            return
        self._execute_batch_delete(selected)

    def _execute_batch_delete(self, paths: list[str]) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Files",
            f"Move {len(paths)} selected files to Recycle Bin?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.progress_bar.setVisible(True)
        self.set_buttons_enabled(False)
        worker = BatchFileDeleteWorker(self.db, paths, self.current_folder)
        worker.progress_val.connect(self.progress_bar.setValue)
        worker.finished_task.connect(self.on_analysis_finished)
        self._track_worker(worker)
        worker.start()

    def select_all_visible(self) -> None:
        self.library_view.select_all_visible()

    def deselect_all_visible(self) -> None:
        self.library_view.deselect_all_visible()

    def clear_selected_tags(self) -> None:
        pass

    def update_selection_ui(self, count: int) -> None:
        self.library_view.toolbar.set_selection_actions_enabled(count > 0)

    def show_settings(self) -> None:
        # Dialog integration...
        pass

    def toggle_face_manager(self, checked: bool) -> None:
        with Profiler(f"MainWindow.toggle_face_manager (checked={checked})"):
            logger.info(f"MainWindow: toggle_face_manager called with checked={checked}")
            self.central_stack.setCurrentIndex(1 if checked else 0)
            self.header.set_face_manager_active(checked)
            if checked:
                logger.info("MainWindow: triggering face_manager_view.refresh_sidebar()")
                self.face_manager.refresh_sidebar()
            else:
                self.initialize_tree()

    def update_force_reanalyze(self, val: bool) -> None:
        self.config.force_reanalyze = val
        self.config.save(self.db.settings_repo)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
