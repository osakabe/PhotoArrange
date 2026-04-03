import sqlite3
import os
import sys

def backfill():
    db_path = r"c:\Users\osaka\Documents\antigravity\PhotoArrange\photo_app.db"
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        # Check if columns exist
        cursor = conn.execute("PRAGMA table_info(media)")
        cols = [c[1] for c in cursor.fetchall()]
        if 'year' not in cols or 'month' not in cols:
            print("Columns 'year' or 'month' missing. Adding them...")
            if 'year' not in cols: conn.execute("ALTER TABLE media ADD COLUMN year INTEGER")
            if 'month' not in cols: conn.execute("ALTER TABLE media ADD COLUMN month INTEGER")

        print("Backfilling year and month from capture_date...")
        # SQLite's STRFTIME works with ISO strings.
        # Format in DB should be ISO 'YYYY-MM-DD HH:MM:SS'
        conn.execute("""
            UPDATE media SET 
                year = CAST(STRFTIME('%Y', capture_date) AS INTEGER),
                month = CAST(STRFTIME('%m', capture_date) AS INTEGER)
            WHERE capture_date IS NOT NULL AND (year IS NULL OR year = 0)
        """)
        
        # Fallback for those that have metadata_json but no capture_date column filled
        print("Checking metadata_json for missing capture_date/year/month...")
        cursor = conn.execute("SELECT file_path, capture_date, metadata_json FROM media WHERE (year IS NULL OR year = 0)")
        rows = cursor.fetchall()
        import json
        for path, cap_date, meta_json in rows:
            meta = json.loads(meta_json) if meta_json else {}
            y = meta.get('year')
            m = meta.get('month')
            if not y or not m:
                # Try to parse from date_taken
                dt = meta.get('date_taken')
                if dt and len(dt) >= 10:
                    try:
                        # '2023:10:15 ...' or '2023-10-15 ...'
                        y = int(dt[:4])
                        m = int(dt[5:7])
                    except:
                        pass
            if y and m:
                conn.execute("UPDATE media SET year = ?, month = ? WHERE file_path = ?", (y, m, path))

        conn.commit()
        print("Backfill complete.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    backfill()
