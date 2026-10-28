"""Offline tests for step 2's LiDAR reading (no internet, no AI libraries)."""
import numpy as np
from shapely.geometry import box

from step2.grid import Grid, upsample
from step2.lidar_roof import read_roof_lidar, usable_mask

X0, Y0 = 430000.0, 4582000.0
FP = box(X0, Y0, X0 + 20, Y0 + 15)  # 300 m2 roof


def _roof(step=0.25, noise=0.03, seed=0):
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(X0 + 0.1, X0 + 20, step), np.arange(Y0 + 0.1, Y0 + 15, step))
    x, y = xs.ravel(), ys.ravel()
    z = 25.0 + rng.normal(0, noise, x.size)
    return x, y, z


def _with(x, y, z, x0, y0, w, h, top):
    inside = (x >= X0 + x0) & (x < X0 + x0 + w) & (y >= Y0 + y0) & (y < Y0 + y0 + h)
    z = z.copy()
    z[inside] = top
    return z


def test_grid_bounds_snap_and_finer_grid_lines_up():
    g = Grid.around(FP, 0.5)
    assert g.x0 % 0.5 == 0 and g.y1 % 0.5 == 0
    f = g.finer(5)
    assert f.bounds == g.bounds and f.width == g.width * 5
    assert upsample(np.ones((2, 3)), 5).shape == (10, 15)


def test_flat_roof_with_stair_housing_and_ac_unit():
    x, y, z = _roof()
    z = _with(x, y, z, 2, 2, 3, 3, 27.8)      # 3 x 3 m stair housing, 2.8 m high
    z = _with(x, y, z, 12, 8, 1, 1, 25.8)     # 1 x 1 m AC unit, 0.8 m high
    r = read_roof_lidar(FP, np.column_stack([x, y, z]))
    s = r.summary
    assert s["roof_area_m2"] == 300.0
    assert s["lidar_coverage"] > 0.99
    assert len(s["surfaces"]) == 1 and abs(s["surfaces"][0]["height_m"] - 25.0) < 0.05
    big = s["obstacles"]
    assert len(big) == 2
    assert 8 <= big[0]["area_m2"] <= 11 and abs(big[0]["height_m"] - 2.8) < 0.15
    assert 0.75 <= big[1]["area_m2"] <= 2.0 and abs(big[1]["height_m"] - 0.8) < 0.15
    # free = roof minus obstacles; usable also loses a 1 m edge strip and 0.5 m clearances
    assert 285 <= s["free_area_m2"] <= 292
    assert 190 <= s["usable_area_m2"] < s["free_area_m2"]


def test_small_raised_terrace_is_an_obstacle_but_set_back_floor_is_a_surface():
    x, y, z = _roof()
    z = _with(x, y, z, 0, 0, 20, 6, 28.0)     # set-back top floor roof: 120 m2, a usable level
    z = _with(x, y, z, 15, 10, 4, 4, 27.5)    # 16 m2 lift room: below MIN_SURFACE_M2
    s = read_roof_lidar(FP, np.column_stack([x, y, z])).summary
    heights = sorted(srf["height_m"] for srf in s["surfaces"])
    assert len(heights) == 2 and abs(heights[0] - 25) < 0.05 and abs(heights[1] - 28) < 0.05
    assert any(14 <= o["area_m2"] <= 18 for o in s["obstacles"])


def test_low_pitched_roof_has_no_usable_surface():
    x, y, _ = _roof()
    z = 20 + 0.27 * np.abs(y - (Y0 + 7.5))   # 15 degree gable: small steps, but not one plane
    s = read_roof_lidar(FP, np.column_stack([x, y, z])).summary
    assert s["surfaces"] == [] and s["usable_area_m2"] == 0


def test_no_points_means_no_usable_area():
    s = read_roof_lidar(FP, np.empty((0, 3))).summary
    assert s["lidar_coverage"] == 0 and s["usable_area_m2"] == 0 and s["surfaces"] == []


def test_usable_mask_setback_and_clearance():
    roof = np.zeros((20, 20), bool)
    roof[2:18, 2:18] = True                   # 16 x 16 cells
    obstacle = np.zeros_like(roof)
    obstacle[9:11, 9:11] = True
    free = roof & ~obstacle
    u = usable_mask(roof, free, obstacle, res=0.5, edge_setback=1.0, clearance=0.5)
    assert not u[2:4, :].any() and u[4, 6]    # the first 1 m (2 cells) from the edge is excluded
    assert u[6, 6]
    assert not u[8, 9] and not u[11, 10]      # next to the obstacle
