"""Pipeline step 2: read the roof. Address in, usable m2 + obstacle map out.

    python -m step2.read_roof "Carrer de Roger de Llúria 56, Barcelona" --laz-dir data/lidar
    python -m step2.read_roof "..." --laz-dir data/lidar --model models/roof_unet.pt

Without --model it uses LiDAR rules only (the baseline). With a trained model
it also reads the orthophoto, and an area counts as an obstacle if either the
LiDAR or the model says so (the safe choice for panel layout).

Writes <out>.json (numbers), <out>_usable.geojson (usable area, EPSG:25831),
<out>_preview.png (photo with the reading on top), <out>_photo.png (the plain
roof photo) and <out>_height.png (LiDAR height above the main roof level).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import mapping

from step1.find_building import find_building

from . import ortho
from .dataset import FACTOR, height_above_roof, lidar_prelabel
from .grid import upsample
from .lidar_roof import read_roof_from_tiles, usable_mask
from .model import CLASSES, COLORS, classes_to_colors, load_model, predict_probs, to_input

HIGH_CONFIDENCE = 0.85    # mean model certainty on the roof
MEDIUM_CONFIDENCE = 0.70
MIN_LIDAR_COVERAGE = 0.9  # share of roof cells with LiDAR points


def combine(lroof, probs):
    """Merge the LiDAR reading (0.5 m) with the model's class probabilities (0.1 m)."""
    fine = lroof.grid.finer(FACTOR)
    area = fine.cell_area
    roof = upsample(lroof.roof, FACTOR)
    lidar_obstacle = upsample(lroof.obstacle, FACTOR)
    lidar_surface = upsample(lroof.surface_id >= 0, FACTOR)
    ai = probs.argmax(0)
    ai_obstacle = roof & (ai == CLASSES.index("obstacle"))
    ai_panel = roof & (ai == CLASSES.index("solar_panel"))

    obstacle = roof & (lidar_obstacle | ai_obstacle | ai_panel)
    free = roof & lidar_surface & ~obstacle
    usable = usable_mask(roof, free, obstacle, fine.res)

    certainty = float(probs.max(0)[roof].mean()) if roof.any() else 0.0
    ai_any = ai_obstacle | ai_panel
    union = (ai_any | lidar_obstacle).sum()
    agreement = float((ai_any & lidar_obstacle).sum() / union) if union else 1.0
    coverage = lroof.summary["lidar_coverage"]
    if certainty >= HIGH_CONFIDENCE and coverage >= MIN_LIDAR_COVERAGE:
        level = "high"
    elif certainty >= MEDIUM_CONFIDENCE:
        level = "medium"
    else:
        level = "low"

    classes = np.zeros(roof.shape, dtype=np.uint8)
    classes[free] = CLASSES.index("free_roof")
    classes[obstacle] = CLASSES.index("obstacle")
    classes[ai_panel] = CLASSES.index("solar_panel")
    summary = {
        "method": "lidar + ai",
        "usable_area_m2": round(float(usable.sum() * area), 1),
        "free_area_m2": round(float(free.sum() * area), 1),
        "obstacle_area_m2": round(float(obstacle.sum() * area), 1),
        "existing_panels_m2": round(float(ai_panel.sum() * area), 1),
        "seen_only_by_ai_m2": round(float((ai_any & ~lidar_obstacle).sum() * area), 1),
        "model_certainty": round(certainty, 3),
        "lidar_ai_agreement": round(agreement, 3),
        "confidence": level,
    }
    return summary, usable, classes, fine


def lidar_only(lroof):
    s = lroof.summary
    coverage_ok = s["lidar_coverage"] >= MIN_LIDAR_COVERAGE
    summary = {
        "method": "lidar only",
        "usable_area_m2": s["usable_area_m2"],
        "free_area_m2": s["free_area_m2"],
        "obstacle_area_m2": s["obstacle_area_m2"],
        "existing_panels_m2": None,  # LiDAR can't tell panels from roof
        "confidence": "medium" if coverage_ok else "low",
    }
    return summary, lroof.usable, None, lroof.grid


USABLE_COLOR = (255, 230, 0)   # yellow outline: where panels fit
# height colours from 1 m below to 5 m above the main roof: blue, green, yellow, red
HEIGHT_STOPS = np.array([-1.0, 0.0, 1.0, 2.5, 5.0])
HEIGHT_COLORS = np.array([(40, 70, 160), (60, 180, 75), (180, 210, 60), (250, 200, 40), (215, 40, 40)])


def _edge(mask):
    return mask & ~ndimage.binary_erosion(mask)


def preview(rgb, classes, roof, usable, path, alpha=0.45):
    """Photo + class colours on the roof, roof outline in white, usable area outlined in yellow."""
    overlay = classes_to_colors(classes).astype(np.float32)
    base = rgb.astype(np.float32) if rgb is not None else np.full(overlay.shape, 128.0)
    out = base.copy()
    out[roof] = (1 - alpha) * base[roof] + alpha * overlay[roof]
    out[_edge(roof)] = 255
    out[_edge(usable)] = USABLE_COLOR
    Image.fromarray(out.clip(0, 255).astype(np.uint8)).save(path)


def height_image(height, roof, path):
    """LiDAR height above the main roof level as colours; outside the roof is grey."""
    out = np.full(height.shape + (3,), 200, dtype=np.float32)
    ok = roof & np.isfinite(height)
    h = np.clip(height[ok], HEIGHT_STOPS[0], HEIGHT_STOPS[-1])
    for ch in range(3):
        out[..., ch][ok] = np.interp(h, HEIGHT_STOPS, HEIGHT_COLORS[:, ch])
    out[_edge(roof)] = 0
    Image.fromarray(out.astype(np.uint8)).save(path)


def read_roof(laz_dir, address=None, lat=None, lon=None, model_path=None, out="roof"):
    record, fp = find_building(address, lat, lon)
    lroof = read_roof_from_tiles(fp, laz_dir)
    if lroof is None:
        raise SystemExit("No LiDAR tile in --laz-dir covers this building. Download the tile first.")
    fine = lroof.grid.finer(FACTOR)

    rgb, photo_error = None, None
    try:
        rgb = ortho.get_ortho(fine)
    except Exception as e:  # the LiDAR reading still works without the photo
        photo_error = str(e)[:200]

    if model_path:
        if rgb is None:
            raise SystemExit(f"The model needs the orthophoto, which failed: {photo_error}")
        model = load_model(model_path)
        x = to_input(rgb, upsample(height_above_roof(lroof), FACTOR))
        summary, usable, classes, grid = combine(lroof, predict_probs(model, x))
    else:
        summary, usable, classes, grid = lidar_only(lroof)
        classes = None

    if classes is None:  # LiDAR classes, drawn at photo resolution
        label = upsample(lidar_prelabel(lroof), FACTOR)
        classes = np.zeros(label.shape[:2], dtype=np.uint8)
        for k, col in enumerate(COLORS):
            classes[np.all(label == col, axis=-1)] = k

    result = {
        "building": {"refcat": record.refcat, "address": record.catastro_address,
                     "query": record.query, "year_built": record.year_built,
                     "dwellings": record.dwellings},
        "roof": summary,
        "lidar": lroof.summary,
        "photo_error": photo_error,
    }
    Path(f"{out}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    poly = grid.cells_to_polygon(usable)
    fc = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25831"}},
          "features": [] if poly is None else [{"type": "Feature", "geometry": mapping(poly),
                                               "properties": {"usable_area_m2": summary["usable_area_m2"]}}]}
    Path(f"{out}_usable.geojson").write_text(json.dumps(fc), encoding="utf-8")
    roof_fine = upsample(lroof.roof, FACTOR)
    usable_fine = usable if usable.shape == roof_fine.shape else upsample(usable, FACTOR)
    preview(rgb, classes, roof_fine, usable_fine, f"{out}_preview.png")
    if rgb is not None:
        Image.fromarray(rgb).save(f"{out}_photo.png")
    height_image(upsample(height_above_roof(lroof), FACTOR), roof_fine, f"{out}_height.png")
    return result


def main():
    ap = argparse.ArgumentParser(description="Pipeline step 2: read the roof")
    ap.add_argument("address", nargs="?")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--laz-dir", required=True, help="folder with ICGC/CNIG LAZ tiles")
    ap.add_argument("--model", help="trained model (models/roof_unet.pt); leave out for LiDAR only")
    ap.add_argument("--out", default="roof", help="output name, without extension")
    args = ap.parse_args()
    if not args.address and (args.lat is None or args.lon is None):
        ap.error("give an address, or both --lat and --lon")
    result = read_roof(args.laz_dir, args.address, args.lat, args.lon, args.model, args.out)
    print(json.dumps({k: result[k] for k in ("building", "roof", "photo_error")}, indent=2, default=str))


if __name__ == "__main__":
    main()
