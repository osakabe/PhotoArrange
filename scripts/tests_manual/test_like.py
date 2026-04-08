import os
import sqlite3


def test_like_matching():
    db_file = os.path.join(os.environ["LOCALAPPDATA"], "PhotoArrange", "media_cache.db")
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    root_folder = r"C:\Users\osaka\Documents\Photos\Amazon Photos"
    norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
    pattern = norm_root.replace("[", "[[]").replace("%", "[%]") + "%"

    print(f"Norm root: {norm_root}")
    print(f"Pattern: {pattern}")

    # Test query
    query = "SELECT count(*) FROM media WHERE file_path LIKE ? COLLATE NOCASE"
    cursor.execute(query, (pattern,))
    count = cursor.fetchone()[0]
    print(f"Match count: {count}")

    # Test group query
    query2 = (
        "SELECT count(*) FROM media WHERE group_id IS NOT NULL AND file_path LIKE ? COLLATE NOCASE"
    )
    cursor.execute(query2, (pattern,))
    count2 = cursor.fetchone()[0]
    print(f"Match count with group_id: {count2}")

    conn.close()


if __name__ == "__main__":
    test_like_matching()
