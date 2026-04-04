import sys
import os
sys.path.append(os.getcwd())
from core.database import Database
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def debug_faces():
    db = Database()
    logger.info("Checking face counts...")
    counts = db.get_face_counts()
    logger.info(f"Counts: {counts}")

    logger.info("Checking person list...")
    persons = db.get_person_list_with_counts()
    logger.info(f"Persons count: {len(persons)}")
    for p in persons[:5]: logger.info(f"Person: {p}")

    logger.info("Checking all face dates...")
    all_dates = db.get_all_face_dates()
    logger.info(f"All dates keys: {list(all_dates.keys())}")
    
    if all_dates:
        for k in list(all_dates.keys())[:5]:
            logger.info(f"Date sample for key {k}: {all_dates[k][:3]}")

if __name__ == "__main__":
    debug_faces()
