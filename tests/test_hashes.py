import os
import sys

# Add project dir to path
sys.path.append(os.getcwd())

from processor.image_processor import ImageProcessor


def test_hashes(folder_path):
    proc = ImageProcessor()
    files = [f for f in os.listdir(folder_path) if f.lower().endswith((".jpg", ".png"))][:10]

    for f in files:
        full_path = os.path.join(folder_path, f)
        comp_hash = proc.get_composite_hash(full_path)
        print(f"File: {f:30} | Hash: {comp_hash}")


if __name__ == "__main__":
    # Use a dummy path or a real one if I can find one
    # Let's try to find an image in the user's workspace
    workspace = r"c:\Users\osaka\Documents\antigravity\PhotoArrange"
    # Actually, I'll just check if there are any images in the root or a subfolder
    test_hashes(workspace)
