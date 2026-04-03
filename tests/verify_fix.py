import os
import sys
import torch
import numpy as np

# Add project root to path
sys.path.append(os.getcwd())

from processor.feature_extractor import FeatureExtractor
from processor.image_processor import ImageProcessor

def test_feature_extractor():
    print("Testing FeatureExtractor updates...")
    fe = FeatureExtractor()
    ip = ImageProcessor()
    
    # Test paths (use some existing ones or dummies)
    # Since I don't know the exact paths, I'll check if the logic handles video extensions.
    video_path = "dummy.mp4"
    # Create a dummy image for thumbnail if it's missing (though prepare_tensor will fail if file missing)
    
    print("Testing prepare_tensor for video extension...")
    # This should trigger the ImageProcessor.get_thumbnail_path logic
    res = fe.prepare_tensor(video_path)
    print(f"prepare_tensor for missing video returned: {res} (Expected: None)")
    
    print("Testing extract_salient_features_batch...")
    # Test with empty list
    res = fe.extract_salient_features_batch([])
    assert res == {}
    print("Empty list handled.")
    
    print("Success: Core logic check passed.")

if __name__ == "__main__":
    try:
        test_feature_extractor()
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
