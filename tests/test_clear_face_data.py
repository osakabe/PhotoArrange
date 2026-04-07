import os
import shutil
import sys
import tempfile
import unittest

# Add src to path if needed (assuming test is in tests/ folder)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import Database


class TestClearFaceData(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = Database(self.db_path)

        # Populate with sample data
        with self.db.get_connection() as conn:
            # Add clusters
            conn.execute("INSERT INTO clusters (cluster_id, custom_name) VALUES (1, 'Person A')")
            conn.execute("INSERT INTO clusters (cluster_id, custom_name) VALUES (2, 'Person B')")

            # Add faces
            # Folder 1: C:\Photos\Summer
            # Folder 2: C:\Photos\Winter
            self.folder1 = os.path.normcase("C:\\Photos\\Summer")
            self.folder2 = os.path.normcase("C:\\Photos\\Winter")

            faces = [
                (os.path.join(self.folder1, "pic1.jpg"), 1),
                (os.path.join(self.folder1, "pic2.jpg"), 1),
                (os.path.join(self.folder2, "pic3.jpg"), 2),
                (os.path.join(self.folder2, "sub", "pic4.jpg"), 2),
            ]

            for path, cid in faces:
                norm_path = os.path.normcase(os.path.abspath(path))
                conn.execute(
                    "INSERT INTO faces (file_path, cluster_id, vector_blob) VALUES (?, ?, ?)",
                    (norm_path, cid, b"dummy_vector"),
                )
            conn.commit()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_clear_folder_specific(self):
        # Clear data for Folder 1
        self.db.clear_face_data(self.folder1)

        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT file_path FROM faces")
            remaining = [r[0] for r in cursor.fetchall()]

            # Should only have Folder 2 faces
            for path in remaining:
                self.assertIn(self.folder2, path)
                self.assertNotIn(self.folder1, path)

            self.assertEqual(len(remaining), 2)

            # Clusters should still exist
            cursor = conn.execute("SELECT COUNT(*) FROM clusters")
            self.assertEqual(cursor.fetchone()[0], 2)

    def test_clear_global(self):
        # Clear all face data
        self.db.clear_face_data()

        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM faces")
            self.assertEqual(cursor.fetchone()[0], 0)

            cursor = conn.execute("SELECT COUNT(*) FROM clusters")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_path_escaping(self):
        # Test with [ and % in path
        special_folder = os.path.normcase("C:\\Photos\\[Special]%Folder")
        with self.db.get_connection() as conn:
            norm_path = os.path.normcase(os.path.abspath(os.path.join(special_folder, "pic.jpg")))
            conn.execute(
                "INSERT INTO faces (file_path, cluster_id, vector_blob) VALUES (?, ?, ?)",
                (norm_path, 1, b"vector"),
            )
            conn.commit()

        self.db.clear_face_data(special_folder)

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM faces WHERE file_path LIKE ?", (special_folder + "%",)
            )
            self.assertEqual(cursor.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
