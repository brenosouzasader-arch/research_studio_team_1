"""Train the roof model on labelled chips, split by block so test roofs are
never neighbours of training roofs.

    python -m step2.train --chips data/chips --labels data/labels --val-blocks 7 --test-blocks 8 9

Training is much faster on a GPU (free Google Colab works). The best model by
validation score is saved to models/roof_unet.pt, with the scores per class in
models/roof_unet_metrics.json.
"""
import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image

from .model import CLASSES, IGNORE, build_model, colors_to_classes, predict_probs, save_model, to_input


def load_chips(chips_dir, labels_dir):
    chips = []
    for meta_path in sorted(Path(chips_dir).glob("*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        label_path = Path(labels_dir) / f"{meta['id']}_label.png"
        if not label_path.exists():
            continue
        rgb = np.asarray(Image.open(Path(chips_dir) / f"{meta['id']}_rgb.png").convert("RGB"))
        height = np.load(Path(chips_dir) / f"{meta['id']}_height.npy")
        label = colors_to_classes(np.asarray(Image.open(label_path).convert("RGB")))
        if label.shape != rgb.shape[:2]:
            raise ValueError(f"{label_path.name} is {label.shape}, photo is {rgb.shape[:2]}")
        chips.append({"id": meta["id"], "block": meta.get("block"),
                      "x": to_input(rgb, height), "y": label})
    return chips


def random_crop(x, y, size):
    _, h, w = x.shape
    ph, pw = max(0, size - h), max(0, size - w)
    if ph or pw:
        x = np.pad(x, ((0, 0), (0, ph), (0, pw)))
        y = np.pad(y, ((0, ph), (0, pw)), constant_values=IGNORE)
    _, h, w = x.shape
    r, c = random.randint(0, h - size), random.randint(0, w - size)
    x, y = x[:, r:r + size, c:c + size], y[r:r + size, c:c + size]
    k = random.randint(0, 3)  # roofs have no up or down: any rotation or flip is fair
    x, y = np.rot90(x, k, axes=(1, 2)), np.rot90(y, k)
    if random.random() < 0.5:
        x, y = x[:, :, ::-1], y[:, ::-1]
    return np.ascontiguousarray(x), np.ascontiguousarray(y)


def iou_per_class(model, chips, device):
    inter = np.zeros(len(CLASSES))
    union = np.zeros(len(CLASSES))
    model.eval()
    for chip in chips:
        pred = predict_probs(model, chip["x"], device).argmax(0)
        known = chip["y"] != IGNORE
        for k in range(len(CLASSES)):
            p, t = (pred == k) & known, (chip["y"] == k)
            inter[k] += (p & t).sum()
            union[k] += (p | t).sum()
    return {c: (round(float(inter[k] / union[k]), 3) if union[k] else None)
            for k, c in enumerate(CLASSES)}


def mean_iou(scores):
    vals = [v for c, v in scores.items() if c != "outside" and v is not None]
    return float(np.mean(vals)) if vals else 0.0


def main():
    import torch
    import segmentation_models_pytorch as smp

    ap = argparse.ArgumentParser(description="Train the step 2 roof model")
    ap.add_argument("--chips", default="data/chips")
    ap.add_argument("--labels", default="data/labels")
    ap.add_argument("--val-blocks", type=int, nargs="*", default=[7])
    ap.add_argument("--test-blocks", type=int, nargs="*", default=[8, 9])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--crop", type=int, default=256, help="training crop in pixels (0.1 m each)")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--out", default="models/roof_unet.pt")
    ap.add_argument("--no-pretrained", action="store_true", help="skip ImageNet weights (offline)")
    args = ap.parse_args()

    chips = load_chips(args.chips, args.labels)
    test = [c for c in chips if c["block"] in args.test_blocks]
    val = [c for c in chips if c["block"] in args.val_blocks]
    held_out = set(args.val_blocks) | set(args.test_blocks)
    train = [c for c in chips if c["block"] not in held_out]
    print(f"{len(train)} training, {len(val)} validation, {len(test)} test roofs")
    if not train:
        raise SystemExit("no labelled training roofs found: check --chips and --labels")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_model(None if args.no_pretrained else "imagenet").to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    ce = torch.nn.CrossEntropyLoss(ignore_index=IGNORE)
    dice = smp.losses.DiceLoss("multiclass", ignore_index=IGNORE)
    steps = max(1, len(train) // args.batch)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    best = -1.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for _ in range(steps):
            batch = [random_crop(c["x"], c["y"], args.crop) for c in random.choices(train, k=args.batch)]
            x = torch.from_numpy(np.stack([b[0] for b in batch])).to(device)
            y = torch.from_numpy(np.stack([b[1] for b in batch]).astype(np.int64)).to(device)
            logits = model(x)
            loss = ce(logits, y) + dice(logits, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item()
        scores = iou_per_class(model, val or train, device)
        score = mean_iou(scores)
        print(f"epoch {epoch:3d}  loss {total / steps:.3f}  {'val' if val else 'train'} IoU {scores}")
        if score > best:
            best = score
            save_model(model, args.out)

    from .model import load_model
    model = load_model(args.out, device)
    metrics = {"val": iou_per_class(model, val, device) if val else None,
               "test": iou_per_class(model, test, device) if test else None,
               "train_roofs": len(train), "val_blocks": args.val_blocks, "test_blocks": args.test_blocks}
    Path(args.out).with_name(Path(args.out).stem + "_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
