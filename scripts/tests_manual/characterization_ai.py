import os
import sys

# CRITICAL: Fix DLL paths before any other imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from core.utils import fix_dll_search_path

fix_dll_search_path()

# Import cv2 explicitly first to avoid conflicts if torch is loaded
import json

from processor.face_processor import FaceProcessor
from processor.feature_extractor import FeatureExtractor


def test_characterization():
    image_path = "dummy_qa.jpg"
    if not os.path.exists(image_path):
        print(f"Skipping: {image_path} not found")
        return

    print("--- Capturing Characterization Data ---")

    # 1. FeatureExtractor
    print("Testing FeatureExtractor...")
    fe = FeatureExtractor()

    # Global features
    global_feats = fe.extract_features([image_path])
    if global_feats and global_feats[0] is not None:
        print(f"Global features shape: {global_feats[0].shape}")
        global_sample = global_feats[0][:10].tolist()
    else:
        global_sample = None

    # Salient features
    salient_feats = fe.extract_salient_features(image_path)
    if salient_feats is not None:
        print(f"Salient features shape: {salient_feats.shape}")
        salient_sample = salient_feats[0, :10].tolist()
    else:
        salient_sample = None

    # 2. FaceProcessor
    print("Testing FaceProcessor...")
    fp = FaceProcessor()
    faces = fp.detect_faces(image_path)
    print(f"Detected {len(faces)} faces.")

    face_data = []
    for i, face in enumerate(faces):
        face_data.append(
            {
                "bbox": face["bbox"],
                "det_score": face["det_score"],
                "embedding_sample": face["embedding"][:10].tolist(),
            }
        )

    # Save to file
    data = {"global_sample": global_sample, "salient_sample": salient_sample, "faces": face_data}

    with open("tests/ai_snapshot.json", "w") as f:
        json.dump(data, f, indent=2)

    print("Snapshot saved to tests/ai_snapshot.json")


if __name__ == "__main__":
    test_characterization()
