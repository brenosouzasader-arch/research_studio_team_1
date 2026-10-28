"""Offline tests for step 2's AI part, chips and the full command.

Catastro and the ICGC orthophoto are replaced by stand-ins, and the model is
built without downloading ImageNet weights. Skipped if PyTorch isn't installed.
"""
import json
import sys

import laspy
import numpy as np
import pytest
from PIL import Image
from shapely.geometry import box

torch = pytest.importorskip("torch")
pytest.importorskip("segmentation_models_pytorch")

from step2 import dataset, model, ortho, read_roof, train  # noqa: E402
from step2.lidar_roof import read_roof_lidar  # noqa: E402

X0, Y0 = 430000.0, 4582000.0
FP = box(X0, Y0, X0 + 20, Y0 + 15)


def _points():
    xs, ys = np.meshgrid(np.arange(X0 + 0.1, X0 + 20, 0.25), np.arange(Y0 + 0.1, Y0 + 15, 0.25))
    x, y = xs.ravel(), ys.ravel()
    z = np.full_like(x, 25.0)
    z[(x < X0 + 5) & (x >= X0 + 2) & (y >= Y0 + 2) & (y < Y0 + 5)] = 27.8  # stair housing
    return x, y, z


def _write_las(path, x, y, z, cls=6):
    cls = np.broadcast_to(np.asarray(cls, dtype=np.uint8), x.shape)
    header = laspy.LasHeader(point_format=6, version="1.4")  # format 6 allows class 77
    header.offsets = [x.min(), y.min(), z.min()]
    header.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    las.classification = cls
    las.write(path)


class _Record:
    refcat, catastro_address, query, year_built, dwellings = "0123456DF3802C", "CL TEST 1", "test", 1900, 10


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """A LiDAR tile on disk; Catastro and the orthophoto replaced by stand-ins.

    The stair housing is class 77 ("other building"), as ICGC labels rooftop structures.
    """
    x, y, z = _points()
    _write_las(tmp_path / "tile.laz", x, y, z, np.where(z > 26, 77, 6))
    for mod in (dataset, read_roof):
        monkeypatch.setattr(mod, "find_building", lambda *a, **k: (_Record(), FP))
    grey = lambda grid: np.full((grid.height, grid.width, 3), 120, dtype=np.uint8)  # noqa: E731
    monkeypatch.setattr(ortho, "get_ortho", grey)
    return tmp_path


def test_input_and_label_colours():
    x = model.to_input(np.zeros((40, 50, 3), np.uint8), np.full((40, 50), np.nan))
    assert x.shape == (4, 40, 50) and x.dtype == np.float32 and np.all(x[3] == 0)
    classes = np.array([[0, 1], [2, 3]], dtype=np.uint8)
    assert np.array_equal(model.colors_to_classes(model.classes_to_colors(classes)), classes)
    odd = np.full((1, 1, 3), 7, dtype=np.uint8)
    assert model.colors_to_classes(odd)[0, 0] == model.IGNORE


def test_predict_probs_any_size():
    net = model.build_model(encoder_weights=None).eval()
    probs = model.predict_probs(net, np.random.rand(4, 70, 90).astype(np.float32))
    assert probs.shape == (len(model.CLASSES), 70, 90)
    assert np.allclose(probs.sum(0), 1, atol=1e-4)


def test_combine_adds_panels_lidar_cannot_see():
    lroof = read_roof_lidar(FP, np.column_stack(_points()))
    fine = lroof.grid.finer(5)
    probs = np.zeros((len(model.CLASSES), fine.height, fine.width), np.float32)
    probs[model.CLASSES.index("free_roof")] = 1.0
    r, c = fine.rowcol(X0 + 10, Y0 + 10)                  # a 2 x 4 m panel row the LiDAR missed
    probs[:, r:r + 20, c:c + 40] = 0
    probs[model.CLASSES.index("solar_panel"), r:r + 20, c:c + 40] = 1.0
    summary, usable, classes, grid = read_roof.combine(lroof, probs)
    assert abs(summary["existing_panels_m2"] - 8.0) < 0.5
    assert abs(summary["seen_only_by_ai_m2"] - 8.0) < 0.5
    assert summary["obstacle_area_m2"] >= 9 + 8 - 0.5
    assert summary["usable_area_m2"] < lroof.summary["usable_area_m2"]
    assert summary["model_certainty"] == 1.0 and summary["confidence"] == "high"


def test_make_chip_and_train_and_read_roof(offline, monkeypatch):
    chips, labels, models = offline / "chips", offline / "labels", offline / "models"
    labels.mkdir()
    for block in (1, 2, 7):
        meta = dataset.make_chip(chips, offline, address="Test", block=block)
        cid = f"{meta['id']}_b{block}"   # same building three times: rename per block
        for suffix in ("_rgb.png", "_rgb.pgw", "_height.npy", "_prelabel.png", ".json"):
            (chips / f"{meta['id']}{suffix}").rename(chips / f"{cid}{suffix}")
        meta["id"] = cid
        (chips / f"{cid}.json").write_text(json.dumps(meta))
        prelabel = np.asarray(Image.open(chips / f"{cid}_prelabel.png"))
        Image.fromarray(prelabel).save(labels / f"{cid}_label.png")   # pre-label used as label
    assert meta["lidar"]["obstacles"][0]["area_m2"] == 9.0
    assert (chips / f"{cid}_rgb.pgw").exists()

    out = models / "roof_unet.pt"
    monkeypatch.setattr(sys, "argv", ["train", "--chips", str(chips), "--labels", str(labels),
                                      "--val-blocks", "7", "--test-blocks", "--epochs", "1",
                                      "--batch", "2", "--crop", "64", "--no-pretrained",
                                      "--out", str(out)])
    train.main()
    assert out.exists() and json.loads(out.with_name("roof_unet_metrics.json").read_text())["val"]

    base = str(offline / "roof")
    res = read_roof.read_roof(offline, address="Test", out=base)
    assert res["roof"]["method"] == "lidar only" and res["roof"]["usable_area_m2"] > 150
    assert json.loads(open(base + "_usable.geojson").read())["features"]
    # a 1-epoch model is still random, so its usable area may be empty: only check it runs
    res = read_roof.read_roof(offline, address="Test", model_path=out, out=base)
    assert res["roof"]["method"] == "lidar + ai"
    assert res["roof"]["obstacle_area_m2"] >= 9
    assert Image.open(base + "_preview.png").size == (Image.open(chips / f"{cid}_rgb.png").size)
    for name in ("_photo.png", "_height.png"):
        assert Image.open(base + name).size == Image.open(base + "_preview.png").size
