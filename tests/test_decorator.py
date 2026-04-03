import torch
from PIL import Image
from torchvision import transforms
import numpy as np
import os

class MockExtractor:
    def __init__(self, model):
        self.model = model
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.transform = transforms.Compose([
            transforms.Resize(224),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    @torch.no_grad()
    def test_method(self, img_t):
        print(f"Inside @torch.no_grad() method. Grad enabled: {torch.is_grad_enabled()}")
        layer_output = self.model.get_intermediate_layers(img_t, n=1, reshape=True)[0]
        print(f"Layer output requires_grad: {layer_output.requires_grad}")
        
        # This will fail if requires_grad is True
        arr = layer_output.cpu().numpy()
        print("Success in numpy conversion")
        return arr

def test_salient():
    model = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14')
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    
    img_t = torch.randn(1, 3, 224, 224).to("cuda" if torch.cuda.is_available() else "cpu")
    
    extractor = MockExtractor(model)
    try:
        extractor.test_method(img_t)
    except Exception as e:
        print(f"FAILED with decorator: {e}")

if __name__ == "__main__":
    test_salient()
