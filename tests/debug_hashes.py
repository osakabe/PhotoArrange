import os
import sys
import sqlite3

# Add project dir to path
sys.path.append(os.getcwd())

from core.database import Database

def debug_hashes():
    conn = sqlite3.connect("photo_app.db")
    cursor = conn.execute("SELECT image_hash, COUNT(*) FROM media GROUP BY image_hash HAVING COUNT(*) > 1")
    rows = cursor.fetchall()
    print(f"Duplicate Hashes found: {len(rows)}")
    for h, count in rows:
        print(f"Hash: {h} | Count: {count}")
        # Get one example path
        c2 = conn.execute("SELECT file_path FROM media WHERE image_hash = ? LIMIT 1", (h,))
        print(f"  Example: {c2.fetchone()[0]}")
    conn.close()

if __name__ == "__main__":
    debug_hashes()
