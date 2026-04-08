import logging
import os

from core.database import Database

# Setup basic logging to see the "IDs reset" message
logging.basicConfig(level=logging.INFO)


def verify_reset():
    db_path = f"test_face_reset_{os.getpid()}.db"  # Unique name
    if os.path.exists(db_path):
        os.remove(db_path)

    # Initialize DB
    db = Database(db_path)

    print("--- 1. Populating Initial Data ---")
    # Avoid nested connections that cause locking issues
    with db.get_connection() as conn:
        # Add some media
        conn.execute(
            "INSERT INTO media (file_path, last_modified, is_in_trash) VALUES (?, ?, ?)",
            ("c:/photo1.jpg", 1000.0, 0),
        )
        conn.commit()

    # Call methods that handle their own connections outside of manual blocks
    db.upsert_cluster(None, "Alice")
    db.upsert_cluster(None, "Bob")

    with db.get_connection() as conn:
        # Add some faces
        # Need to use normalized path to match faces table expectations in real app
        # Provide dummy vector_blob to satisfy NOT NULL constraint
        conn.execute(
            "INSERT INTO faces (file_path, cluster_id, vector_blob, bbox_json) VALUES (?, ?, ?, ?)",
            ("c:/photo1.jpg", 1, b"\x00" * 2048, "[]"),
        )

        # Add some ignored vectors
        conn.execute(
            "INSERT INTO ignored_person_vectors (id, vector_blob) VALUES (?, ?)",
            (1, b"\x00" * 2048),
        )
        conn.commit()

    # Verify presence
    with db.get_connection() as conn:
        face_count = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        ignored_count = conn.execute("SELECT COUNT(*) FROM ignored_person_vectors").fetchone()[0]
        print(
            f"Before reset: Faces={face_count}, Clusters={cluster_count}, Ignored={ignored_count}"
        )
        assert face_count == 1
        assert cluster_count == 2
        assert ignored_count == 1

    print("\n--- 2. Performing Global Reset ---")
    db.clear_face_data(folder_path=None)

    # Verify tables are empty
    with db.get_connection() as conn:
        face_count = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        ignored_count = conn.execute("SELECT COUNT(*) FROM ignored_person_vectors").fetchone()[0]
        print(f"After reset: Faces={face_count}, Clusters={cluster_count}, Ignored={ignored_count}")
        assert face_count == 0
        assert cluster_count == 0
        assert ignored_count == 0

        # Verify sqlite_sequence is cleared
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_sequence WHERE name IN ('faces', 'clusters', 'ignored_person_vectors')"
        )
        seq_count = cursor.fetchone()[0]
        print(f"Sequence entries remaining: {seq_count}")
        assert seq_count == 0

    print("\n--- 3. Verifying ID Reset to 1 ---")
    # Adding a new person should get ID 1
    # upsert_cluster returns True if merged, False otherwise.
    # But for a brand new cluster it just inserts.
    db.upsert_cluster(None, "New Person")

    with db.get_connection() as conn:
        cid = conn.execute(
            "SELECT cluster_id FROM clusters WHERE custom_name='New Person'"
        ).fetchone()[0]
        print(f"New person ID: {cid}")
        assert cid == 1

    print("\nSUCCESS: Face data reset and ID sequence reset verified.")

    # Cleanup
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            # Also clean up WAL/SHM files
            if os.path.exists(db_path + "-wal"):
                os.remove(db_path + "-wal")
            if os.path.exists(db_path + "-shm"):
                os.remove(db_path + "-shm")
        except:
            pass


if __name__ == "__main__":
    try:
        verify_reset()
    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")
        import traceback

        traceback.print_exc()
        exit(1)
