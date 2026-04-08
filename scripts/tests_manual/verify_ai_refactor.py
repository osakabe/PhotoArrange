import json
import os
import sys

import numpy as np

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import fix_dll_search_path

fix_dll_search_path()

from processor.face_processor import FaceProcessor
from processor.feature_extractor import FeatureExtractor


def verify_refactor():
    snapshot_path = "tests/ai_snapshot.json"
    if not os.path.exists(snapshot_path):
        print(f"Skipping: {snapshot_path} not found. Run characterization_ai.py first.")
        return

    with open(snapshot_path, "r") as f:
        snapshot = json.load(f)

    image_path = "dummy_qa.jpg"
    print("--- Verifying AI Refactor ---")

    # 1. FeatureExtractor
    print("Verifying FeatureExtractor...")
    fe = FeatureExtractor()

    global_feats = fe.extract_features([image_path])
    if global_feats and global_feats[0] is not None:
        current_global = global_feats[0][:10].tolist()
        # Compare with snapshot
        if snapshot["global_sample"]:
            diff = np.abs(np.array(current_global) - np.array(snapshot["global_sample"])).max()
            print(f"Global feature max diff: {diff}")
            assert diff < 1e-5, "Global feature mismatch!"

    salient_feats = fe.extract_salient_features(image_path)
    if salient_feats is not None:
        current_salient = salient_feats[0, :10].tolist()
        if snapshot["salient_sample"]:
            diff = np.abs(np.array(current_salient) - np.array(snapshot["salient_sample"])).max()
            print(f"Salient feature max diff: {diff}")
            assert diff < 1e-5, "Salient feature mismatch!"

    # 2. FaceProcessor
    print("Verifying FaceProcessor...")
    fp = FaceProcessor()
    faces = fp.detect_faces(image_path)
    print(f"Detected {len(faces)} faces (Snapshot: {len(snapshot['faces'])})")
    assert len(faces) == len(snapshot["faces"]), "Face count mismatch!"

    for i, face in enumerate(faces):
        snap_face = snapshot["faces"][i]
        # Compare det_score
        assert abs(face["det_score"] - snap_face["det_score"]) < 1e-5, f"Face {i} score mismatch!"
        # Compare embedding
        diff = np.abs(face["embedding"][:10] - np.array(snap_face["embedding_sample"])).max()
        print(f"Face {i} embedding max diff: {diff}")
        assert diff < 1e-5, f"Face {i} embedding mismatch!"

    print("\n✅ AI Refactor Verification SUCCESSFUL")


if __name__ == "__main__":
    try:
        verify_refactor()
    except Exception as e:
        print(f"\n❌ Verification FAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
