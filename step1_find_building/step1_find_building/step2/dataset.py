"""Make training chips for the AI model, one per building.

For each address: run step 1, read the roof from LiDAR, download the ICGC
orthophoto, and save into the output folder:

    <id>_rgb.png        orthophoto, 0.1 m per pixel (+ .pgw world file for QGIS)
    <id>_height.npy     LiDAR height above the main roof level, same pixels
    <id>_prelabel.png   LiDAR's guess, in label colours: a head start for labelling
    <id>.json           address, plot reference, block, bounds, LiDAR summary

To label a roof: copy <id>_prelabel.png to labels/<id>_label.png, open it over
<id>_rgb.png in any paint program (GIMP, Photoshop, Krita) and fix it with a
hard pencil in the exact colours of model.COLORS: free roof green, obstacle
red, existing solar panel blue, outside black. White = not sure (ignored).

Run:
    python -m step2.dataset roofs.csv --laz-dir data/lidar --out data/chips
roofs.csv has two columns, address and block (the block number 1 to 9 from
our focus-area diagram), used later to split training and test roofs.
"""
import argparse
import csv
import json
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

from step1.find_building import find_building

from . import ortho
from .grid import upsample
from .lidar_roof import read_roof_from_tiles
from .model import CLASSES, COLORS

FACTOR = 5          # 0.5 m LiDAR cell = 5 x 5 photo pixels at 0.1 m
UNSURE = (255, 255, 255)


def height_above_roof(lroof):
    """LiDAR heights relative to the largest roof surface (metres)."""
    if lroof.summary["surfaces"]:
        base = lroof.summary["surfaces"][0]["height_m"]
    else:
        base = np.nanmedian(lroof.dsm) if np.isfinite(lroof.dsm).any() else 0.0
    return lroof.dsm - base


def lidar_prelabel(lroof):
    """Class colours from LiDAR at 0.5 m; roof cells without points are left white."""
    out = np.zeros(lroof.roof.shape + (3,), dtype=np.uint8)  # outside = black
    out[lroof.roof] = UNSURE
    out[lroof.surface_id >= 0] = COLORS[CLASSES.index("free_roof")]
    out[lroof.obstacle] = COLORS[CLASSES.index("obstacle")]
    return out


def make_chip(out_dir, laz_dir, address=None, lat=None, lon=None, block=None):
    record, fp = find_building(address, lat, lon)
    lroof = read_roof_from_tiles(fp, laz_dir)
    if lroof is None:
        raise RuntimeError("no LiDAR tile in the folder covers this building")
    fine = lroof.grid.finer(FACTOR)
    rgb = ortho.get_ortho(fine)
    chip_id = record.refcat

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ortho.save_png(rgb, out_dir / f"{chip_id}_rgb.png", fine)
    np.save(out_dir / f"{chip_id}_height.npy",
            upsample(height_above_roof(lroof), FACTOR).astype(np.float32))
    Image.fromarray(upsample(lidar_prelabel(lroof), FACTOR)).save(out_dir / f"{chip_id}_prelabel.png")
    meta = {"id": chip_id, "address": record.catastro_address or record.query,
            "refcat": record.refcat, "block": block, "bounds": fine.bounds,
            "res": fine.res, "lidar": lroof.summary}
    (out_dir / f"{chip_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main():
    ap = argparse.ArgumentParser(description="Make training chips for step 2")
    ap.add_argument("csv", help="CSV with columns: address, block")
    ap.add_argument("--laz-dir", required=True)
    ap.add_argument("--out", default="data/chips")
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    done = 0
    for row in rows:
        print(f"=== {row['address']}")
        try:
            meta = make_chip(args.out, args.laz_dir, address=row["address"],
                             block=int(row["block"]) if row.get("block") else None)
            print(f"    saved {meta['id']}: usable {meta['lidar']['usable_area_m2']} m2")
            done += 1
        except Exception:
            traceback.print_exc()
    print(f"\n{done} of {len(rows)} chips made in {args.out}")


if __name__ == "__main__":
    main()
