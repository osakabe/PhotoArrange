import os
import sqlite3


def check_path_format():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM media LIMIT 1")
    path = cursor.fetchone()[0]
    print(f"Path format in DB: {path}")
    conn.close()


if __name__ == "__main__":
    check_path_format()
