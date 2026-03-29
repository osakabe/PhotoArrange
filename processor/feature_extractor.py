import os
import torch
from PIL import Image
from torchvision import transforms
import numpy as np
import logging

logger = logging.getLogger("PhotoArrange")

class FeatureExtractor:
    def __init__(self, model_type="dinov2_vits14", device=None):
        """
        Initializes the DINOv2 model for feature extraction.
        """
        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = device
            
        logger.info(f"Initializing FeatureExtractor with {model_type} on {self.device}")
        
        try:
            # DINOv2 ViT-S/14 is a great balance of speed and precision (384-dim vectors)
            self.model = torch.hub.load('facebookresearch/dinov2', model_type)
            self.model.to(self.device)
            self.model.eval()
            
            # Standard DINOv2 transforms
            self.transform = transforms.Compose([
                transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            self.vector_dim = 384 # For ViT-S/14
            self.last_attention = None
        except Exception as e:
            logger.error(f"Failed to load DINOv2 model: {e}")
            raise

    def _attention_hook(self, module, input, output):
        """
        Hook to capture the attention matrix from the last block.
        DINOv2 uses a specific attention implementation where we can extract weights.
        """
        # For ViT attention, the last block's attention weights are what we need.
        # This implementation depends on the specific structure of DINOv2 Attention.
        self.last_attention = output

    @torch.no_grad()
    def extract_salient_features(self, image_path):
        """
        Extracts features for the high-attention (foreground) patches.
        This is the "Stage 2" precise inspection.
        """
        try:
            if not image_path or not os.path.exists(image_path):
                return None
            
            img = Image.open(image_path).convert('RGB')
            img_t = self.transform(img).unsqueeze(0).to(self.device)
            
            # 1. Get Attention Map (CLS to Patches)
            # We use a context manager to temp hook the attention
            # In DINOv2 Hub models, we can often use get_last_selfattention if we find it
            # But let's use get_intermediate_layers for features and a custom saliency mask
            
            # DINOv2 patch features (last layer)
            # reshape=True means (1, 384, 16, 16)
            patch_features = self.model.get_intermediate_layers(img_t, n=1, reshape=True)[0]
            
            # 2. Derive Saliency from patch feature Norms (often correlates strongly with attention)
            # Or use a simpler approach: get the attention map
            # Since get_last_selfattention failed, we'll use feature norms as a proxy for salient regions
            saliency = torch.norm(patch_features, dim=1).squeeze(0) # (16, 16)
            
            # Flatten to 256 patches
            saliency_flat = saliency.flatten()
            patch_feats_flat = patch_features.squeeze(0).permute(1, 2, 0).reshape(256, 384)
            
            # Normalize patches for comparison
            patch_feats_flat = torch.nn.functional.normalize(patch_feats_flat, dim=1)
            
            # Select Top-K salient patches (e.g., 64 patches)
            top_k = 64
            _, indices = torch.topk(saliency_flat, top_k)
            
            salient_feats = patch_feats_flat[indices] # (64, 384)
            return salient_feats.detach().cpu().numpy()
            
        except Exception as e:
            logger.error(f"Local Salient Feature Error: {e}")
            return None

    @torch.no_grad()
    def compute_local_similarity(self, feats1, feats2):
        """
        Computes similarity between two sets of salient patches.
        Legacy single-pair method, now calls the batched version for consistency.
        """
        if feats1 is None or feats2 is None:
            return 0.0
        
        # Wrap in lists to use the batched version
        scores = self.compute_local_similarity_batch([feats1], [feats2])
        return scores[0]

    @torch.no_grad()
    def compute_local_similarity_batch(self, feats1_list, feats2_list):
        """
        Computes similarities for multiple pairs of salient patches in a single GPU batch.
        Uses torch.bmm (Batch Matrix Multiplication) for maximum speed.
        feats1_list, feats2_list: Lists of numpy arrays or a stacked tensor [N, 64, 384].
        Returns a list of float scores.
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
            # t1: [N, 64, 384], t2.transpose(1, 2): [N, 384, 64]
            sim_mat = torch.bmm(t1, t2.transpose(1, 2))
            
            # 3. Chamfer-like Max Similarity
            # For each patch in t1, find best match in t2: [N, 64]
            max_sim1, _ = torch.max(sim_mat, dim=2)
            # For each patch in t2, find best match in t1: [N, 64]
            max_sim2, _ = torch.max(sim_mat, dim=1)
            
            # 4. Symmetric Mean
            scores = (torch.mean(max_sim1, dim=1) + torch.mean(max_sim2, dim=1)) / 2.0
            
            return scores.cpu().tolist()
        except Exception as e:
            logger.error(f"Batch Similarity Matching Error: {e}")
            return [0.0] * len(feats1_list)
        finally:
            if self.device.type == 'cuda':
                torch.cuda.empty_cache()

    @torch.no_grad()
    def extract_features(self, image_paths):
        """
        Extracts features for a list of image paths.
        Returns a list of numpy arrays (embeddings).
        """
        if not image_paths:
            return []
            
        embeddings = []
        for path in image_paths:
            try:
                if not os.path.exists(path):
                    embeddings.append(None)
                    continue
                    
                img = Image.open(path).convert('RGB')
                img_t = self.transform(img).unsqueeze(0).to(self.device)
                
                # Forward pass
                feat = self.model(img_t)
                # Normalize for cosine similarity (L2 norm)
                feat = torch.nn.functional.normalize(feat, dim=1)
                
                embeddings.append(feat.detach().cpu().numpy().flatten())
            except Exception as e:
                logger.error(f"Error extracting features for {path}: {e}")
                embeddings.append(None)
                
        return embeddings

    @torch.no_grad()
    def extract_features_from_video(self, frames):
        """
        Extracts features for a list of frames (images) and averages them.
        Used for video global similarity in Stage 1.
        """
        if not frames:
            return None
            
        all_vecs = []
        import cv2
        for img in frames:
            if img is None: continue
            
            # Convert OpenCV (BGR numpy array) to PIL (RGB) if necessary
            if isinstance(img, np.ndarray):
                img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                
            img_t = self.transform(img).unsqueeze(0).to(self.device)
            feat = self.model(img_t)
            feat = torch.nn.functional.normalize(feat, dim=1)
            all_vecs.append(feat)
            
        if not all_vecs:
            return None
            
        # Average the normalized vectors and re-normalize the result
        avg_vec = torch.mean(torch.stack(all_vecs), dim=0)
        avg_vec = torch.nn.functional.normalize(avg_vec, dim=1)
        return avg_vec.detach().cpu().numpy().flatten()

    @torch.no_grad()
    def prepare_tensor(self, image_path, img_proc=None):
        """
        Loads and transforms an image or video frame into a DINOv2 Tensor on the CPU.
        For videos, it uses the pre-generated middle-frame thumbnail as a representative frame.
        """
        try:
            if not image_path or not os.path.exists(image_path):
                return None
            
            load_path = image_path
            # Support videos by using their cached thumbnail (representative frame)
            if image_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                # Use provided or cached ImageProcessor to avoid repeated instantiation overhead
                if img_proc is None:
                    if not hasattr(self, '_img_proc'):
                        from processor.image_processor import ImageProcessor
                        self._img_proc = ImageProcessor()
                    img_proc = self._img_proc
                
                load_path = img_proc.get_thumbnail_path(image_path)
                # Ensure thumbnail exists (it should have been generated during discovery/Stage 1)
                if not os.path.exists(load_path):
                    # Fallback to direct frame extraction if thumbnail is missing for some reason
                    load_path = img_proc.generate_thumbnail(image_path)
                    if not load_path: return None

            img = Image.open(load_path).convert('RGB')
            return self.transform(img)
        except Exception as e:
            logger.error(f"Error preparing tensor for {image_path}: {e}")
            return None

    @torch.no_grad()
    def extract_features_from_tensors(self, tensors, device=None):
        """
        Runs GPU inference on a batch of pre-calculated Tensors.
        This is a GPU-bound operation.
        """
        if not tensors:
            return []
            
        target_device = device or self.device
        try:
            input_batch = torch.stack(tensors).to(target_device)
            features = self.model(input_batch)
            features = torch.nn.functional.normalize(features, dim=1)
            return features.detach().cpu().numpy()
        except Exception as e:
            logger.error(f"Inference error from tensors: {e}")
            return [None] * len(tensors)
        finally:
            if target_device.type == 'cuda':
                torch.cuda.empty_cache()

    @torch.no_grad()
    def extract_salient_features_batch(self, image_paths, batch_size=256, progress_callback=None):
        """
        Extracts salient features for a list of paths in GPU batches.
        Uses parallel preprocessing and batched inference for maximum throughput.
        (Increased batch_size=256 for high-end GPU utilization)
        """
        if not image_paths:
            return {}
            
        results = {}
        from concurrent.futures import ThreadPoolExecutor
        from functools import partial
        
        # Pre-bind prep_func for efficiency
        prep_func = partial(self.prepare_tensor)
        
        # 1. Processing in smaller chunks to manage system RAM and GPU VRAM
        for i in range(0, len(image_paths), batch_size):
            if hasattr(self, "is_cancelled") and self.is_cancelled:
                break
                
            batch_paths = image_paths[i : i + batch_size]
            
            if progress_callback:
                prog_percent = i / len(image_paths)
                progress_callback(f"Extracting Salient: {i}/{len(image_paths)}", 12 + int(prog_percent * 10))
            
            # 2. Parallel Loading & Prep (Increased max_workers=16 for CPU saturation)
            with ThreadPoolExecutor(max_workers=16) as executor:
                tensors = list(executor.map(prep_func, batch_paths))
            
            valid_tensors = []
            valid_paths = []
            for path, t in zip(batch_paths, tensors):
                if t is not None:
                    valid_tensors.append(t.pin_memory())
                    valid_paths.append(path)
            
            if not valid_tensors:
                continue

            try:
                img_batch = torch.stack(valid_tensors).to(self.device)
                
                # Inference: DINOv2 ViT-S/14
                # layer_output: [Batch, 384, 16, 16] 
                layer_output = self.model.get_intermediate_layers(img_batch, n=1, reshape=True)[0]
                
                # 3. Saliency Calculation (Local Norm)
                saliency_map = torch.norm(layer_output, dim=1) # [Batch, 16, 16]
                
                # 4. Feature Normalization (Critical Fix)
                # Normalize patch vectors along hidden dimension (dim=1)
                layer_output = torch.nn.functional.normalize(layer_output, dim=1)
                
                # 5. Extraction & Top-K Selection (Moved to CPU)
                batch_np = layer_output.permute(0, 2, 3, 1).detach().cpu().numpy() # [Batch, 16, 16, 384]
                sal_np = saliency_map.detach().cpu().numpy() # [Batch, 16, 16]
                
                for b_idx, path in enumerate(valid_paths):
                    flat_sal = sal_np[b_idx].flatten()
                    flat_feat = batch_np[b_idx].reshape(256, 384)
                    
                    # Top-K Salient Patches (e.g. 64)
                    top_indices = flat_sal.argsort()[-64:][::-1]
                    results[path] = flat_feat[top_indices] # [64, 384]
                    
            except Exception as e:
                logger.error(f"Salient Inference Error for {len(valid_paths)} images: {e}")
                for path in valid_paths:
                    results[path] = None
            finally:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        return results

    @torch.no_grad()
    def extract_features_batch(self, image_paths, batch_size=64):
        """
        Legacy compatibility method that combines preparation and inference.
        """
        all_embeddings = [None] * len(image_paths)
        
        for i in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[i:i+batch_size]
            batch_tensors = []
            valid_indices = []
            
            for j, path in enumerate(batch_paths):
                tensor = self.prepare_tensor(path)
                if tensor is not None:
                    batch_tensors.append(tensor)
                    valid_indices.append(i + j)
            
            if not batch_tensors:
                continue
                
            features_np = self.extract_features_from_tensors(batch_tensors)
            for idx, feat in zip(valid_indices, features_np):
                if feat is not None:
                    all_embeddings[idx] = feat
                    
        return all_embeddings
