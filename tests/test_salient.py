import os

import torch
from PIL import Image
from torchvision import transforms


def test_salient():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
    model.to(device)
    model.eval()

    transform = transforms.Compose(
        [
            transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )

    # Use a dummy image if sample not found
    sample_img = r"C:\Users\osaka\Documents\Photos\Amazon Photos\Pictures\Pixel 6a\Restored\IMG_20220707_121140_050.webp"
    if not os.path.exists(sample_img):
        print(f"{sample_img} not found, using dummy.")
        img_t = torch.randn(1, 3, 224, 224).to(device)
    else:
        img = Image.open(sample_img).convert("RGB")
        img_t = transform(img).unsqueeze(0).to(device)

    print("Running get_intermediate_layers...")
    try:
        layer_output = model.get_intermediate_layers(img_t, n=1, reshape=True)[0]
        print(f"Layer output shape: {layer_output.shape}")

        saliency_map = torch.norm(layer_output, dim=1)
        print(f"Saliency map shape: {saliency_map.shape}")

        layer_output = torch.nn.functional.normalize(layer_output, dim=1)
        batch_np = layer_output.permute(0, 2, 3, 1).cpu().numpy()
        print(f"Numpy output shape: {batch_np.shape}")

        print("SUCCESS")
    except Exception as e:
        print(f"FAILURE: {e}")


if __name__ == "__main__":
    test_salient()
