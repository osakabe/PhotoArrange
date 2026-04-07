import os
import sqlite3


def check_db():
    db_path = "photo_app.db"
    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("--- Database Content Audit ---")

    # 1. Total faces
    cursor.execute("SELECT COUNT(*) FROM faces")
    total_faces = cursor.fetchone()[0]
    print(f"Total faces: {total_faces}")

    # 2. Unknown faces
    cursor.execute(
        "SELECT COUNT(*) FROM faces WHERE (cluster_id IS NULL OR cluster_id = -1) AND is_ignored = 0"
    )
    unknown_faces = cursor.fetchone()[0]
    print(f"Unknown faces (not ignored): {unknown_faces}")

    # 3. Ignored faces
    cursor.execute("SELECT COUNT(*) FROM faces WHERE is_ignored = 1")
    ignored_faces = cursor.fetchone()[0]
    print(f"Ignored faces: {ignored_faces}")

    # 4. Verified Person clusters
    cursor.execute("SELECT cluster_id, custom_name, is_ignored FROM clusters")
    clusters = cursor.fetchall()
    print(f"Total clusters in DB: {len(clusters)}")
    for cid, name, ignored in clusters:
        cursor.execute("SELECT COUNT(*) FROM faces WHERE cluster_id = ? AND is_ignored = 0", (cid,))
        cnt = cursor.fetchone()[0]
        print(f"  - Cluster {cid}: {name or 'N/A'} | Ignored: {ignored} | Faces: {cnt}")

    # 5. Check if any faces have invalid cluster_ids
    cursor.execute(
        "SELECT COUNT(*) FROM faces WHERE cluster_id NOT IN (SELECT cluster_id FROM clusters) AND cluster_id IS NOT NULL AND cluster_id != -1"
    )
    orphaned_faces = cursor.fetchone()[0]
    if orphaned_faces > 0:
        print(
            f"WARNING: Found {orphaned_faces} orphaned faces (cluster_id exists but not in clusters table)."
        )
    else:
        print("Data Integrity: No orphaned faces found.")

    conn.close()


if __name__ == "__main__":
    check_db()
