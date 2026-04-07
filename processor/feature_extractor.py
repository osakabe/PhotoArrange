import logging
import os
from typing import Any, Optional, Union

import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from core.utils import Profiler

from .model_manager import ModelManager

logger = logging.getLogger("PhotoArrange")


class FeatureExtractor:
    """
    Handles AI feature extraction using DINOv2.
    Managed by ModelManager to prevent duplicate model loads.
    """

    def __init__(self, model_type: str = "dinov2_vits14") -> None:
        self.manager = ModelManager()
        self.device = self.manager.device
        self.model = self.manager.get_dinov2(model_type)

        # Standard DINOv2 transforms
        self.transform = transforms.Compose(
            [
                transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )
        self.vector_dim = 384  # For ViT-S/14

    @torch.no_grad()
    def extract_salient_features(self, image_path: str) -> Optional[np.ndarray]:
        """
        Extracts features for high-attention (foreground) patches (Stage 2).
        """
        if not image_path or not os.path.exists(image_path):
            return None

        try:
            with Profiler(
                f"FeatureExtractor.extract_salient_features ({os.path.basename(image_path)})"
            ):
                img = Image.open(image_path).convert("RGB")
                img_t = self.transform(img).unsqueeze(0).to(self.device)

                # Extract patch features from last layer
                # reshape=True -> [1, 384, 16, 16]
                patch_features = self.model.get_intermediate_layers(img_t, n=1, reshape=True)[0]

            # Derive saliency from patch norms
            saliency = torch.norm(patch_features, dim=1).squeeze(0)  # [16, 16]

            # Prepare flattened patches
            patch_feats_flat = patch_features.squeeze(0).permute(1, 2, 0).reshape(256, 384)
            patch_feats_flat = torch.nn.functional.normalize(patch_feats_flat, dim=1)

            # Select Top-K salient patches
            top_k = 64
            _, indices = torch.topk(saliency.flatten(), top_k)

            salient_feats = patch_feats_flat[indices]  # [64, 384]
            return salient_feats.cpu().numpy()

        except Exception as e:
            logger.error(f"Salient Feature Error for {image_path}: {e}")
            return None

    @torch.no_grad()
    def compute_local_similarity_batch(
        self,
        feats1_list: Union[list[np.ndarray], torch.Tensor],
        feats2_list: Union[list[np.ndarray], torch.Tensor],
    ) -> list[float]:
        """
        Computes symmetric Chamfer-like similarity between pairs of salient patches.
        """
        if not feats1_list or not feats2_list or len(feats1_list) != len(feats2_list):
            return []

        try:
            # 1. Prepare Tensors
            if isinstance(feats1_list, list):
                t1 = torch.stack([torch.from_numpy(f) for f in feats1_list]).to(self.device)
                t2 = torch.stack([torch.from_numpy(f) for f in feats2_list]).to(self.device)
            else:
                t1, t2 = feats1_list.to(self.device), feats2_list.to(self.device)

            # 2. Batch Cross Similarity [N, 64, 64]
            sim_mat = torch.bmm(t1, t2.transpose(1, 2))

            # 3. Chamfer Similarity (max match in both directions)
            max_sim1, _ = torch.max(sim_mat, dim=2)
            max_sim2, _ = torch.max(sim_mat, dim=1)

            scores = (torch.mean(max_sim1, dim=1) + torch.mean(max_sim2, dim=1)) / 2.0
            return scores.cpu().tolist()

        except Exception as e:
            logger.error(f"Batch Similarity Matching Error: {e}")
            return [0.0] * len(feats1_list)
        finally:
            self.manager.empty_cache()

    @torch.no_grad()
    def extract_features(self, image_paths: list[str]) -> list[Optional[np.ndarray]]:
        """Sequential feature extraction for a list of paths (Legacy)."""
        return [self._extract_single(p) for p in image_paths]

    def _extract_single(self, path: str) -> Optional[np.ndarray]:
        if not os.path.exists(path):
            return None
        try:
            img = Image.open(path).convert("RGB")
            img_t = self.transform(img).unsqueeze(0).to(self.device)
            feat = self.model(img_t)
            feat = torch.nn.functional.normalize(feat, dim=1)
            return feat.flatten().cpu().numpy()
        except Exception as e:
            logger.error(f"Feature extraction error for {path}: {e}")
            return None

    @torch.no_grad()
    def extract_features_from_video(self, frames: list[np.ndarray]) -> Optional[np.ndarray]:
        """Averages features from multiple video frames for Stage 1 global matching."""
        if not frames:
            return None

        import cv2

        all_vecs = []
        for img in frames:
            if img is None:
                continue
            if isinstance(img, np.ndarray):
                img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

            img_t = self.transform(img).unsqueeze(0).to(self.device)
            feat = self.model(img_t)
            all_vecs.append(torch.nn.functional.normalize(feat, dim=1))

        if not all_vecs:
            return None

        avg_vec = torch.mean(torch.stack(all_vecs), dim=0)
        avg_vec = torch.nn.functional.normalize(avg_vec, dim=1)
        return avg_vec.flatten().cpu().numpy()

    def prepare_tensor(self, image_path: str, img_proc: Any = None) -> Optional[torch.Tensor]:
        """Loads and transforms an image into a Tensor on the CPU."""
        if not image_path or not os.path.exists(image_path):
            return None

        try:
            load_path = self._resolve_video_thumb(image_path, img_proc)
            if not load_path:
                return None

            img = Image.open(load_path).convert("RGB")
            return self.transform(img)
        except Exception as e:
            logger.error(f"Tensor prep error for {image_path}: {e}")
            return None

    def _resolve_video_thumb(self, path: str, img_proc: Any) -> Optional[str]:
        if not path.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            return path

        if img_proc is None:
            if not hasattr(self, "_img_proc"):
                from processor.image_processor import ImageProcessor

                self._img_proc = ImageProcessor()
            img_proc = self._img_proc

        thumb = img_proc.get_thumbnail_path(path)
        if not os.path.exists(thumb):
            thumb = img_proc.generate_thumbnail(path)
        return thumb if thumb and os.path.exists(thumb) else None

    @torch.no_grad()
    def extract_features_from_tensors(
        self, tensors: list[torch.Tensor]
    ) -> list[Optional[np.ndarray]]:
        """Runs GPU batch inference on a list of pre-processed Tensors."""
        if not tensors:
            return []

        try:
            with Profiler(f"FeatureExtractor.extract_features_from_tensors (size={len(tensors)})"):
                input_batch = torch.stack(tensors).to(self.device)
                features = self.model(input_batch)
                features = torch.nn.functional.normalize(features, dim=1)
                return list(features.cpu().numpy())
        except Exception as e:
            logger.error(f"GPU batch inference error: {e}")
            return [None] * len(tensors)
        finally:
            self.manager.empty_cache()

    @torch.no_grad()
    def extract_salient_features_batch(
        self,
        image_paths: list[str],
        batch_size: int = 256,
        progress_callback: Optional[callable] = None,
    ) -> dict[str, Optional[np.ndarray]]:
        """
        High-throughput salient feature extraction using parallel prep and GPU batching.
        """
        if not image_paths:
            return {}

        results: dict[str, Optional[np.ndarray]] = {}
        from concurrent.futures import ThreadPoolExecutor

        for i in range(0, len(image_paths), batch_size):
            if hasattr(self, "is_cancelled") and self.is_cancelled:
                break

            batch = image_paths[i : i + batch_size]
            if progress_callback:
                progress_callback(
                    f"Extracting Salient: {i}/{len(image_paths)}",
                    12 + int((i / len(image_paths)) * 10),
                )

            # Parallel Loading
            with ThreadPoolExecutor(max_workers=16) as executor:
                tensors = list(executor.map(self.prepare_tensor, batch))

            # Filter and Pin memory
            valid_tensors, valid_paths = [], []
            for p, t in zip(batch, tensors):
                if t is not None:
                    valid_tensors.append(t.pin_memory())
                    valid_paths.append(p)

            if not valid_tensors:
                continue

            try:
                with Profiler(
                    f"FeatureExtractor.salient_batch_inference (size={len(valid_tensors)})"
                ):
                    img_batch = torch.stack(valid_tensors).to(self.device)
                    layer_output = self.model.get_intermediate_layers(img_batch, n=1, reshape=True)[
                        0
                    ]

                # Saliency and Normalization
                saliency_map = torch.norm(layer_output, dim=1)
                layer_output = torch.nn.functional.normalize(layer_output, dim=1)

                # Selection (Top-64)
                batch_np = layer_output.permute(0, 2, 3, 1).cpu().numpy()
                sal_np = saliency_map.cpu().numpy()

                for b_idx, path in enumerate(valid_paths):
                    flat_sal = sal_np[b_idx].flatten()
                    flat_feat = batch_np[b_idx].reshape(256, 384)
                    top_indices = flat_sal.argsort()[-64:][::-1]
                    results[path] = flat_feat[top_indices]

            except Exception as e:
                logger.error(f"Salient Batch Error: {e}")
                for p in valid_paths:
                    results[p] = None
            finally:
                self.manager.empty_cache()

        return results
