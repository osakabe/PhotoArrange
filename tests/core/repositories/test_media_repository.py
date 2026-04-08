import os

from core.models import MediaRecord
from core.repositories.media_repository import MediaRepository


def test_media_repository_add_and_get(db):
    repo = MediaRepository(db.db_path)
    # Using mixed case path to test normalization
    path = "C:\\Photos\\Image.JPG"
    m = MediaRecord(
        file_path=path,
        last_modified=123.0,
        metadata={"width": 100},
        capture_date="2024:01:01 12:00:00",
        file_hash="hash1",
        year=2024,
        month=1,
    )
    repo.add_media_batch([m])

    # Get using original path
    fetched = repo.get_media(path)
    assert fetched is not None
    # Path should be normalized in DB (lowercase on Windows)
    assert fetched.file_path == os.path.normcase(os.path.abspath(path))
    assert fetched.year == 2024
    assert fetched.metadata["width"] == 100


def test_media_repository_on_conflict_coalesce(db):
    repo = MediaRepository(db.db_path)
    path = "C:\\Photos\\Conflict.JPG"

    # 1. Initial insert with hash
    m1 = MediaRecord(file_path=path, file_hash="InitialHash")
    repo.add_media_batch([m1])

    # 2. Update without hash (file_hash=None)
    m2 = MediaRecord(file_path=path, file_hash=None, last_modified=456.0)
    repo.add_media_batch([m2])

    # 3. Verify hash is NOT wiped
    fetched = repo.get_media(path)
    assert fetched.file_hash == "InitialHash"
    assert fetched.last_modified == 456.0


def test_media_repository_paged_query(populated_db):
    repo = MediaRepository(populated_db.db_path)
    # Test filtering by cluster_id (from populated_db)
    # populated_db has 50 faces with cluster_id=100
    results = repo.get_media_paged(cluster_id=100, year=None, month=None, limit=10)
    assert len(results) > 0
    # Every result should belong to cluster 100 (this is checked by the JOIN in get_media_paged)

    # Test root folder pattern
    # All dummy images in populated_db have the same path from test_data_dir
    # We need to get the test_data_dir to test folder pattern
    # But get_media_paged uses LIKE pattern
    all_media = repo.get_all_media_paths()
    if all_media:
        folder = os.path.dirname(all_media[0])
        print(f"DEBUG: all_media[0]={all_media[0]}")
        print(f"DEBUG: folder={folder}")
        results_folder = repo.get_media_paged(
            cluster_id=None, year=None, month=None, root_folder=folder, limit=10
        )
        print(f"DEBUG: results_folder len={len(results_folder)}")
        assert len(results_folder) > 0
