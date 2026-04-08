import logging
import os
import threading
from typing import Any, Optional

import torch

# Ensure DLL paths are fixed for ONNX/OpenCV
from core.utils import fix_dll_search_path

fix_dll_search_path()

logger = logging.getLogger("PhotoArrange")


class ModelManager:
    """
    Singleton manager for AI models (DINOv2, InsightFace).
    Prevents duplicate loading and centralizes VRAM management.
    """

    _instance: Optional["ModelManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ModelManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ModelManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._models: dict[str, Any] = {}
        self._initialized = True
        logger.info(f"ModelManager initialized on {self.device}")

    def get_dinov2(self, model_type: str = "dinov2_vits14") -> Any:
        """Loads and returns the DINOv2 model."""
        key = f"dinov2_{model_type}"
        with self._lock:
            if key not in self._models:
                logger.info(f"Loading {key} on {self.device}...")
                model = torch.hub.load("facebookresearch/dinov2", model_type)
                model.to(self.device)
                model.eval()
                self._models[key] = model
            return self._models[key]

    def get_insightface(self, model_name: str = "buffalo_l", det_thresh: float = 0.35) -> Any:
        """Loads and returns the InsightFace FaceAnalysis object."""
        key = f"insightface_{model_name}"
        with self._lock:
            if key not in self._models:
                import onnxruntime as ort
                from insightface.app import FaceAnalysis

                # Determine model root (local path prioritized)
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                model_root = os.path.join(project_root, "insightface")

                available_providers = ort.get_available_providers()
                use_gpu = (
                    "CUDAExecutionProvider" in available_providers and self.device.type == "cuda"
                )
                providers = (
                    ["CUDAExecutionProvider", "CPUExecutionProvider"]
                    if use_gpu
                    else ["CPUExecutionProvider"]
                )

                logger.info(f"Loading {key} with providers {providers}...")

                kwargs = {"name": model_name, "providers": providers}
                if os.path.exists(model_root):
                    kwargs["root"] = model_root

                try:
                    app = FaceAnalysis(**kwargs)
                    ctx_id = 0 if use_gpu else -1
                    app.prepare(ctx_id=ctx_id, det_size=(640, 640))

                    if "detection" in app.models:
                        app.models["detection"].det_thresh = det_thresh

                    self._models[key] = app
                except Exception as e:
                    logger.error(f"Failed to load InsightFace: {e}")
                    # Fallback to CPU
                    kwargs["providers"] = ["CPUExecutionProvider"]
                    app = FaceAnalysis(**kwargs)
                    app.prepare(ctx_id=-1, det_size=(640, 640))
                    if "detection" in app.models:
                        app.models["detection"].det_thresh = det_thresh
                    self._models[key] = app

            return self._models[key]

    def empty_cache(self) -> None:
        """Triggers manual VRAM cleanup if using CUDA."""
        if self.device.type == "cuda":
            torch.cuda.empty_cache()
            logger.debug("GPU cache emptied via ModelManager.")

    def unload_models(self) -> None:
        """Clears all loaded models from memory/VRAM."""
        with self._lock:
            self._models.clear()
            self.empty_cache()
            logger.info("All models unloaded from ModelManager.")
