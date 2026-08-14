"""Terrain predictors from the Copernicus GLO-30 DEM.

Anonymous COGs on AWS (`copernicus-dem-30m`), read with the same windowed `WarpedVRT` machinery
as the imagery so elevation lands on the identical grid. Fontainebleau is not mountainous —
roughly 37 to 170 m — but the massif is a sandstone plateau cut by ridges and dry valleys, and
that relief is exactly what steers a surface fire.

⚠️ **Aspect is circular and must not be fed to a model as degrees.** 359° and 1° are adjacent,
not 358 apart. It is decomposed into `northness` (cos) and `eastness` (sin), which are the
components that actually carry meaning: northness is the insolation axis, and a south-facing
slope in the northern hemisphere is drier and warmer, so it should burn harder if aspect matters
at all.

⚠️ **The DEM is 30 m and the grid is 10 m.** Resampling up does not create detail; slope
computed after upsampling is smoother than the truth. Slope and aspect are therefore derived at
native resolution *first* and only then put on the target grid, which is the honest order.
"""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from .config import Grid

DEM_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)

DEM_ENV = dict(
    AWS_NO_SIGN_REQUEST="YES",
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MULTIPLEX="YES",
    VSI_CACHE="TRUE",
)


def dem_url(lon: float, lat: float) -> str:
    return DEM_URL.format(
        ns="N" if lat >= 0 else "S", lat=int(abs(np.floor(lat))),
        ew="E" if lon >= 0 else "W", lon=int(abs(np.floor(lon))),
    )


def elevation_on_grid(grid: Grid, *, lon: float = 2.5, lat: float = 48.4) -> np.ndarray:
    """Read the DEM tile covering the AOI onto the target grid."""
    with rasterio.Env(**DEM_ENV):
        with rasterio.open(dem_url(lon, lat)) as src:
            with WarpedVRT(
                src, crs=grid.crs, transform=grid.transform,
                width=grid.width, height=grid.height, resampling=Resampling.bilinear,
            ) as vrt:
                return vrt.read(1, masked=True).astype("float32").filled(np.nan)


def slope_aspect(grid: Grid, *, lon: float = 2.5, lat: float = 48.4
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slope (degrees), northness and eastness, derived natively then reprojected.

    Horn's method on the native 30 m grid. Computing gradients after upsampling to 10 m would
    smooth real terrain into a gentler, wrong surface.
    """
    from rasterio.warp import reproject

    with rasterio.Env(**DEM_ENV):
        with rasterio.open(dem_url(lon, lat)) as src:
            arr = src.read(1).astype("float64")
            # Metres per degree at this latitude — the DEM is in geographic coordinates,
            # so a raw np.gradient would mix degrees and metres and give nonsense slopes.
            mid_lat = src.bounds.bottom + (src.bounds.top - src.bounds.bottom) / 2
            my = 111_320.0 * abs(src.res[1])
            mx = 111_320.0 * np.cos(np.radians(mid_lat)) * abs(src.res[0])
            dzdy, dzdx = np.gradient(arr, my, mx)

            slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype("float32")
            aspect = np.arctan2(-dzdx, dzdy)
            north = np.cos(aspect).astype("float32")
            east = np.sin(aspect).astype("float32")

            out = []
            for band in (slope, north, east):
                dst = np.full(grid.shape, np.nan, dtype="float32")
                reproject(
                    band, dst, src_transform=src.transform, src_crs=src.crs,
                    dst_transform=grid.transform, dst_crs=grid.crs,
                    resampling=Resampling.bilinear,
                )
                out.append(dst)
    return tuple(out)


def distance_to(mask: np.ndarray, grid: Grid) -> np.ndarray:
    """Euclidean distance in metres to the nearest True cell.

    Used for distance to the track and road network — forest roads at Fontainebleau double as
    firebreaks and access for ground crews, so proximity is a plausible severity control rather
    than an arbitrary covariate.
    """
    from scipy import ndimage

    if not mask.any():
        return np.full(grid.shape, np.nan, dtype="float32")
    return (ndimage.distance_transform_edt(~mask) * grid.resolution).astype("float32")


def rasterise_lines(gdf, grid: Grid) -> np.ndarray:
    """Burn line features onto the grid as a boolean mask."""
    import rasterio.features

    shapes = [(g, 1) for g in gdf.to_crs(grid.crs).geometry if g is not None and not g.is_empty]
    return rasterio.features.rasterize(
        shapes, out_shape=grid.shape, transform=grid.transform,
        fill=0, dtype="uint8", all_touched=True,
    ).astype(bool)
