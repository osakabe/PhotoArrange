from typing import Any

from .base import BaseRepository


class SettingRepository(BaseRepository):
    """
    Handles application-wide settings persisted in the database.
    """

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default

    def save_setting(self, key: str, value: Any) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
            )
            conn.commit()

    def get_all_settings(self) -> dict[str, str]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT key, value FROM settings")
            return {row[0]: row[1] for row in cursor.fetchall()}
