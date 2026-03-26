import os
import sys

# Windows DLL Search Path fix for Conda environment
if os.name == 'nt':
    # Current environment's Library/bin
    env_bin = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
    if os.path.exists(env_bin):
        os.add_dll_directory(env_bin)

import cv2
import numpy as np
from insightface.app import FaceAnalysis
from sklearn.cluster import DBSCAN

class FaceProcessor:
    def __init__(self, model_name='buffalo_l', ctx_id=0):
        # Determine model root directory
        model_root = None
        if getattr(sys, 'frozen', False):
            # When running as a frozen executable, models are bundled in the app directory
            # For Nuitka onefile, the models are extracted to a temp dir
            # and __file__ of this module points into that temp dir.
            # We need to go up from processor/face_processor.py to the root
            module_dir = os.path.dirname(os.path.abspath(__file__))
            # module_dir is <temp_dir>/processor/
            app_dir = os.path.dirname(module_dir)
            # app_dir is <temp_dir>/
            model_root = os.path.join(app_dir, 'insightface')
            print(f"[DEBUG] Frozen mode: using model root: {model_root}")
        
        # Initialize InsightFace with GPU support
        # specify root if in frozen mode
        kwargs = {'name': model_name, 'providers': ['CUDAExecutionProvider', 'CPUExecutionProvider']}
        if model_root:
            kwargs['root'] = model_root
            
        self.app = FaceAnalysis(**kwargs)
        self.app.prepare(ctx_id=ctx_id, det_size=(640, 640))

    def detect_faces(self, image_input):
        if isinstance(image_input, str):
            img = cv2.imread(image_input)
        else:
            img = image_input

        if img is None:
            return []
        
        # Detect faces
        faces = self.app.get(img)
        
        results = []
        for face in faces:
            results.append({
                "bbox": face.bbox.tolist(),
                "kps": face.kps.tolist() if face.kps is not None else None,
                "det_score": float(face.det_score),
                "embedding": face.embedding,
                "gender": face.gender,
                "age": face.age
            })
        return results

    def cluster_faces(self, face_embeddings, eps=0.5, min_samples=2):
        if not face_embeddings:
            return []
        
        embeddings = np.array(face_embeddings)
        # Normalize embeddings (InsightFace embeddings are usually normalized, but double check)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        
        # DBSCAN clustering
        # Metric 'cosine' is often better for face embeddings
        # Or Euclidean on normalized embeddings is equivalent to cosine
        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine').fit(embeddings)
        return clustering.labels_
