import os

import numpy as np

from core.models import FaceInfo
from core.repositories.face_repository import FaceRepository


def test_face_repository_add_and_get(db):
    repo = FaceRepository(db.db_path)
    # Use mixed case path for Windows normalization test
    path = "C:\\Photos\\Face.JPG"
    vec = np.random.rand(512).astype(np.float32).tobytes()

    # 1. Add faces
    face1 = FaceInfo(
        face_id=0, file_path=path, vector_blob=vec, bbox=[10.0, 10.0, 50.0, 50.0], cluster_id=10
    )
    repo.add_faces_batch([face1])

    # 2. Get faces for file (path normalization check)
    faces = repo.get_faces_for_file(path)
    assert len(faces) == 1
    assert faces[0].file_path == os.path.normcase(os.path.abspath(path))
    assert faces[0].cluster_id == 10


def test_face_repository_cluster_ops(db):
    repo = FaceRepository(db.db_path)
    path = "C:\\Photos\\ClusterTest.JPG"
    vec = b"dummy_vector"

    # Add face
    repo.add_faces_batch([FaceInfo(face_id=0, file_path=path, vector_blob=vec, cluster_id=-1)])
    faces = repo.get_faces_for_file(path)
    fid = faces[0].face_id

    # Update cluster
    repo.update_faces_cluster_batch([(500, fid)])

    # Verify
    faces_updated = repo.get_faces_for_file(path)
    assert faces_updated[0].cluster_id == 500


def test_face_repository_counts(db):
    repo = FaceRepository(db.db_path)
    # Add some diverse faces
    repo.add_faces_batch(
        [
            FaceInfo(face_id=0, file_path="p1.jpg", vector_blob=b"v", cluster_id=1),
            FaceInfo(
                face_id=0, file_path="p1.jpg", vector_blob=b"v", cluster_id=1
            ),  # Same photo, same cluster
            FaceInfo(
                face_id=0, file_path="p2.jpg", vector_blob=b"v", cluster_id=1
            ),  # Diff photo, same cluster
            FaceInfo(face_id=0, file_path="p3.jpg", vector_blob=b"v", cluster_id=-1),  # Unknown
            FaceInfo(
                face_id=0, file_path="p4.jpg", vector_blob=b"v", cluster_id=2, is_ignored=True
            ),  # Ignored
        ]
    )

    counts = repo.get_face_counts()
    # persons[1] should be 2 because it's in p1.jpg and p2.jpg
    assert counts.persons[1] == 2
    assert counts.unknown == 1
    assert counts.ignored == 1
