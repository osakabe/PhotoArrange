import sqlite3
import os

def check_features():
    db_file = os.path.join(os.environ['LOCALAPPDATA'], 'PhotoArrange', 'media_cache.db')
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    print(f"--- Feature check for {db_file} ---")
    
    # Check media_features table
    cursor.execute("SELECT count(*) FROM media_features")
    feature_count = cursor.fetchone()[0]
    print(f"Total entries in media_features: {feature_count}")
    
    cursor.execute("SELECT count(*) FROM media_features WHERE vector_blob IS NOT NULL")
    vector_count = cursor.fetchone()[0]
    print(f"Total entries with vector_blob: {vector_count}")
    
    cursor.execute("SELECT count(*) FROM media_features WHERE salient_blob IS NOT NULL")
    salient_count = cursor.fetchone()[0]
    print(f"Total entries with salient_blob: {salient_count}")
    
    conn.close()

if __name__ == "__main__":
    check_features()
