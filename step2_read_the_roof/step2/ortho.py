"""ICGC orthophoto for one building, from the ICGC WMS (CC BY 4.0, no key).

The image is cut to the same bounds as the LiDAR grid, at 0.1 m per pixel, so
pixel (r, c) of the photo sits inside LiDAR cell (r // 5, c // 5).

If the request fails, run `python -m step2.ortho --layers` to list the layers
the service offers and set ORTHO_LAYER to the current colour orthophoto.
"""
import io
import xml.etree.ElementTree as ET

import numpy as np
import requests
from PIL import Image

WMS_URL = "https://geoserveis.icgc.cat/servei/catalunya/orto-territorial/wms"
ORTHO_LAYER = "ortofoto_color_vigent"   # latest colour orthophoto
PHOTO_RES = 0.1                         # metres per pixel we ask for
TIMEOUT = 60


class OrthoError(Exception):
    pass


def get_ortho(grid, url=WMS_URL, layer=ORTHO_LAYER):
    """Return an RGB uint8 array (height x width x 3) covering grid.bounds."""
    minx, miny, maxx, maxy = grid.bounds
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": layer, "STYLES": "", "SRS": "EPSG:25831",
        "BBOX": f"{minx},{miny},{maxx},{maxy}",
        "WIDTH": grid.width, "HEIGHT": grid.height, "FORMAT": "image/png",
    }
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.headers.get("Content-Type", "").startswith("image/"):
        raise OrthoError(f"ICGC WMS did not return an image: {r.text[:300]}")
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    if img.size != (grid.width, grid.height):
        img = img.resize((grid.width, grid.height))
    return np.asarray(img)


def list_layers(url=WMS_URL):
    r = requests.get(url, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    names = [e.text for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Name" and e.text]
    return names


def save_png(rgb, path, grid=None):
    """Save the image, plus a world file (.pgw) so QGIS places it correctly."""
    Image.fromarray(rgb).save(path)
    if grid is not None:
        with open(str(path)[:-4] + ".pgw", "w") as f:
            f.write(f"{grid.res}\n0\n0\n{-grid.res}\n"
                    f"{grid.x0 + grid.res / 2}\n{grid.y1 - grid.res / 2}\n")


if __name__ == "__main__":
    import sys
    if "--layers" in sys.argv:
        print("\n".join(list_layers()))
