import os
import cv2
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import imagehash
import hashlib
from datetime import datetime
import subprocess
import json
import re

from core.utils import get_app_data_dir

class ImageProcessor:
    def __init__(self, thumbnail_size=(256, 256)):
        self.thumbnail_size = thumbnail_size
        self.thumbnails_dir = os.path.join(get_app_data_dir(), ".thumbnails")
        if not os.path.exists(self.thumbnails_dir):
            os.makedirs(self.thumbnails_dir, exist_ok=True)

    def get_image_hash(self, image_input):
        try:
            if isinstance(image_input, str):
                with Image.open(image_input) as img:
                    return str(imagehash.phash(img))
            else:
                # Assuming numpy array (BGR from cv2)
                img = Image.fromarray(cv2.cvtColor(image_input, cv2.COLOR_BGR2RGB))
                return str(imagehash.phash(img))
        except Exception as e:
            print(f"Error calculating hash: {e}")
            return None

    def get_metadata(self, image_path):
        try:
            with Image.open(image_path) as img:
                info = img.getexif()
                date_str = info.get(36867) or info.get(306)
                
                date_obj = None
                if date_str:
                    try:
                        date_obj = datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                    except:
                        pass
                
                if not date_obj:
                    mtime = os.path.getmtime(image_path)
                    date_obj = datetime.fromtimestamp(mtime)

                meta = {
                    "date_taken": date_obj.strftime('%Y:%m:%d %H:%M:%S'),
                    "year": date_obj.year,
                    "month": date_obj.month,
                    "size": os.path.getsize(image_path),
                    "width": img.width,
                    "height": img.height
                }
                
                gps = self._get_gps_from_exif(info)
                if gps:
                    meta["lat"], meta["lon"], meta["alt"] = gps
                return meta
        except Exception as e:
            print(f"Error reading metadata for {image_path}: {e}")
        
        mtime = os.path.getmtime(image_path)
        date_obj = datetime.fromtimestamp(mtime)
        return {
            "date_taken": date_obj.strftime('%Y:%m:%d %H:%M:%S'),
            "year": date_obj.year,
            "month": date_obj.month,
            "size": os.path.getsize(image_path)
        }

    def get_video_metadata(self, video_path):
        mtime = os.path.getmtime(video_path)
        date_obj = datetime.fromtimestamp(mtime)
        meta = {
            "date_taken": date_obj.strftime('%Y:%m:%d %H:%M:%S'),
            "year": date_obj.year,
            "month": date_obj.month,
            "size": os.path.getsize(video_path)
        }
        
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path]
            res = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            data = json.loads(res).get('format', {})
            tags = data.get('tags', {})
            
            ctime = tags.get('creation_time')
            if ctime:
                try:
                    dt = datetime.fromisoformat(ctime.replace('Z', '+00:00'))
                    meta["date_taken"] = dt.strftime('%Y:%m:%d %H:%M:%S')
                    meta["year"] = dt.year
                    meta["month"] = dt.month
                except:
                    pass
            
            loc = tags.get('com.apple.quicktime.location.ISO6709') or tags.get('location')
            if loc:
                match = re.match(r'([+-][0-9.]+)([+-][0-9.]+)([+-][0-9.]+)?/?', loc)
                if match:
                    meta["lat"] = float(match.group(1))
                    meta["lon"] = float(match.group(2))
                    if match.group(3):
                        meta["alt"] = float(match.group(3))
        except:
            pass
        return meta

    def _get_gps_from_exif(self, info):
        if not info: return None
        gps_info = {}
        for tag, value in info.items():
            decoded = TAGS.get(tag, tag)
            if decoded == "GPSInfo":
                for t in value:
                    sub_decoded = GPSTAGS.get(t, t)
                    gps_info[sub_decoded] = value[t]
        
        if 'GPSLatitude' in gps_info and 'GPSLongitude' in gps_info:
            def to_decimal(dms, ref):
                try:
                    d = float(dms[0])
                    m = float(dms[1])
                    s = float(dms[2])
                    res = d + (m / 60.0) + (s / 3600.0)
                    if ref in ['S', 'W']: res = -res
                    return res
                except: return 0.0
            
            try:
                lat = to_decimal(gps_info['GPSLatitude'], gps_info['GPSLatitudeRef'])
                lon = to_decimal(gps_info['GPSLongitude'], gps_info['GPSLongitudeRef'])
                alt = float(gps_info.get('GPSAltitude', 0))
                return lat, lon, alt
            except:
                return None
        return None

    def get_thumbnail_path(self, file_path):
        path_hash = hashlib.sha256(file_path.encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.thumbnails_dir, f"{path_hash}_{os.path.basename(file_path)}.jpg")

    def generate_thumbnail(self, file_path):
        # Use a hash of the full path to avoid collisions for files with same name in different folders
        target_path = self.get_thumbnail_path(file_path)
        if os.path.exists(target_path):
            return target_path
            
        try:
            if file_path.lower().endswith(('.mp4', '.avi', '.mov')):
                cap = cv2.VideoCapture(file_path)
                success, frame = cap.read()
                if success:
                    cv2.imwrite(target_path, cv2.resize(frame, (256, 256)))
                cap.release()
            else:
                with Image.open(file_path) as img:
                    img.thumbnail((256, 256))
                    img.save(target_path, "JPEG")
            return target_path
        except:
            return None

    def extract_video_frames(self, video_path, num_frames=3):
        frames = []
        try:
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                cap.release()
                return []
            
            # Select positions (e.g., 25%, 50%, 75%)
            positions = [int(total_frames * (i + 1) / (num_frames + 1)) for i in range(num_frames)]
            
            for pos in positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                success, frame = cap.read()
                if success:
                    # Convert to RGB if needed, but for hashing and detect_faces BGR is fine
                    frames.append(frame)
            cap.release()
        except Exception as e:
            print(f"Error extracting frames from {video_path}: {e}")
        return frames

    def clear_thumbnails(self):
        if os.path.exists(self.thumbnails_dir):
            for f in os.listdir(self.thumbnails_dir):
                file_path = os.path.join(self.thumbnails_dir, f)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    print(f"Error deleting thumbnail {file_path}: {e}")
