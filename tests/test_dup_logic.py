import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from processor.image_processor import ImageProcessor


def test_logic():
    proc = ImageProcessor()

    # Test images and videos if they exist in a known location
    test_folder = "C:/Users/osaka/Pictures"

    files = []
    for root, _, filenames in os.walk(test_folder):
        for f in filenames:
            if f.lower().endswith((".jpg", ".png", ".mp4")):
                files.append(os.path.join(root, f))
                if len(files) > 5:
                    break
        if len(files) > 5:
            break

    print(f"Testing with {len(files)} files...")

    for f in files:
        is_video = f.lower().endswith((".mp4", ".avi", ".mov"))
        if is_video:
            meta = proc.get_video_metadata(f)
            sig = proc.get_video_signature(f)
            print(f"VIDEO: {os.path.basename(f)}")
            print(f"  Hash: {sig}")
            print(f"  EXIF Date: {meta.get('has_exif_date')}")
            print(f"  Location: {meta.get('has_location')}")
        else:
            meta = proc.get_metadata(f)
            hash_comp = proc.get_composite_hash(f)
            print(f"IMAGE: {os.path.basename(f)}")
            print(f"  Hash: {hash_comp}")
            print(f"  EXIF Date: {meta.get('has_exif_date')}")
            print(f"  Location: {meta.get('has_location')}")
        print("-" * 20)


if __name__ == "__main__":
    test_logic()
