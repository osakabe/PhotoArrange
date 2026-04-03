import sqlite3
import os

db_path = 'photo_app.db'
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found.")
    exit(1)

conn = sqlite3.connect(db_path)
print("--- Clustered Duplicate Summary ---")

# Get counts
query = "SELECT image_hash, COUNT(*) FROM media WHERE image_hash IS NOT NULL AND image_hash != '' GROUP BY image_hash HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC"
rows = conn.execute(query).fetchall()

print(f"Total Duplicate Groups: {len(rows)}")
for h, count in rows[:10]:
    print(f"Hash: {h} | Count: {count}")
    # Get paths for this specific hash
    p_query = "SELECT file_path FROM media WHERE image_hash = ?"
    paths = [r[0] for r in conn.execute(p_query, (h,)).fetchall()]
    for p in paths[:3]:
        print(f"  - {os.path.basename(p)}")
    if len(paths) > 3:
        print(f"  ... (+{len(paths)-3} more)")

conn.close()
