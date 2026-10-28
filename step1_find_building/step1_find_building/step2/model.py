"""Step 2b: the AI part. A U-Net that labels every 0.1 m pixel of the roof.

Input: 4 channels, the orthophoto (R, G, B) plus LiDAR height above the main
roof level. Output: one of the classes below per pixel. The encoder starts
from ImageNet weights and is fine-tuned on our labelled Barcelona roofs
(see train.py). RID (TUMFTM/RID) and BDAPPV can be added as extra training
data later; their labels map onto these classes.
"""
import numpy as np

CLASSES = ["outside", "free_roof", "obstacle", "solar_panel"]
# label PNGs use exactly these colours (paint with a hard pencil, no anti-aliasing)
COLORS = [(0, 0, 0), (60, 180, 75), (230, 25, 75), (0, 130, 200)]
IGNORE = 255              # pixels with any other colour are left out of training
ENCODER = "resnet34"
HEIGHT_CLIP = (-1.0, 5.0)  # metres above the main roof level
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def build_model(encoder_weights="imagenet"):
    import segmentation_models_pytorch as smp
    return smp.Unet(ENCODER, encoder_weights=encoder_weights, in_channels=4,
                    classes=len(CLASSES))


def to_input(rgb, height):
    """rgb: H x W x 3 uint8; height: H x W metres (NaN allowed) -> 4 x H x W float32."""
    x = (rgb.astype(np.float32) / 255.0 - MEAN) / STD
    h = np.nan_to_num(height, nan=0.0).clip(*HEIGHT_CLIP) / HEIGHT_CLIP[1]
    return np.concatenate([x.transpose(2, 0, 1), h[None].astype(np.float32)])


def colors_to_classes(label_rgb):
    out = np.full(label_rgb.shape[:2], IGNORE, dtype=np.uint8)
    for k, col in enumerate(COLORS):
        out[np.all(label_rgb == col, axis=-1)] = k
    return out


def classes_to_colors(classes):
    out = np.zeros(classes.shape + (3,), dtype=np.uint8)
    for k, col in enumerate(COLORS):
        out[classes == k] = col
    return out


def save_model(model, path):
    import torch
    torch.save({"state_dict": model.state_dict(), "classes": CLASSES, "encoder": ENCODER}, path)


def load_model(path, device="cpu"):
    import torch
    ckpt = torch.load(path, map_location=device)
    if ckpt.get("classes") != CLASSES:
        raise ValueError(f"model was trained for classes {ckpt.get('classes')}, expected {CLASSES}")
    model = build_model(encoder_weights=None)
    model.load_state_dict(ckpt["state_dict"])
    return model.to(device).eval()


def predict_probs(model, x, device="cpu"):
    """x: 4 x H x W float32 -> class probabilities C x H x W (padded to a multiple of 32 inside)."""
    import torch
    _, h, w = x.shape
    ph, pw = (-h) % 32, (-w) % 32
    xp = np.pad(x, ((0, 0), (0, ph), (0, pw)), mode="reflect" if min(h, w) > 32 else "edge")
    with torch.no_grad():
        logits = model(torch.from_numpy(xp)[None].to(device))
        probs = torch.softmax(logits, dim=1)[0, :, :h, :w]
    return probs.cpu().numpy()
