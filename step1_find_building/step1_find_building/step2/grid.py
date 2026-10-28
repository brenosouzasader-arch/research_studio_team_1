"""A north-up raster grid around one building, in EPSG:25831 metres.

Step 2 works on rasters: the LiDAR roof at 0.5 m and the orthophoto at 0.1 m.
Both grids share the same bounds (snapped to 0.5 m), so one LiDAR cell is
exactly 5 x 5 photo pixels and the two line up without resampling tricks.
"""
import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import box
from shapely.ops import unary_union

SNAP = 0.5  # bounds are multiples of this, so 0.5 m and 0.1 m grids share edges


@dataclass
class Grid:
    x0: float     # left edge
    y1: float     # top edge
    res: float    # metres per cell
    width: int
    height: int

    @classmethod
    def around(cls, footprint, res, buffer=2.0):
        minx, miny, maxx, maxy = footprint.bounds
        x0 = math.floor((minx - buffer) / SNAP) * SNAP
        y0 = math.floor((miny - buffer) / SNAP) * SNAP
        x1 = math.ceil((maxx + buffer) / SNAP) * SNAP
        y1 = math.ceil((maxy + buffer) / SNAP) * SNAP
        return cls(x0, y1, res, round((x1 - x0) / res), round((y1 - y0) / res))

    @property
    def bounds(self):
        return (self.x0, self.y1 - self.height * self.res,
                self.x0 + self.width * self.res, self.y1)

    @property
    def cell_area(self):
        return self.res * self.res

    def finer(self, factor):
        return Grid(self.x0, self.y1, self.res / factor, self.width * factor, self.height * factor)

    def centres(self):
        xs = self.x0 + (np.arange(self.width) + 0.5) * self.res
        ys = self.y1 - (np.arange(self.height) + 0.5) * self.res
        return np.meshgrid(xs, ys)

    def rowcol(self, x, y):
        col = np.floor((np.asarray(x) - self.x0) / self.res).astype(int)
        row = np.floor((self.y1 - np.asarray(y)) / self.res).astype(int)
        return row, col

    def mask(self, geom):
        """True for cells whose centre lies inside geom."""
        X, Y = self.centres()
        return shapely.contains_xy(geom, X, Y)

    def cells_to_polygon(self, mask):
        """Union of the cells marked True, as a (multi)polygon in EPSG:25831."""
        rows, cols = np.nonzero(mask)
        cells = [box(self.x0 + c * self.res, self.y1 - (r + 1) * self.res,
                     self.x0 + (c + 1) * self.res, self.y1 - r * self.res)
                 for r, c in zip(rows, cols)]
        return unary_union(cells) if cells else None


def upsample(array, factor):
    """Repeat each cell factor x factor times (nearest neighbour)."""
    return np.repeat(np.repeat(array, factor, axis=0), factor, axis=1)
