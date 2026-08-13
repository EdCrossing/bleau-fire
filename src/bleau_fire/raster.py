"""Windowed, reprojected reads straight out of remote COGs.

Vendored from `~/projects/eo-agent` (`eo_agent.raster`), and the module worth understanding,
because it is what makes this workable on a home machine.

Each Sentinel-2 asset URL points at a Cloud Optimized GeoTIFF covering a 110 x 110 km granule.
You never download it. A COG is internally tiled with pyramid overviews and a header at a known
offset, so GDAL issues HTTP **byte-range requests** for exactly the tiles overlapping the AOI.
Reading a 30 km box out of a 110 km granule transfers a few MB, not the whole file.

`WarpedVRT` then does reprojection, resampling and windowing in one virtual step, so every band
lands on the *identical* target grid regardless of native CRS or resolution. That is what makes
the 10 m and 20 m bands stackable without hand-rolled alignment code — and NBR mixes exactly
those two resolutions (B08 at 10 m, B12 at 20 m), so it matters here specifically.

Resampling method matters and is not a detail:
  - reflectance  -> bilinear (continuous values, interpolation is meaningful)
  - class labels -> nearest  (interpolating class 8 and class 4 into "class 6" is nonsense)
"""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from .config import Grid

# GDAL tuning for remote COG access. Without these GDAL issues far more HTTP requests than
# necessary and reads get slow enough that you assume something is broken.
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",  # don't list the bucket dir on every open
    GDAL_HTTP_MULTIPLEX="YES",
    GDAL_HTTP_VERSION="2",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="536870912",  # 512 MB
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.jp2",
    AWS_NO_SIGN_REQUEST="YES",  # sentinel-cogs is public and anonymous
)


def read_on_grid(
    href: str,
    grid: Grid,
    *,
    resampling: Resampling = Resampling.bilinear,
    fill: float = np.nan,
) -> np.ndarray:
    """Read `href` reprojected onto `grid` as float32 of grid.shape.

    Nodata becomes `fill` (NaN by default) so downstream masking has one convention.
    """
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src:
            with WarpedVRT(
                src,
                crs=grid.crs,
                transform=grid.transform,
                width=grid.width,
                height=grid.height,
                resampling=resampling,
            ) as vrt:
                arr = vrt.read(1, masked=True)

    return arr.astype("float32").filled(fill)


def read_classes_on_grid(href: str, grid: Grid) -> np.ndarray:
    """Read a categorical raster (SCL) onto the grid with nearest-neighbour resampling.

    Returned as uint8 with 0 for nodata, matching SCL's own convention where 0 == no data.
    """
    arr = read_on_grid(href, grid, resampling=Resampling.nearest, fill=0.0)
    return np.nan_to_num(arr, nan=0.0).astype("uint8")


def to_reflectance(dn: np.ndarray, offset: float) -> np.ndarray:
    """Convert Sentinel-2 L2A digital numbers to surface reflectance.

        reflectance = (DN + offset) / 10000     (offset is 0 or -1000, see search.boa_offset)

    Clipped at 0: the offset can push genuinely dark pixels slightly negative, which is
    physically meaningless and upsets anything computing a normalised ratio — which is
    precisely what NBR is.
    """
    return np.clip((dn + offset) / 10000.0, 0.0, None)
