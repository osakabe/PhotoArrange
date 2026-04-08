import logging

from PySide6.QtCore import QObject, Signal

from core.config import AppConfig
from core.models import ClusterInfo, MediaRecord
from core.repositories.face_repository import FaceRepository
from core.repositories.media_repository import MediaRepository
from core.repositories.setting_repository import SettingRepository

logger = logging.getLogger("PhotoArrange")


class AppController(QObject):
    """
    Main Logic Controller (ViewModel-ish).
    Orchestrates data flow between UI widgets and repositories.
    """

    # Signals for UI updates
    config_updated = Signal(object)
    data_refresh_requested = Signal()

    def __init__(self, db_path: str = None) -> None:
        super().__init__()
        self.media_repo = MediaRepository(db_path)
        self.face_repo = FaceRepository(db_path)
        self.settings_repo = SettingRepository(db_path)

        self.config = AppConfig.load(self.settings_repo)
        logger.info("AppController initialized.")

    # --- Config Management ---
    def update_config(self, **kwargs) -> None:
        """Updates and persists configuration."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.config.save(self.settings_repo)
        self.config_updated.emit(self.config)

    # --- Media Operations ---
    def get_media_paged(self, **kwargs) -> list[MediaRecord]:
        return self.media_repo.get_media_paged(**kwargs)

    def delete_media(self, file_path: str) -> None:
        self.media_repo.delete_media(file_path)
        self.data_refresh_requested.emit()

    # --- Face/Person Operations ---
    def get_person_list(self) -> list[ClusterInfo]:
        return self.face_repo.get_clusters()

    def rename_person(self, cluster_id: int, new_name: str) -> None:
        self.face_repo.upsert_cluster(cluster_id, name=new_name)
        self.data_refresh_requested.emit()

    def move_face_to_person(self, face_id: int, target_cluster_id: int) -> None:
        self.face_repo.update_face_cluster(face_id, target_cluster_id)
        self.data_refresh_requested.emit()
