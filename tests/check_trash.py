import os
import sqlite3


def check_trash():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Trash check for {db_file} ---")

    cursor.execute("SELECT count(*) FROM media WHERE is_in_trash = 1")
    trash_count = cursor.fetchone()[0]
    print(f"Total media in trash: {trash_count}")

    cursor.execute("SELECT count(*) FROM media WHERE is_in_trash = 0")
    active_count = cursor.fetchone()[0]
    print(f"Total active media: {active_count}")

    conn.close()


if __name__ == "__main__":
    check_trash()
