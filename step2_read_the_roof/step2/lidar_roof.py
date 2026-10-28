"""Step 2a: read the roof from LiDAR alone, with rules (no AI).

This is the baseline the AI model is measured against. It follows the
plane-growing idea of Awrangjeb & Fraser (2014, Remote Sensing 6(5) 3716), in
raster form:

1. Make a 0.5 m height grid of the roof (highest building point per cell).
2. Grow surfaces: neighbouring cells join when their heights differ by at
   most 0.15 m (the paper's plane-distance tolerance). Surfaces steeper than
   10 degrees overall are dropped.
3. Surfaces of at least 20 m2 are usable roof levels (main terrace, set-back
   top floor). Everything else on the roof is an obstacle: stair housings,
   lift rooms, tanks, AC units, parapets.
4. Usable area = surfaces, minus a strip along the roof edge and a clearance
   around each obstacle.

LiDAR misses objects under about 0.5 m2 and things that barely stick out
(skylights, existing panels). That gap is what the photo model fills.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from step1 import lidar as step1_lidar

from .grid import Grid

GRID_RES = 0.5            # metres per LiDAR cell
FLAT_SLOPE_DEG = 10.0     # surfaces steeper than this overall are ramps or pitched parts
HEIGHT_TOL = 0.15         # max height step between neighbours in one surface
MIN_SURFACE_M2 = 20.0     # smaller raised flat areas (stair housing tops) count as obstacles
MIN_OBSTACLE_M2 = 0.5     # smaller blobs are listed as noise, but still not usable
EDGE_SETBACK_M = 1.0      # keep panels this far from the roof edge
OBSTACLE_CLEARANCE_M = 0.5


@dataclass
class LidarRoof:
    grid: Grid
    roof: np.ndarray            # bool: cell inside the footprint
    dsm: np.ndarray             # float: roof height (NaN = no points)
    surface_id: np.ndarray      # int: -1 = not a usable surface
    obstacle: np.ndarray        # bool: on the roof but not on a surface
    usable: np.ndarray          # bool: surface after edge setback and clearances
    summary: dict = field(default_factory=dict)


def rasterise_max(points, grid):
    dsm = np.full((grid.height, grid.width), np.nan)
    if len(points) == 0:
        return dsm
    r, c = grid.rowcol(points[:, 0], points[:, 1])
    ok = (r >= 0) & (r < grid.height) & (c >= 0) & (c < grid.width)
    r, c, z = r[ok], c[ok], points[ok, 2]
    order = np.argsort(z)  # lowest first, so the highest point per cell wins
    dsm[r[order], c[order]] = z[order]
    return dsm


def grow_surfaces(dsm, res, flat_slope_deg=FLAT_SLOPE_DEG, tol=HEIGHT_TOL):
    """Label connected areas whose neighbouring cells differ by <= tol in height.

    An area whose best-fit plane is steeper than flat_slope_deg, or that is not
    one plane (fit error above tol), is dropped (-1). Per-cell slopes are not used, because they
    blur every wall into the cells next to it and make obstacles look bigger.
    """
    h, w = dsm.shape
    has = ~np.isnan(dsm)
    idx = np.arange(h * w).reshape(h, w)
    rows, cols = [], []
    for a, b in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),   # right
                 ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):  # down
        ok = has[a] & has[b] & (np.abs(dsm[a] - dsm[b]) <= tol)
        rows.append(idx[a][ok])
        cols.append(idx[b][ok])
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(h * w, h * w))
    _, labels = connected_components(graph, directed=False)
    labels = labels.reshape(h, w)
    labels[~has] = -1

    rr, cc = np.indices((h, w))
    ids, counts = np.unique(labels[labels >= 0], return_counts=True)
    for lab in ids[counts >= 3]:
        cells = labels == lab
        A = np.column_stack([cc[cells] * res, rr[cells] * res, np.ones(cells.sum())])
        coef, *_ = np.linalg.lstsq(A, dsm[cells], rcond=None)
        rms = np.sqrt(np.mean((A @ coef - dsm[cells]) ** 2))
        if np.degrees(np.arctan(np.hypot(coef[0], coef[1]))) > flat_slope_deg or rms > tol:
            labels[cells] = -1  # a ramp, or not one plane at all (e.g. a low-pitched gable)
    return labels


def read_roof_lidar(footprint, points, res=GRID_RES):
    """footprint: shapely polygon (EPSG:25831); points: N x 3 building points."""
    grid = Grid.around(footprint, res)
    roof = grid.mask(footprint)
    dsm = rasterise_max(points, grid)
    dsm[~roof] = np.nan
    has = ~np.isnan(dsm)

    labels = grow_surfaces(dsm, res)
    surface_id = np.full(labels.shape, -1)
    surfaces = []
    ids, counts = np.unique(labels[labels >= 0], return_counts=True)
    for k, (lab, n) in enumerate(sorted(zip(ids, counts), key=lambda t: -t[1])):
        if n * grid.cell_area < MIN_SURFACE_M2:
            break
        cells = labels == lab
        surface_id[cells] = len(surfaces)
        surfaces.append({"height_m": round(float(np.median(dsm[cells])), 2),
                         "area_m2": round(float(n * grid.cell_area), 1)})

    obstacle = has & roof & (surface_id < 0)
    on_surface = surface_id >= 0
    obstacles, noise_m2 = [], 0.0
    blobs, n_blobs = ndimage.label(obstacle, structure=np.ones((3, 3)))
    for b in range(1, n_blobs + 1):
        cells = blobs == b
        area = float(cells.sum() * grid.cell_area)
        if area < MIN_OBSTACLE_M2:
            noise_m2 += area
            continue
        ring = ndimage.binary_dilation(cells, iterations=2) & on_surface
        base = np.median(dsm[ring]) if ring.any() else np.nanmin(dsm)
        rr, cc = np.nonzero(cells)
        x, y = grid.x0 + (cc.mean() + 0.5) * res, grid.y1 - (rr.mean() + 0.5) * res
        obstacles.append({"area_m2": round(area, 2),
                          "height_m": round(float(np.nanmax(dsm[cells]) - base), 2),
                          "x": round(float(x), 2), "y": round(float(y), 2)})

    usable = usable_mask(roof, on_surface, obstacle, res)
    roof_cells = int(roof.sum())
    summary = {
        "roof_area_m2": round(roof_cells * grid.cell_area, 1),
        "lidar_coverage": round(float(has[roof].mean()), 3) if roof_cells else 0.0,
        "surfaces": surfaces,
        "obstacles": sorted(obstacles, key=lambda o: -o["area_m2"]),
        "obstacle_area_m2": round(float(obstacle.sum() * grid.cell_area), 1),
        "noise_area_m2": round(noise_m2, 1),
        "free_area_m2": round(float(on_surface.sum() * grid.cell_area), 1),
        "usable_area_m2": round(float(usable.sum() * grid.cell_area), 1),
    }
    return LidarRoof(grid, roof, dsm, surface_id, obstacle, usable, summary)


def usable_mask(roof, free, obstacle, res,
                edge_setback=EDGE_SETBACK_M, clearance=OBSTACLE_CLEARANCE_M):
    """Free cells at least edge_setback from the roof edge and clearance from obstacles."""
    # distance from a cell centre to the nearest outside / obstacle cell, minus half a cell
    to_edge = (ndimage.distance_transform_edt(roof) - 0.5) * res
    to_obstacle = (ndimage.distance_transform_edt(~obstacle) - 0.5) * res if obstacle.any() \
        else np.full(roof.shape, np.inf)
    return free & roof & (to_edge > edge_setback) & (to_obstacle > clearance)


def read_roof_from_tiles(footprint, laz_dir):
    tiles = step1_lidar.find_tiles(footprint, laz_dir)
    if not tiles:
        return None
    return read_roof_lidar(footprint, step1_lidar.roof_points(footprint, tiles))
