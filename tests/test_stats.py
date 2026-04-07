import os
import sqlite3


def test_stats(db_file, root_folder):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    include_trash = False
    where_media = ["m.is_in_trash = 0"] if not include_trash else []
    params = []
    if root_folder:
        norm_root = os.path.abspath(os.path.normpath(root_folder)) + os.path.sep
        pattern = norm_root.replace("[", "[[]").replace("%", "[%]") + "%"
        where_media.append("m.file_path LIKE ? COLLATE NOCASE")
        params.append(pattern)

    where_clause = (" WHERE " + " AND ".join(where_media)) if where_media else ""
    query = f"SELECT dg.group_id, dg.discovery_method FROM duplicate_groups dg JOIN media m ON dg.group_id = m.group_id {where_clause} GROUP BY dg.group_id HAVING COUNT(*) > 1"

    print(f"Query: {query}")
    print(f"Params: {params}")

    cursor.execute(query, params)
    rows = cursor.fetchall()
    print(f"Stats rows: {rows}")

    # Try without HAVING COUNT(*) > 1 to see what's being grouped
    query_no_having = f"SELECT dg.group_id, COUNT(*) FROM duplicate_groups dg JOIN media m ON dg.group_id = m.group_id {where_clause} GROUP BY dg.group_id"
    cursor.execute(query_no_having, params)
    print(f"Groups counts: {cursor.fetchall()}")

    conn.close()


if __name__ == "__main__":
    db = "test_media_v32.db"
    root = r"C:\Users\osaka\Documents\antigravity\PhotoArrange"
    test_stats(db, root)
