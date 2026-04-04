import os
import sys
import unittest
import numpy as np
import json
import shutil
import tempfile

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import Database

class TestDatabaseFaceExtensions(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_media.db")
        self.db = Database(self.db_path)
        
        # Populate with mock faces
        # face_id will be auto-incremented: 1, 2, 3, 4
        faces = [
            ("path/to/unknown.jpg", np.zeros(128, dtype=np.float32).tobytes(), json.dumps([10, 10, 50, 50])), # ID 1: Unknown
            ("path/to/person1_a.jpg", np.zeros(128, dtype=np.float32).tobytes(), json.dumps([10, 10, 50, 50])), # ID 2: Person 100
            ("path/to/person1_b.jpg", np.zeros(128, dtype=np.float32).tobytes(), json.dumps([10, 10, 50, 50])), # ID 3: Person 100
            ("path/to/ignored.jpg", np.zeros(128, dtype=np.float32).tobytes(), json.dumps([10, 10, 50, 50])), # ID 4: Ignored
        ]
        
        with self.db.get_connection() as conn:
            for f in faces:
                conn.execute("INSERT INTO faces (file_path, vector_blob, bbox_json) VALUES (?, ?, ?)", f)
            
            # Set cluster_ids and ignored flags manually for test setup
            conn.execute("UPDATE faces SET cluster_id = ? WHERE face_id = ?", (100, 2))
            conn.execute("UPDATE faces SET cluster_id = ? WHERE face_id = ?", (100, 3))
            conn.execute("UPDATE faces SET is_ignored = 1 WHERE face_id = ?", (4,))
            conn.commit()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_get_face_counts(self):
        counts = self.db.get_face_counts()
        print(f"\nCounts: {counts}")
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(counts["ignored"], 1)
        self.assertEqual(counts["persons"][100], 2)

    def test_get_faces_by_category_unknown(self):
        faces = self.db.get_faces_by_category('unknown')
        self.assertEqual(len(faces), 1)
        self.assertEqual(faces[0]["face_id"], 1)
        self.assertFalse(faces[0]["is_ignored"])

    def test_get_faces_by_category_ignored(self):
        faces = self.db.get_faces_by_category('ignored')
        self.assertEqual(len(faces), 1)
        self.assertEqual(faces[0]["face_id"], 4)
        self.assertTrue(faces[0]["is_ignored"])

    def test_get_faces_by_category_person(self):
        faces = self.db.get_faces_by_category('person', person_id=100)
        self.assertEqual(len(faces), 2)
        ids = [f["face_id"] for f in faces]
        self.assertIn(2, ids)
        self.assertIn(3, ids)

    def test_update_face_association(self):
        # Move Face 1 (Unknown) to Person 200
        self.db.update_face_association(1, 200)
        counts = self.db.get_face_counts()
        self.assertEqual(counts["unknown"], 0)
        self.assertEqual(counts["persons"][200], 1)
        
        # Mark Face 2 as ignored
        self.db.update_face_association(2, 100, is_ignored=True)
        counts = self.db.get_face_counts()
        self.assertEqual(counts["ignored"], 2)
        self.assertEqual(counts["persons"][100], 1)

if __name__ == "__main__":
    unittest.main()
