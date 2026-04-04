import logging
from PySide6.QtCore import QThread, Signal, Slot
from core.database import Database

logger = logging.getLogger("PhotoArrange")

class PersonAction:
    REGISTER_NEW = "register_new"
    ASSOCIATE_EXISTING = "associate_existing"
    IGNORE_FACE = "ignore_face"
    IGNORE_CLUSTER = "ignore_cluster"
    UNIGNORE_CLUSTER = "unignore_cluster"
    RENAME_PERSON = "rename_person"

class PersonManagementWorker(QThread):
    """
    Worker thread to handle person management tasks asynchronously.
    Prevents UI lag when updating large batches of face associations.
    """
    task_finished = Signal(bool, str)
    refresh_requested = Signal()

    def __init__(self, db: Database, action_type: str, params: dict):
        super().__init__()
        self.db = db
        self.action_type = action_type
        self.params = params

    def run(self):
        try:
            if self.action_type == PersonAction.REGISTER_NEW:
                self._handle_register_new()
            elif self.action_type == PersonAction.ASSOCIATE_EXISTING:
                self._handle_associate_existing()
            elif self.action_type == PersonAction.IGNORE_FACE:
                self._handle_ignore_face()
            elif self.action_type == PersonAction.IGNORE_CLUSTER:
                self._handle_ignore_cluster()
            elif self.action_type == PersonAction.UNIGNORE_CLUSTER:
                self._handle_unignore_cluster()
            elif self.action_type == PersonAction.RENAME_PERSON:
                self._handle_rename_person()
            else:
                raise ValueError(f"Unknown action type: {self.action_type}")

            self.task_finished.emit(True, f"Action '{self.action_type}' completed successfully.")
            self.refresh_requested.emit()
            
        except Exception as e:
            logger.exception(f"Error in PersonManagementWorker ({self.action_type}):")
            self.task_finished.emit(False, str(e))

    def _handle_rename_person(self):
        """Changes the name of a cluster."""
        cluster_id = self.params.get("cluster_id")
        name = self.params.get("name")
        if cluster_id is None or name is None:
            raise ValueError("Missing 'cluster_id' or 'name' for RENAME_PERSON")
        
        # upsert_cluster handles rename/merge
        self.db.upsert_cluster(cluster_id, name.strip())
        logger.info(f"Renamed cluster {cluster_id} to '{name}'")

    def _handle_register_new(self):
        """Creates a new person and links faces to it."""
        face_ids = self.params.get("face_ids")
        # Legacy support for single face_id
        if face_ids is None and self.params.get("face_id"):
            face_ids = [self.params.get("face_id")]

        name = self.params.get("name")
        if not face_ids or not name:
            raise ValueError("Missing 'face_ids' or 'name' for REGISTER_NEW")

        # 1. Create cluster
        new_cid = self.db.create_cluster_manual(name)
        # 2. Link faces
        for fid in face_ids:
            self.db.update_face_association(fid, person_id=new_cid, is_ignored=False)
        logger.info(f"Registered new person '{name}' (ID: {new_cid}) and linked {len(face_ids)} faces")

    def _handle_associate_existing(self):
        """Links faces to an existing person."""
        face_ids = self.params.get("face_ids")
        if face_ids is None and self.params.get("face_id"):
            face_ids = [self.params.get("face_id")]

        target_cid = self.params.get("cluster_id")
        if not face_ids or target_cid is None:
            raise ValueError("Missing 'face_ids' or 'cluster_id' for ASSOCIATE_EXISTING")

        for fid in face_ids:
            self.db.update_face_association(fid, person_id=target_cid, is_ignored=False)
        logger.info(f"Associated {len(face_ids)} faces with existing person ID: {target_cid}")

    def _handle_ignore_face(self):
        """Ignores specific faces."""
        face_ids = self.params.get("face_ids")
        if face_ids is None and self.params.get("face_id"):
            face_ids = [self.params.get("face_id")]

        if not face_ids:
            raise ValueError("Missing 'face_ids' for IGNORE_FACE")

        for fid in face_ids:
            self.db.update_face_association(fid, person_id=None, is_ignored=True)
        logger.info(f"Marked {len(face_ids)} faces as ignored.")
    def _handle_ignore_cluster(self):
        """Ignores a whole person/cluster."""
        cluster_id = self.params.get("cluster_id")
        if cluster_id is None:
            raise ValueError("Missing 'cluster_id' for IGNORE_CLUSTER")
        
        self.db.set_cluster_ignored(cluster_id, is_ignored=True)
        logger.info(f"Marked cluster {cluster_id} and all its faces as ignored.")

    def _handle_unignore_cluster(self):
        """Restores an ignored person/cluster."""
        cluster_id = self.params.get("cluster_id")
        if cluster_id is None:
            raise ValueError("Missing 'cluster_id' for UNIGNORE_CLUSTER")
        
        self.db.set_cluster_ignored(cluster_id, is_ignored=False)
        logger.info(f"Restored cluster {cluster_id} from ignored status.")
