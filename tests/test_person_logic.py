import os
import sys
import unittest

from processor.person_logic import PersonAction, PersonManagementWorker
from PySide6.QtWidgets import QApplication

from core.database import Database

# Ensure App instance for QThread/Signals
app = QApplication.instance() or QApplication(sys.argv)


class TestPersonLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = "test_logic.db"
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        cls.db = Database(cls.db_path)
        # Setup dummy media and face
        with cls.db.get_connection() as conn:
            import numpy as np

            dummy_vec = np.zeros(512, dtype="float32").tobytes()
            conn.execute("INSERT INTO media (file_path) VALUES ('test.jpg')")
            conn.execute(
                "INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id) VALUES ('test.jpg', ?, '[0,0,10,10]', NULL)",
                (dummy_vec,),
            )
        cls.face_id = 1

    def test_01_register_new(self):
        params = {"face_id": self.face_id, "name": "Test Person"}
        worker = PersonManagementWorker(self.db, PersonAction.REGISTER_NEW, params)

        success_flag = False

        def on_finished(success, msg):
            nonlocal success_flag
            success_flag = success
            if not success:
                print(f"Worker Task Failed: {msg}")

        worker.task_finished.connect(on_finished)
        worker.run()

        self.assertTrue(success_flag)

        # Verify DB
        clusters = self.db.get_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0][1], "Test Person")

        # Verify face association
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT cluster_id, is_ignored FROM faces WHERE face_id = ?", (self.face_id,)
            ).fetchone()
            self.assertEqual(row[0], clusters[0][0])
            self.assertEqual(row[1], 0)

    def test_02_associate_existing(self):
        # Create another cluster
        cid2 = self.db.create_cluster_manual("Second Person")

        params = {"face_id": self.face_id, "cluster_id": cid2}
        worker = PersonManagementWorker(self.db, PersonAction.ASSOCIATE_EXISTING, params)
        worker.run()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT cluster_id FROM faces WHERE face_id = ?", (self.face_id,)
            ).fetchone()
            self.assertEqual(row[0], cid2)

    def test_03_ignore_face(self):
        params = {"face_id": self.face_id}
        worker = PersonManagementWorker(self.db, PersonAction.IGNORE_FACE, params)
        worker.run()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT is_ignored FROM faces WHERE face_id = ?", (self.face_id,)
            ).fetchone()
            self.assertEqual(row[0], 1)

    def test_04_ignore_cluster(self):
        cid = self.db.create_cluster_manual("To Be Ignored")
        # Add another face to this cluster
        with self.db.get_connection() as conn:
            import numpy as np

            dummy_vec = np.zeros(512, dtype="float32").tobytes()
            conn.execute(
                "INSERT INTO faces (file_path, vector_blob, cluster_id) VALUES ('test2.jpg', ?, ?)",
                (dummy_vec, cid),
            )

        params = {"cluster_id": cid}
        worker = PersonManagementWorker(self.db, PersonAction.IGNORE_CLUSTER, params)
        worker.run()

        # Verify cluster table
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT is_ignored FROM clusters WHERE cluster_id = ?", (cid,)
            ).fetchone()
            self.assertEqual(row[0], 1)
            # Verify all faces in cluster are ignored
            rows = conn.execute(
                "SELECT is_ignored FROM faces WHERE cluster_id = ?", (cid,)
            ).fetchall()
            for r in rows:
                self.assertEqual(r[0], 1)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)


if __name__ == "__main__":
    unittest.main()
