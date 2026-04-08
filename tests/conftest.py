import json
import os
import shutil
import tempfile

import numpy as np
import pytest
from PIL import Image

from core.database import Database
from core.repositories.face_repository import FaceRepository


@pytest.fixture(autouse=True)
def mock_model_manager(mocker):
    """Prevent heavy AI model loading during unit/GUI tests."""
    mock = mocker.patch("processor.workers.ModelManager")
    return mock


from core.utils import normalize_path


@pytest.fixture(scope="session")
def test_data_dir():
    """Create a temporary directory for test data (images, db)."""
    tmp_dir = tempfile.mkdtemp(prefix="photo_arrange_test_")
    yield tmp_dir
    shutil.rmtree(tmp_dir)


@pytest.fixture
def db(test_data_dir):
    """Provide a fresh, initialized Database instance in a temp file."""
    db_path = os.path.normcase(os.path.abspath(os.path.join(test_data_dir, "test_media.db")))
    if os.path.exists(db_path):
        os.remove(db_path)

    db = Database(db_path)
    # Ensure tables exist (Database init usually does this)
    yield db
    db.close()


@pytest.fixture
def repo(db):
    """Provide a FaceRepository instance."""
    return FaceRepository(db.db_path)


@pytest.fixture
def populated_db(db, test_data_dir):
    """Populate the database with dummy media and faces for UI testing."""
    # Create dummy images
    dummy_img_path = normalize_path(os.path.join(test_data_dir, "dummy.jpg"))
    img = Image.new("RGB", (100, 100), color=(100, 100, 100))
    img.save(dummy_img_path, "JPEG")

    count = 500  # Sufficient for responsiveness tests without being too slow to setup
    faces = []

    with db.get_connection() as conn:
        # 1. Insert Media
        conn.execute(
            "INSERT INTO media (file_path, file_hash, capture_date, is_corrupted) VALUES (?, ?, ?, 0)",
            (dummy_img_path, "dummy_hash", "2024:01:01 00:00:00"),
        )

        # 2. Insert Faces
        for i in range(count):
            day = (i // 10) % 28 + 1
            date_str = f"2024:01:{day:02d} 12:00:00"
            vec = np.random.rand(512).astype(np.float32).tobytes()
            bbox = json.dumps([0, 0, 50, 50])
            cluster_id = 100 if i < 50 else -1  # First 50 belong to Person 100
            faces.append((dummy_img_path, vec, bbox, cluster_id, 0, date_str))

        # 3. Insert Cluster metadata
        conn.execute(
            "INSERT INTO clusters (cluster_id, custom_name) VALUES (?, ?)", (100, "Person 100")
        )

        conn.executemany(
            "INSERT INTO faces (file_path, vector_blob, bbox_json, cluster_id, is_ignored, capture_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            faces,
        )
        conn.commit()

    yield db
