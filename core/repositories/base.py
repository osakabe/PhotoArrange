import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Dict, Generator, Optional

from ..exceptions import DatabaseError, DatabaseLockedError
from ..utils import get_app_data_dir

logger = logging.getLogger("PhotoArrange")


# Thread-local storage for database connections
class DBLocalStorage(threading.local):
    connections: Dict[str, sqlite3.Connection]


_local = DBLocalStorage()


class BaseRepository:
    """
    Base class for all repositories. Handles SQLite connection management and common setup.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            self.db_path: str = os.path.join(get_app_data_dir(), "media_cache.db")
        else:
            self.db_path = os.path.normpath(db_path)

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for thread-local SQLite connections.
        Ensures WAL mode and busy_timeout are applied.
        """
        if not hasattr(_local, "connections"):
            _local.connections = {}

        if self.db_path not in _local.connections:
            try:
                # Ensure directory exists
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                _local.connections[self.db_path] = conn
                logger.debug(
                    f"BaseRepository: Opened new thread-local connection for {self.db_path}"
                )
            except sqlite3.Error as e:
                raise DatabaseError(f"Failed to connect to {self.db_path}: {e}") from e

        conn = _local.connections[self.db_path]
        try:
            yield conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                # Optionally retry or just raise
                raise DatabaseLockedError(f"Database at {self.db_path} is locked: {e}") from e
            raise DatabaseError(f"Database operation failed: {e}") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"General SQLite error: {e}") from e
        # Connection is NOT closed here to allow reuse within the same thread.

    def close(self) -> None:
        """Close the thread-local connection for this database on the current thread."""
        if hasattr(_local, "connections") and self.db_path in _local.connections:
            try:
                _local.connections[self.db_path].close()
                logger.debug(f"BaseRepository: Closed connection for {self.db_path}")
            except sqlite3.Error as e:
                logger.error(f"Error closing DB connection {self.db_path}: {e}")
            finally:
                del _local.connections[self.db_path]
