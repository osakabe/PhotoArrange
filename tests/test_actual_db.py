import os
import sys

# Add the project root to sys.path
sys.path.append(os.getcwd())

from core.database import Database

def test_actual_db_call():
    db = Database()
    print(f"DB Path: {db.db_path}")
    
    # Simulate UI selection: Duplicates category
    include_trash = False
    # Use EXACTLY what the user might have selected
    # If the user hasn't selected a folder, it's None
    root_folder = None 
    discovery_filter = None
    
    stats = db.get_duplicate_stats(include_trash=include_trash,
                                  root_folder=root_folder,
                                  discovery_filter=discovery_filter)
    print(f"Stats: {stats}")
    
    # Simulate data loading
    f = {"cluster_id": -2, "year": None, "month": None, "location": None}
    media = db.get_media_paged(f["cluster_id"], f["year"], f["month"], f["location"],
                               limit=100, offset=0,
                               include_trash=include_trash,
                               root_folder=root_folder,
                               discovery_filter=discovery_filter)
    print(f"Media count: {len(media)}")
    if media:
        print(f"Sample media item: {media[0]}")

if __name__ == "__main__":
    test_actual_db_call()
