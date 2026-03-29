import os
import cv2
import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import hashlib
from datetime import datetime
import subprocess
import json
import re
import time
try:
    import torch
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from core.utils import get_app_data_dir, fix_dll_search_path
fix_dll_search_path() # Required for cv2 videoio FFmpeg DLLs on Windows


class ImageProcessor:
    def __init__(self, thumbnail_size=(256, 256)):
        self.thumbnail_size = thumbnail_size
        self.thumbnails_dir = os.path.join(get_app_data_dir(), ".thumbnails")
        if not os.path.exists(self.thumbnails_dir):
            os.makedirs(self.thumbnails_dir, exist_ok=True)
        
        self.device = None
        if HAS_TORCH and torch.cuda.is_available():
            self.device = torch.device('cuda')


    def get_file_hash(self, file_path):
        """
        Calculates a full-file MD5 checksum for exact bit-for-bit duplicate detection.
        """
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            print(f"Error calculating MD5 for {file_path}: {e}")
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
                    "has_exif_date": True if date_str else False,
                    "year": date_obj.year,
                    "month": date_obj.month,
                    "size": os.path.getsize(image_path),
                    "width": img.width,
                    "height": img.height,
                    "camera_model": info.get(272) # Model tag
                }
                
                gps = self._get_gps_from_exif(info)
                if gps:
                    meta["lat"], meta["lon"], meta["alt"] = gps
                    meta["has_location"] = True
                else:
                    meta["has_location"] = False
            return meta
        except Exception as e:
            print(f"Error reading metadata for {image_path}: {e}")
            # Mark as corrupted if even basic Pillow opening fails
            corrupted_meta = {
                "corrupted": True,
                "size": os.path.exists(image_path) and os.path.getsize(image_path) or 0
            }
            return corrupted_meta

    def get_video_metadata(self, video_path):
        mtime = os.path.getmtime(video_path)
        date_obj = datetime.fromtimestamp(mtime)
        meta = {
            "date_taken": date_obj.strftime('%Y:%m:%d %H:%M:%S'),
            "year": date_obj.year,
            "month": date_obj.month,
            "size": os.path.getsize(video_path),
            "camera_model": None
        }
        
        # Extract dimensions using cv2
        try:
            cap = cv2.VideoCapture(video_path)
            meta["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            meta["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
        except:
            pass

        try:
            # Silence stderr to avoid cluttered logs and parsing errors
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path]
            try:
                res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=15)
                data = json.loads(res).get('format', {})
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                import logging
                logging.getLogger("PhotoArrange").warning(f"ffprobe failed or timed out for {video_path}: {e}")
                data = {}
            
            tags = data.get('tags', {})
            
            # If ffprobe returns no format data, it's likely corrupted
            if not data:
                meta["corrupted"] = True

            
            ctime = tags.get('creation_time')
            if ctime:
                try:
                    dt = datetime.fromisoformat(ctime.replace('Z', '+00:00'))
                    meta["date_taken"] = dt.strftime('%Y:%m:%d %H:%M:%S')
                    meta["has_exif_date"] = True
                    meta["year"] = dt.year
                    meta["month"] = dt.month
                except:
                    pass
            else:
                meta["has_exif_date"] = False
            
            # Extract additional tags for video matching
            meta["camera_model"] = tags.get('model') or tags.get('com.apple.quicktime.model')
            
            loc = tags.get('com.apple.quicktime.location.ISO6709') or tags.get('location')
            if loc:
                match = re.match(r'([+-][0-9.]+)([+-][0-9.]+)([+-][0-9.]+)?/?', loc)
                if match:
                    meta["lat"] = float(match.group(1))
                    meta["lon"] = float(match.group(2))
                    meta["has_location"] = True
                    if match.group(3):
                        meta["alt"] = float(match.group(3))
                else:
                    meta["has_location"] = False
            else:
                meta["has_location"] = False
        except:
            pass
        return meta

    def _get_gps_from_exif(self, exif):
        if not exif: return None
        try:
            # Safer way to get GPS IFD in modern Pillow (0x8825 is the tag for GPSInfo)
            gps_ifd = exif.get_ifd(0x8825) 
            if not gps_ifd: return None
            
            from PIL.ExifTags import GPSTAGS
            gps_info = {}
            for t, v in gps_ifd.items():
                tag = GPSTAGS.get(t, t)
                gps_info[tag] = v
                
            if 'GPSLatitude' in gps_info and 'GPSLongitude' in gps_info:
                def to_decimal(dms, ref):
                    try:
                        # Some DMS are (degrees, minutes, seconds)
                        d = float(dms[0])
                        m = float(dms[1])
                        s = float(dms[2])
                        res = d + (m / 60.0) + (s / 3600.0)
                        if ref in ['S', 'W']: res = -res
                        return res
                    except: return 0.0
                
                lat = to_decimal(gps_info['GPSLatitude'], gps_info.get('GPSLatitudeRef', 'N'))
                lon = to_decimal(gps_info['GPSLongitude'], gps_info.get('GPSLongitudeRef', 'E'))
                alt = float(gps_info.get('GPSAltitude', 0))
                return lat, lon, alt
        except:
            pass
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
                if not cap.isOpened():
                    return None
                
                # Seek to 50% to get a more representative thumbnail (first frame is often black)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if total_frames > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames // 2)
                    
                success, frame = cap.read()
                # Fallback to first frame if seeking failed
                if not success:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    success, frame = cap.read()

                if success:
                    # Unicode-safe imwrite for Windows (video thumbnails)
                    _, buffer = cv2.imencode('.jpg', cv2.resize(frame, (256, 256)))
                    buffer.tofile(target_path)
                cap.release()
            else:
                with Image.open(file_path) as img:
                    img.thumbnail((256, 256))
                    img.save(target_path, "JPEG")
            return target_path
        except:
            return None

    def extract_video_frames(self, video_path, num_frames=5):
        """
        Extracts representative frames from a video for AI embedding or face detection.
        Defaults to 5 frames for robust duplicate detection (v2.0).
        """
        frames_with_indices = []
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return []
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                cap.release()
                return []
            
            # Select positions spaced throughout the video (e.g. 10%, 30%, 50%, 70%, 90%)
            positions = [int(total_frames * (i + 1) / (num_frames + 1)) for i in range(num_frames)]
            
            for pos in positions:
                try:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                    success, frame = cap.read()
                    if success:
                        # Return both the frame and its index
                        # For AI embedding, we might prefer RGB, but we return raw BGR for consistency with OpenCV
                        frames_with_indices.append((frame, pos))
                except Exception as inner_e:
                    import logging
                    logging.getLogger("PhotoArrange").error(f"Error reading frame at {pos} in {video_path}: {inner_e}")
                    continue
            cap.release()
        except Exception as e:
            import logging
            logging.getLogger("PhotoArrange").error(f"Error extracting frames from {video_path}: {e}")
        return frames_with_indices


    def clear_thumbnails(self):
        if os.path.exists(self.thumbnails_dir):
            for f in os.listdir(self.thumbnails_dir):
                file_path = os.path.join(self.thumbnails_dir, f)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    print(f"Error deleting thumbnail {file_path}: {e}")
