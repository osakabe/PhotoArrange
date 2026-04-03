import os
import sys

import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis
from sklearn.cluster import DBSCAN

import logging
import torch

# Import Centralized DLL Fix
from core.utils import fix_dll_search_path
fix_dll_search_path()

logger = logging.getLogger("PhotoArrange")


class FaceProcessor:
    def __init__(self, model_name='buffalo_l', ctx_id=0, det_thresh=0.35):
        # Determine model root directory (Prioritize local project-based models)
        module_dir = os.path.dirname(os.path.abspath(__file__))
        app_dir = os.path.dirname(module_dir)
        local_insightface = os.path.abspath(os.path.join(app_dir, 'insightface'))
        
        model_root = None
        if os.path.exists(local_insightface):
            model_root = local_insightface
            logger.info(f"Using local model root: {model_root}")
        elif getattr(sys, 'frozen', False):
            model_root = os.path.join(app_dir, 'insightface')
            logger.info(f"Frozen mode: using model root: {model_root}")
        
        # Initialize InsightFace with safe provider selection
        import onnxruntime as ort
        available_providers = ort.get_available_providers()
        logger.info(f"Available ONNX providers: {available_providers}")
        
        # Default to CPU if GPU initialization is risky
        use_gpu = 'CUDAExecutionProvider' in available_providers and ctx_id >= 0
        if use_gpu:
            try:
                if not torch.cuda.is_available():
                    logger.warning("CUDAExecutionProvider available but torch.cuda.is_available() is False. Falling back to CPU for safety.")
                    use_gpu = False
            except Exception as te:
                logger.error(f"Error checking torch.cuda state: {te}")
                use_gpu = False

        providers = ['CUDAExecutionProvider' if use_gpu else 'CPUExecutionProvider', 'CPUExecutionProvider']
        logger.info(f"Selected priority providers: {providers}")
        
        kwargs = {'name': model_name, 'providers': providers}
        if model_root:
            kwargs['root'] = model_root
            
        try:
            self.app = FaceAnalysis(**kwargs)
            # Use det_size=(640, 640) as default for better speed/precision balance, 
            # but allow override or high-res detection if needed.
            actual_ctx = ctx_id if use_gpu else -1
            self.app.prepare(ctx_id=actual_ctx, det_size=(640, 640))
            
            # Log successful initialization and which provider was actually picked
            actual_providers = self.app.models['detection'].session.get_providers() if 'detection' in self.app.models else "Unknown"
            logger.info(f"FaceAnalysis initialized successfully. Actual providers: {actual_providers}")

            # Set detection threshold directly on the model to avoid signature errors in some versions
            if 'detection' in self.app.models:
                self.app.models['detection'].det_thresh = det_thresh
                
        except Exception as e:
            logger.exception("Failed to initialize FaceAnalysis with primary providers. Retrying with CPU fallback.")
            try:
                kwargs['providers'] = ['CPUExecutionProvider']
                self.app = FaceAnalysis(**kwargs)
                self.app.prepare(ctx_id=-1, det_size=(640, 640))
                if 'detection' in self.app.models:
                    self.app.models['detection'].det_thresh = det_thresh
                logger.info("FaceAnalysis fallback to CPU successful.")
            except Exception as e2:
                logger.exception(f"Fatal error initializing FaceAnalysis even on CPU: {e2}")
                raise e2


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
        if img is None: 
            return []
        
        try:
            faces = self.app.get(img)
            if not faces:
                logger.debug(f"No faces detected in {image_input}")
            return self._postprocess_faces(faces)
        except Exception as e:
            logger.error(f"Inference error during detect_faces for {image_input}: {e}")
            return []
        finally:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def detect_faces_batch(self, preprocessed_images):
        """
        Processes a list of pre-processed numpy arrays.
        Feeding pre-loaded arrays in a loop significantly increases GPU throughput 
        compared to the disk-bound sequential approach.
        """
        all_results = []
        for i, img in enumerate(preprocessed_images):
            if img is None:
                all_results.append([])
                continue
            
            try:
                # This call is GPU-bound (Inference)
                faces = self.app.get(img)
                if not faces:
                    logger.debug(f"Batch index {i}: No faces detected.")
                all_results.append(self._postprocess_faces(faces))
            except Exception as e:
                logger.error(f"Inference error in detect_faces_batch at index {i}: {e}")
                all_results.append([])
        
        # Release VRAM after batch processing
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
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
