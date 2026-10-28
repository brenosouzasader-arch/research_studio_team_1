"""Hand a step 2 roof reading to the Roofscapes RePowered map.

    python -m step2.export_web lluria56 --web ..\\..\\roofscapes-repowered

Reads <name>.json and <name>_usable.geojson (written by step2.read_roof) and
writes roofs/<refcat>.json in the map folder, in longitude/latitude, plus
roofs/index.json (the list of read roofs). When the map finds a reading for a
clicked building, it places panels only on the usable area instead of
assuming 25% of the roof is lost to obstacles.
"""
import argparse
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape

TO_LNGLAT = Transformer.from_crs("EPSG:25831", "EPSG:4326", always_xy=True)


def _ring(coords):
    return [[round(lng, 7), round(lat, 7)] for lng, lat in
            (TO_LNGLAT.transform(x, y) for x, y in coords)]


def _polygons(geom):
    """Shapely (Multi)Polygon in EPSG:25831 -> [[outer, hole, ...], ...] in lng/lat."""
    parts = getattr(geom, "geoms", [geom])
    return [[_ring(p.exterior.coords)] + [_ring(h.coords) for h in p.interiors] for p in parts]


def web_reading(result, usable_fc):
    """Step 2 result + usable GeoJSON (EPSG:25831) -> the map's roof reading."""
    usable = []
    for f in usable_fc.get("features", []):
        usable += _polygons(shape(f["geometry"]))
    obstacles = []
    for o in result["lidar"].get("obstacles", []):
        lng, lat = TO_LNGLAT.transform(o["x"], o["y"])
        obstacles.append({"lng": round(lng, 7), "lat": round(lat, 7),
                          "area_m2": o["area_m2"], "height_m": o["height_m"]})
    lngs = [p[0] for poly in usable for p in poly[0]]
    lats = [p[1] for poly in usable for p in poly[0]]
    b = result["building"]
    return {
        "source": "Pipeline step 2: read the roof",
        "refcat": b["refcat"][:14],
        "address": b.get("address"),
        "roof": result["roof"],
        "roof_area_m2": result["lidar"].get("roof_area_m2"),
        "lidar_coverage": result["lidar"].get("lidar_coverage"),
        "usable": usable,
        "bbox": [min(lngs), min(lats), max(lngs), max(lats)] if usable else None,
        "obstacles": obstacles,
    }


def export(result, usable_fc, web_dir):
    """Write roofs/<refcat>.json and refresh roofs/index.json. Returns the file written."""
    reading = web_reading(result, usable_fc)
    roofs = Path(web_dir) / "roofs"
    roofs.mkdir(parents=True, exist_ok=True)
    path = roofs / f"{reading['refcat']}.json"
    path.write_text(json.dumps(reading, indent=1), encoding="utf-8")

    index_path = roofs / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"roofs": []}
    index["roofs"] = [r for r in index["roofs"] if r["refcat"] != reading["refcat"]]
    index["roofs"].append({"refcat": reading["refcat"], "address": reading["address"], "bbox": reading["bbox"],
                           "usable_area_m2": reading["roof"]["usable_area_m2"],
                           "confidence": reading["roof"]["confidence"]})
    index["roofs"].sort(key=lambda r: r["refcat"])
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return path


def export_files(name, web_dir):
    result = json.loads(Path(f"{name}.json").read_text(encoding="utf-8"))
    usable_fc = json.loads(Path(f"{name}_usable.geojson").read_text(encoding="utf-8"))
    return export(result, usable_fc, web_dir)


def main():
    ap = argparse.ArgumentParser(description="Send step 2 roof readings to the Roofscapes RePowered map")
    ap.add_argument("names", nargs="+", help="step 2 output names (the --out you gave read_roof), e.g. lluria56")
    ap.add_argument("--web", required=True, help="the roofscapes-repowered folder")
    args = ap.parse_args()
    for name in args.names:
        print("wrote", export_files(name, args.web))


if __name__ == "__main__":
    main()
