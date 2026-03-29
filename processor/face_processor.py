import os
import sys

import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis
from sklearn.cluster import DBSCAN

# Import Centralized DLL Fix
from core.utils import fix_dll_search_path
fix_dll_search_path()


class FaceProcessor:
    def __init__(self, model_name='buffalo_l', ctx_id=0):
        # Determine model root directory
        model_root = None
        if getattr(sys, 'frozen', False):
            module_dir = os.path.dirname(os.path.abspath(__file__))
            app_dir = os.path.dirname(module_dir)
            model_root = os.path.join(app_dir, 'insightface')
            print(f"[DEBUG] Frozen mode: using model root: {model_root}")
        
        # Initialize InsightFace with safe provider selection
        import onnxruntime as ort
        available_providers = ort.get_available_providers()
        
        # Default to CPU if GPU initialization is risky
        use_gpu = 'CUDAExecutionProvider' in available_providers and ctx_id >= 0
        providers = ['CUDAExecutionProvider' if use_gpu else 'CPUExecutionProvider', 'CPUExecutionProvider']
        
        kwargs = {'name': model_name, 'providers': providers}
        if model_root:
            kwargs['root'] = model_root
            
        try:
            self.app = FaceAnalysis(**kwargs)
            # Use det_size=(1024, 1024) for high-res portrait detection
            actual_ctx = ctx_id if use_gpu else -1
            self.app.prepare(ctx_id=actual_ctx, det_size=(1024, 1024))
            
            # Set detection threshold directly on the model to avoid signature errors in some versions
            if 'detection' in self.app.models:
                self.app.models['detection'].det_thresh = 0.35
                
        except Exception as e:
            import logging
            logging.getLogger("PhotoArrange").error(f"Failed to initialize FaceAnalysis with GPU: {e}. Retrying with CPU.")
            kwargs['providers'] = ['CPUExecutionProvider']
            self.app = FaceAnalysis(**kwargs)
            self.app.prepare(ctx_id=-1, det_size=(1024, 1024))
            if 'detection' in self.app.models:
                self.app.models['detection'].det_thresh = 0.35


    def preprocess_image(self, image_input):
        """
        Preprocesses an image for face detection. 
        Intended to be called in a ThreadPoolExecutor to offload CPU work (loading, EXIF, resize).
        """
        if not isinstance(image_input, str):
            return image_input

        from PIL import Image, ImageOps
        import logging
        logger = logging.getLogger("PhotoArrange")

        # PILLOW Path (Better for EXIF and color accuracy)
        try:
            with Image.open(image_input) as img_pil:
                try:
                    # Some images (especially Google Takeout) have invalid Orientation tags (e.g. 0 or >8)
                    # that cause ImageOps.exif_transpose to throw "argument out of range".
                    img_pil = ImageOps.exif_transpose(img_pil) 
                except (ValueError, IndexError, Exception) as e:
                    # If EXIF transposition fails, we log a warning and use the raw image
                    if "argument out of range" in str(e).lower():
                        logger.warning(f"Invalid EXIF orientation in {image_input}. Processing without rotation.")
                    else:
                        raise e
                
                img_pil = img_pil.convert('RGB')
                return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            # ULTIMATE FALLBACK: OpenCV Path (Robust but less metadata-aware)
            try:
                img_cv = cv2.imdecode(np.fromfile(image_input, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img_cv is not None:
                    return img_cv
                raise ValueError("cv2.imdecode returned None")
            except Exception as e2:
                logger.error(f"Fatal Preprocess Error for {image_input}: PIL error='{e}', CV fallback error='{e2}'")
                return None

    def detect_faces(self, image_input):
        """Sequential single-image detection (Legacy / Simple use cases)"""
        img = self.preprocess_image(image_input)
        if img is None: return []
        
        # Note: det_thresh is set as a model attribute in __init__
        faces = self.app.get(img)
        return self._postprocess_faces(faces)

    def detect_faces_batch(self, preprocessed_images):
        """
        Processes a list of pre-processed numpy arrays.
        Feeding pre-loaded arrays in a loop significantly increases GPU throughput 
        compared to the disk-bound sequential approach.
        """
        all_results = []
        for img in preprocessed_images:
            if img is None:
                all_results.append([])
                continue
            
            # This call is GPU-bound (Inference)
            faces = self.app.get(img)
            all_results.append(self._postprocess_faces(faces))
        return all_results

    def _postprocess_faces(self, faces):
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


    def cluster_faces(self, face_embeddings, eps=0.42, min_samples=2):
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
