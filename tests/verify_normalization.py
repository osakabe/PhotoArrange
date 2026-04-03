import sqlite3
import os
import json

def verify_db():
    db_path = r"c:\Users\osaka\Documents\antigravity\PhotoArrange\photo_app.db"
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return

    # Add core to sys.path to import Database
    sys.path.append(r"c:\Users\osaka\Documents\antigravity\PhotoArrange")
    from core.database import Database
    
    db = Database(db_path)
    
    print("Checking media table schema...")
    with db.get_connection() as conn:
        cursor = conn.execute("PRAGMA table_info(media)")
        cols = [c[1] for c in cursor.fetchall()]
        print(f"Columns: {cols}")
        if 'year' in cols and 'month' in cols:
            print("SUCCEEDED: year and month columns exist.")
        else:
            print("FAILED: year or month column missing.")

    print("\nChecking get_media_paged results...")
    try:
        # Get first 5 media items
        media_list = db.get_media_paged(None, None, None, limit=5)
        if not media_list:
            print("No media found in DB to verify.")
        else:
            for item in media_list:
                print(f"Path: {os.path.basename(item['file_path'])}")
                print(f"  City: {item.get('city')}, Pref: {item.get('prefecture')}, Country: {item.get('country')}")
                print(f"  Year: {item.get('metadata').get('year')} (meta) vs (db: {db.get_media(item['file_path'])[10]})")
                if item.get('city') is None and item.get('prefecture') is None:
                    print("  Note: No location info found for this record (expected if no GPS).")
                else:
                    print("  SUCCEEDED: Location info fetched from l table.")
    except Exception as e:
        print(f"FAILED: get_media_paged error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    verify_db()
