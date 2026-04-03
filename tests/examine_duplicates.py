import os
import sys
import sqlite3
import json

# Add project dir to path
sys.path.append(os.getcwd())

from core.database import Database

def check_duplicate_groups():
    db = Database("photo_app.db")
    groups = db.get_duplicate_groups()
    print(f"Total duplicate groups: {len(groups)}")
    
    for i, group in enumerate(groups[:10]): # Check first 10 groups
        print(f"\nGroup {i+1} (Hash: {group[0]['group_hash']}):")
        for item in group:
            print(f"  - {item['file_path']}")

if __name__ == "__main__":
    check_duplicate_groups()
