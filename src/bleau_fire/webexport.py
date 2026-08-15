"""Export layers as bare, exactly-georeferenced images for the web viewer.

The earlier renders came out of matplotlib with titles, legends, colourbars and `bbox_inches`
cropping. That was fine for looking at, and wrong for a map: the chrome means the image extent
no longer corresponds to the AOI, so pixel position cannot be converted back to a coordinate,
and layers cannot be stacked or panned honestly.

Everything here writes **the grid and nothing but the grid** — no axes, no labels, exact AOI
extent — so the browser can map pixel to longitude/latitude by linear interpolation, draw its
own graticule, and overlay layers that actually register with each other. Legends move to HTML,
where they belong anyway.

The historic layers come from IGN's WMS, which returns an arbitrary bbox as an image, so they
land on the same extent by construction:

    ORTHOIMAGERY.ORTHOPHOTOS.1950-1965   aerial photography, mid-century
    ORTHOIMAGERY.ORTHOPHOTOS.1965-1980
    GEOGRAPHICALGRIDSYSTEMS.ETATMAJOR40  the 1820-1866 État-Major survey
    IGNF_FORETS-ANCIENNES                woodland continuously forested since that survey
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from .config import AOI, Grid

WMS = "https://data.geopf.fr/wms-r/wms"
HEADERS = {"User-Agent": "bleau-fire/0.1 (Fontainebleau burn severity)"}

HISTORIC = {
    "ortho1950": "ORTHOIMAGERY.ORTHOPHOTOS.1950-1965",
    "ortho1965": "ORTHOIMAGERY.ORTHOPHOTOS.1965-1980",
    "etatmajor": "GEOGRAPHICALGRIDSYSTEMS.ETATMAJOR40",
    "anciennes": "IGNF_FORETS-ANCIENNES",
    "ortho_now": "ORTHOIMAGERY.ORTHOPHOTOS",
}

# Layers whose colours ARE their data. Stored lossless — see fetch_wms.
CATEGORICAL = {"anciennes"}

# A wider region drawn behind everything, so zooming out shows where in France this is rather
# than the AOI floating on black. Roughly 2.3 x 1.7 degrees — Paris to Orleans.
CONTEXT_BBOX: tuple[float, float, float, float] = (1.30, 47.70, 3.90, 49.30)
CONTEXT = {
    "ctx_map": "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2",
    "ctx_ortho": "ORTHOIMAGERY.ORTHOPHOTOS",
}


def fetch_wms(
    layer: str, out: Path, *, bbox: tuple[float, float, float, float] = AOI,
    width: int = 2000, refresh: bool = False, categorical: bool = False,
) -> Path:
    """Fetch one WMS layer at the given pixel width.

    ⚠️ WMS 1.3.0 with `CRS=EPSG:4326` takes BBOX as `miny,minx,maxy,maxx` — latitude first,
    the same axis-order trap as the WFS calls in `ign.py`.

    ⚠️ **`categorical=True` forces PNG, and that is a correctness requirement, not a quality
    preference.** A rendered thematic layer encodes its classes *as colours*. JPEG is lossy and
    interpolates between neighbouring colours, so a boundary between two classes acquires a
    gradient of invented intermediate colours that belong to no class at all. Reading classes
    back out then needs a tolerance, which silently mis-assigns edge pixels. This is the same
    rule as using nearest-neighbour resampling for class rasters, applied to compression.
    """
    if out.exists() and not refresh:
        return out
    w, s, e, n = bbox
    height = int(round(width * (n - s) / (e - w)))
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": layer, "STYLES": "", "CRS": "EPSG:4326",
        "BBOX": f"{s},{w},{n},{e}", "WIDTH": str(width), "HEIGHT": str(height),
        "FORMAT": "image/png",
    }
    r = requests.get(WMS, params=params, headers=HEADERS, timeout=300)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    if categorical:
        # Quantise to the palette actually present, then store losslessly.
        img.quantize(colors=16, method=Image.MEDIANCUT).save(out, "PNG", optimize=True)
    else:
        img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out


def _resize(arr_rgb: np.ndarray, width: int) -> Image.Image:
    img = Image.fromarray(arr_rgb)
    h = int(round(img.height * width / img.width))
    return img.resize((width, h), Image.LANCZOS)


def write_rgb(arrays: dict[str, np.ndarray], out: Path, *, width: int = 2000,
              scale: float = 0.30) -> Path:
    """True-colour image with a **fixed** stretch.

    Fixed rather than per-scene percentiles so pre and post are directly comparable — a
    per-image stretch would rescale the post-fire scene to hide the very darkening being
    measured.
    """
    rgb = np.stack([arrays["red"], arrays["green"], arrays["blue"]], axis=-1)
    img = np.clip(np.nan_to_num(rgb) / scale, 0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    _resize((img * 255).astype("uint8"), width).save(
        out, "JPEG", quality=84, optimize=True, progressive=True
    )
    return out


def write_colormapped(arr: np.ndarray, out: Path, *, cmap: str = "RdYlGn_r",
                      vmin: float = -0.3, vmax: float = 1.0, width: int = 2000,
                      alpha_below: float | None = None) -> Path:
    """Colour-map a continuous raster to PNG, optionally transparent below a threshold.

    `alpha_below` matters for an overlay: a dNBR layer painted opaque everywhere hides the
    imagery underneath, so unburned pixels are made transparent and the layer reads as an
    annotation rather than a replacement.
    """
    import matplotlib as mpl
    from matplotlib.colors import Normalize

    norm = Normalize(vmin=vmin, vmax=vmax)
    rgba = (mpl.colormaps[cmap](norm(np.nan_to_num(arr, nan=vmin))) * 255).astype("uint8")
    if alpha_below is not None:
        rgba[..., 3] = np.where(np.nan_to_num(arr, nan=vmin) < alpha_below, 0, 255)
    img = Image.fromarray(rgba, mode="RGBA")
    h = int(round(img.height * width / img.width))
    img = img.resize((width, h), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def write_classes(classes: np.ndarray, colours: list[str], out: Path, *,
                  width: int = 2000, transparent_below: int = 0) -> Path:
    """Render an integer class raster with an explicit palette, nodata transparent."""
    from matplotlib.colors import to_rgb

    h, w = classes.shape
    rgba = np.zeros((h, w, 4), dtype="uint8")
    for i, hexc in enumerate(colours):
        m = classes == i
        if m.any():
            rgba[m, :3] = np.array([int(c * 255) for c in to_rgb(hexc)], dtype="uint8")
            rgba[m, 3] = 255
    rgba[classes < transparent_below, 3] = 0
    img = Image.fromarray(rgba, mode="RGBA")
    img = img.resize((width, int(round(h * width / w))), Image.NEAREST)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def grid_bounds_wgs84(grid: Grid) -> dict:
    """The grid's exact WGS84 bounds, so the browser can map pixels to coordinates."""
    from pyproj import Transformer

    tf = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    xs = [grid.transform.c, grid.transform.c + grid.width * grid.resolution]
    ys = [grid.transform.f - grid.height * grid.resolution, grid.transform.f]
    lons, lats = tf.transform([xs[0], xs[1], xs[0], xs[1]], [ys[0], ys[0], ys[1], ys[1]])
    return {"west": min(lons), "east": max(lons), "south": min(lats), "north": max(lats)}
