"""Offline tests for handing step 2 readings to the Roofscapes RePowered map."""
import json

from shapely.geometry import box, mapping

from step2.export_web import export

X0, Y0 = 430550.0, 4582740.0  # Roger de Llúria 56, EPSG:25831


def _result(refcat="0629625DF3802H", usable=120.0):
    return {"building": {"refcat": refcat, "address": "CL TEST 1"},
            "roof": {"method": "lidar only", "usable_area_m2": usable, "free_area_m2": 150.0,
                     "obstacle_area_m2": 30.0, "existing_panels_m2": None, "confidence": "medium"},
            "lidar": {"roof_area_m2": 200.0, "lidar_coverage": 0.97,
                      "obstacles": [{"area_m2": 9.0, "height_m": 2.8, "x": X0 + 5, "y": Y0 + 5}]}}


def _usable():
    area = box(X0, Y0, X0 + 12, Y0 + 10).difference(box(X0 + 4, Y0 + 4, X0 + 6, Y0 + 6))
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(area), "properties": {}}]}


def test_export_writes_reading_in_lnglat_with_holes(tmp_path):
    path = export(_result(), _usable(), tmp_path)
    r = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "0629625DF3802H.json"
    assert len(r["usable"]) == 1 and len(r["usable"][0]) == 2      # one area with one hole
    lng, lat = r["usable"][0][0][0]
    assert 2.16 < lng < 2.18 and 41.38 < lat < 41.40
    w, s, e, n = r["bbox"]
    assert w < e and s < n
    assert r["roof"]["usable_area_m2"] == 120.0 and len(r["obstacles"]) == 1


def test_index_keeps_one_entry_per_roof(tmp_path):
    export(_result(usable=100.0), _usable(), tmp_path)
    export(_result(usable=120.0), _usable(), tmp_path)
    export(_result(refcat="0629624DF3802H0001AB"), _usable(), tmp_path)
    index = json.loads((tmp_path / "roofs" / "index.json").read_text(encoding="utf-8"))
    assert [r["refcat"] for r in index["roofs"]] == ["0629624DF3802H", "0629625DF3802H"]
    assert index["roofs"][1]["usable_area_m2"] == 120.0


def test_empty_usable_area_still_exports(tmp_path):
    r = json.loads(export(_result(usable=0.0), {"features": []}, tmp_path).read_text(encoding="utf-8"))
    assert r["usable"] == [] and r["bbox"] is None
