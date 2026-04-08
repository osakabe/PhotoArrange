import os
import sqlite3
import threading

from core.repositories.base import BaseRepository


def test_base_repository_initialization(test_data_dir):
    """Test that BaseRepository initializes with correct path and default."""
    # Custom path
    custom_path = os.path.join(test_data_dir, "custom.db")
    repo = BaseRepository(custom_path)
    assert repo.db_path == os.path.normpath(custom_path)

    # Default path
    repo_default = BaseRepository()
    assert "media_cache.db" in repo_default.db_path


def test_base_repository_get_connection(test_data_dir):
    """Test that connection yields WAL mode and busy_timeout."""
    db_path = os.path.join(test_data_dir, "test.db")
    repo = BaseRepository(db_path)

    with repo.get_connection() as conn:
        assert isinstance(conn, sqlite3.Connection)

        # Check journal_mode
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

        # Check busy_timeout
        cursor = conn.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        assert int(timeout) == 5000

    repo.close()


def test_base_repository_thread_local(test_data_dir):
    """Test that connections are thread-local and isolated."""
    db_path = os.path.join(test_data_dir, "test_thread.db")
    repo = BaseRepository(db_path)

    connections = []

    def worker():
        with repo.get_connection() as conn:
            connections.append(conn)
            # Create a table in one thread
            conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
        repo.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(connections) == 2
    # The two threads should have yielded different connection objects
    assert connections[0] is not connections[1]
