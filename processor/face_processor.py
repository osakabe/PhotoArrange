import logging
from typing import Any, Optional, Union

import cv2
import numpy as np
from PIL import Image, ImageOps
from sklearn.cluster import DBSCAN

from core.utils import Profiler

from .model_manager import ModelManager

logger = logging.getLogger("PhotoArrange")


class FaceProcessor:
    """
    Handles face detection and clustering using InsightFace.
    Managed by ModelManager to prevent duplicate model loads.
    """

    def __init__(self, model_name: str = "buffalo_l", det_thresh: float = 0.35) -> None:
        self.manager = ModelManager()
        self.device = self.manager.device
        self.app = self.manager.get_insightface(model_name, det_thresh)

    def preprocess_image(self, image_input: Union[str, np.ndarray]) -> Optional[np.ndarray]:
        """
        Preprocesses an image for face detection (loading, orientation fix).
        """
        if isinstance(image_input, np.ndarray):
            return image_input

        try:
            with Image.open(image_input) as img_pil:
                img_pil = self._safe_exif_transpose(img_pil, image_input)
                img_pil = img_pil.convert("RGB")
                return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            return self._cv2_fallback_load(image_input, e)

    def _safe_exif_transpose(self, img_pil: Any, path: str) -> Any:
        try:
            return ImageOps.exif_transpose(img_pil)
        except Exception as e:
            logger.warning(f"EXIF transposition failed for {path}: {e}")
            return img_pil

    def _cv2_fallback_load(self, path: str, pil_error: Exception) -> Optional[np.ndarray]:
        try:
            img_cv = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img_cv is not None:
                return img_cv
            raise ValueError("cv2.imdecode returned None")
        except Exception as e2:
            logger.error(f"Preprocess Error for {path}: PIL='{pil_error}', CV='{e2}'")
            return None

    def detect_faces(self, image_input: Union[str, np.ndarray]) -> list[dict[str, Any]]:
        """Sequential single-image detection."""
        img = self.preprocess_image(image_input)
        if img is None:
            return []

        try:
            with Profiler("FaceProcessor.detect_faces"):
                faces = self.app.get(img)
                return self._postprocess_faces(faces)
        except Exception as e:
            logger.error(f"Inference error during detect_faces: {e}")
            return []
        finally:
            self.manager.empty_cache()

    def detect_faces_batch(
        self, preprocessed_images: list[np.ndarray]
    ) -> list[list[dict[str, Any]]]:
        """Processes a list of pre-processed numpy arrays."""
        results = []
        with Profiler(f"FaceProcessor.detect_faces_batch (size={len(preprocessed_images)})"):
            for i, img in enumerate(preprocessed_images):
                if img is None:
                    results.append([])
                    continue
                try:
                    faces = self.app.get(img)
                    results.append(self._postprocess_faces(faces))
                except Exception as e:
                    logger.error(f"Inference error in batch at index {i}: {e}")
                    results.append([])

        self.manager.empty_cache()
        return results

    def _postprocess_faces(self, faces: list[Any]) -> list[dict[str, Any]]:
        results = []
        for face in faces:
            results.append(
                {
                    "bbox": face.bbox.tolist(),
                    "kps": face.kps.tolist() if face.kps is not None else None,
                    "det_score": float(face.det_score),
                    "embedding": face.embedding,
                    "gender": face.gender,
                    "age": face.age,
                }
            )
        # Apply intra-frame NMS (spatial deduplication)
        return self._deduplicate_in_frame(results)

    def _deduplicate_in_frame(
        self, faces: list[dict[str, Any]], iou_threshold: float = 0.45
    ) -> list[dict[str, Any]]:
        """
        Suppresses overlapping face detections within the same frame using IoU.
        Keep the one with higher det_score.
        """
        if not faces:
            return []

        # Sort by confidence score descending
        sorted_faces = sorted(faces, key=lambda x: x["det_score"], reverse=True)
        keep = []

        for i in range(len(sorted_faces)):
            is_dup = False
            for j in range(len(keep)):
                iou = self.compute_iou(sorted_faces[i]["bbox"], keep[j]["bbox"])
                if iou > iou_threshold:
                    is_dup = True
                    break
            if not is_dup:
                keep.append(sorted_faces[i])

        return keep

    @staticmethod
    def compute_iou(box1: list[float], box2: list[float]) -> float:
        """Calculates Intersection over Union (IoU) between two bounding boxes."""
        x1, y1, x2, y2 = box1
        x3, y3, x4, y4 = box2

        inter_x1 = max(x1, x3)
        inter_y1 = max(y1, y3)
        inter_x2 = min(x2, x4)
        inter_y2 = min(y2, y4)

        inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
        area1 = (x2 - x1) * (y2 - y1)
        area2 = (x4 - x3) * (y4 - y3)

        union_area = area1 + area2 - inter_area
        return inter_area / union_area if union_area > 0 else 0

    def cluster_faces(
        self, face_embeddings: list[np.ndarray], eps: float = 0.42, min_samples: int = 2
    ) -> np.ndarray:
        """Clusters face embeddings using DBSCAN with cosine metric."""
        if not face_embeddings:
            return np.array([])

        embeddings = np.array(face_embeddings)
        # Normalize for consistency (though cosine metric is used)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / (norms + 1e-6)

        with Profiler(f"FaceProcessor.cluster_faces (count={len(face_embeddings)})"):
            clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(embeddings)
            return clustering.labels_
