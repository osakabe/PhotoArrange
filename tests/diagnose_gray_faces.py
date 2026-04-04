import sys
import os
import sqlite3
import json
import cv2
import numpy as np
from PIL import Image

# Add current directory to path so we can import FaceCropWorker logic if needed
sys.path.append(os.getcwd())

def diagnose_faces(face_ids):
    db_path = os.path.join(os.environ['USERPROFILE'], '.gemini', 'antigravity', 'media_cache.db')
    print(f"Connecting to DB: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Cache directory
    cache_dir = os.path.join(os.environ['USERPROFILE'], '.gemini', 'antigravity', 'face_cache')
    print(f"Cache Directory: {cache_dir}")

    for fid in face_ids:
        print(f"\n--- Diagnosing Face ID: {fid} ---")
        row = conn.execute("SELECT * FROM faces WHERE face_id = ?", (fid,)).fetchone()
        
        if not row:
            print(f"FAILED: ID {fid} not found in database.")
            continue
            
        file_path = row['file_path']
        bbox_json = row['bbox_json']
        cluster_id = row['cluster_id']
        is_ignored = row['is_ignored']
        frame_idx = row.get('frame_index', 0)
        
        print(f"File Path: {file_path}")
        print(f"BBox JSON: {bbox_json}")
        print(f"Cluster ID: {cluster_id}")
        
        if not os.path.exists(file_path):
            print(f"CRITICAL: File DOES NOT EXIST at path: {file_path}")
            # Try normalized path check
            norm_path = os.path.normpath(file_path)
            if not os.path.exists(norm_path):
                 print(f"Normalized path also fails: {norm_path}")
            else:
                 print(f"Normalized path WORKS: {norm_path}. This indicates a path normalization bug in the app.")
            continue

        try:
            bbox = json.loads(bbox_json)
        except Exception as e:
            print(f"ERROR: Failed to parse bbox JSON: {e}")
            continue

        # Try to generate crop
        cache_file = os.path.join(cache_dir, f"face_{fid}.jpg")
        print(f"Target Cache: {cache_file}")
        
        try:
            is_video = file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
            source_img = None
            
            if is_video:
                print(f"Processing as VIDEO frame {frame_idx}")
                cap = cv2.VideoCapture(file_path)
                if not cap.isOpened():
                    print("ERROR: VideoCapture could not open file.")
                else:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
                    ret, frame = cap.read()
                    cap.release()
                    if ret: 
                        source_img = frame
                        print("SUCCESS: Read video frame.")
                    else:
                        print("ERROR: Failed to read video frame.")
            else:
                print("Processing as IMAGE")
                try:
                    # Use PIL for better rotation support
                    with Image.open(file_path) as pil_img:
                        from PIL import ImageOps
                        pil_img = ImageOps.exif_transpose(pil_img)
                        source_img = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2BGR)
                        print("SUCCESS: Read image via PIL.")
                except Exception as ex:
                    print(f"PIL fallback: {ex}. Trying cv2 directly...")
                    source_img = cv2.imread(file_path)
                    if source_img is not None:
                        print("SUCCESS: Read image via cv2.")
                    else:
                        print("ERROR: Failed to read image via cv2.")

            if source_img is not None:
                ih, iw = source_img.shape[:2]
                x1, y1, x2, y2 = bbox
                # Perform crop
                crop = source_img[int(y1):int(y2), int(x1):int(x2)]
                if crop.size == 0:
                    print(f"ERROR: Crop resulted in 0 size. BBox: {bbox}, Image Shape: {ih}, {iw}")
                else:
                    print(f"SUCCESS: Generated crop of size {crop.shape}. Saving...")
                    if not os.path.exists(cache_dir):
                        os.makedirs(cache_dir)
                    cv2.imwrite(cache_file, crop)
                    if os.path.exists(cache_file):
                        print(f"VERIFIED: Cache file created at {cache_file}")
            
        except Exception as e:
            print(f"UNEXPECTED ERROR during processing: {e}")

    conn.close()

if __name__ == "__main__":
    ids = [2141, 2125, 2119, 2116, 2111, 2107, 2105, 2100]
    diagnose_faces(ids)
