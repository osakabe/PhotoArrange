import sys
import os
import sqlite3
import json

# DLL and environment fixes for InsightFace/PyTorch on Windows
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.utils import fix_dll_search_path
fix_dll_search_path()

from core.database import Database
from processor.duplicate_manager import DuplicateManager

def test_media_indices():
    db = Database("test_media.db")
    # Clean up
    if os.path.exists("test_media.db"):
        os.remove("test_media.db")
        os.remove("test_media.db-wal") if os.path.exists("test_media.db-wal") else None
        os.remove("test_media.db-shm") if os.path.exists("test_media.db-shm") else None
    
    db = Database("test_media.db")
    
    path = os.path.normcase(os.path.abspath("test_image.jpg"))
    # Insert a mock media
    with db.get_connection() as conn:
        conn.execute('''
            INSERT INTO media (file_path, last_modified, metadata_json, group_id, capture_date, file_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (path, 12345.0, '{"size": 100}', "hash1", "2023-01-01", "md5_123"))
        conn.commit()
    
    # Test get_media
    media = db.get_media(path)
    print(f"Media tuple length: {len(media)}")
    print(f"Media tuple: {media}")
    
    # Expected indices in get_media (19 columns):
    # 0: file_path
    # 2: metadata_json
    # 3: group_id
    # 13: is_corrupted
    # 14: is_in_trash
    # 15: capture_date
    # 16: file_hash
    
    print(f"Path: {media[0]}")
    print(f"Group ID: {media[3]}")
    print(f"In Trash: {media[14]}")
    print(f"Capture Date: {media[15]}")
    print(f"File Hash: {media[16]}")
    
    assert media[0] == path
    assert media[3] == "hash1"
    assert media[16] == "md5_123"
    
    # Test DuplicateManager with this media
    mgr = DuplicateManager(db, None)
    # mgr.mark_file_as_trashed expects a dictionary 'item'
    item = {
        "file_path": path,
        "last_modified": 12345.0,
        "metadata": {"size": 100, "lat": 0, "lon": 0},
        "group_id": "hash1",
        "thumbnail_path": "thumb.jpg",
        "file_hash": "md5_123",
        "discovery_method": "exact"
    }
    
    new_path = path + ".trash"
    mgr.mark_file_as_trashed(path, new_path, item)
    
    media_trashed = db.get_media(new_path)
    print(f"Trashed Media tuple length: {len(media_trashed)}")
    print(f"Trashed Media tuple: {media_trashed}")
    print(f"Trashed File Hash: {media_trashed[16]}")
    
    assert media_trashed[14] == 1 # is_in_trash
    assert media_trashed[16] == "md5_123" # file_hash should be correctly preserved
    
    print("Verification Successful!")
    
    # Cleanup
    if os.path.exists("test_media.db"):
        os.remove("test_media.db")
        os.remove("test_media.db-wal") if os.path.exists("test_media.db-wal") else None
        os.remove("test_media.db-shm") if os.path.exists("test_media.db-shm") else None

if __name__ == "__main__":
    test_media_indices()
