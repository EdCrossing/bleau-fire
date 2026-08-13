"""Configuration and the target grid.

Grid handling here is adapted from `~/projects/eo-agent` (`eo_agent.config`). It is vendored
rather than imported so this repo stands alone, and the reasoning is worth restating: **the
target grid is defined once, here, and everything is forced onto it** — every band, every
scene, and the climbing vectors too. Sentinel-2 arrives in UTM zones at three resolutions, and
letting each asset keep its native grid produces silent misalignment that only surfaces later
as an inexplicable result.

The whole massif fits inside a single MGRS granule (**31UDP**), so no mosaicking is needed.
Sentinel-2's swath is 290 km and products are tiled at 110 x 110 km; the AOI below is roughly
30 x 28 km. A fire straddling a tile boundary would have been materially more work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS, Transformer
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCENE_DIR = DATA / "scenes"
VECTOR_DIR = DATA / "vectors"
OUT_DIR = DATA / "out"
CACHE_DIR = DATA / "stac-cache"

# The Fontainebleau massif: (west, south, east, north) in EPSG:4326.
# Chosen to contain both fire sectors (Noisy-sur-Ecole ~1,500 ha in the south-west,
# Faisanderie ~450 ha in the north-east) and every mapped climbing area.
AOI: tuple[float, float, float, float] = (2.40, 48.25, 2.80, 48.50)

STAC_API = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# Earth Search asset keys. Native resolutions differ: the first four are 10 m, swir22 is 20 m.
#   red/green/blue -> quicklooks, and looking at your data is not optional
#   nir  (B08, 10 m) and swir22 (B12, 20 m) -> NBR
BANDS: tuple[str, ...] = ("blue", "green", "red", "nir", "swir22")
MASK_BAND = "scl"

# SCL classes discarded per pixel. Same set as eo-agent, for comparability.
#   0 nodata · 1 saturated/defective · 3 cloud shadow · 8 cloud med · 9 cloud high · 10 cirrus
MASK_CLASSES: tuple[int, ...] = (0, 1, 3, 8, 9, 10)

# The fire. Ignition 12 July 2026; Copernicus EMS activation EMSR894.
FIRE_START = "2026-07-12"

# The bi-temporal pair, chosen on **measured AOI usability** rather than scene metadata.
# Ten days apart, so phenology is effectively held constant — the difference is the fire.
#
# Both are Sentinel-2A. That is deliberate: a difference index compares two radiometric
# measurements, so using one satellite for both removes inter-sensor calibration offsets from
# the result rather than assuming they are negligible.
#
# ⚠️ The obvious pick was `S2C_31UDP_20260711_0_L2A` — 0.6% cloud, one day before ignition.
# It is 99.4% nodata over this AOI: a partial swath that intersects the search bbox while
# containing almost nothing. Scene cloud cover is computed over *valid* pixels, so a nearly
# empty granule advertises nearly zero cloud. Seven scenes in this series fail that way.
# Run `run.py probe` before trusting any scene; see `scenes.probe_scene`.
PRE_SCENE = "S2A_31UDP_20260710_0_L2A"   # 2026-07-10, 99.62% usable, 2 days before ignition
POST_SCENE = "S2A_31UDP_20260720_0_L2A"  # 2026-07-20, 100.00% usable, 8 days after


@dataclass(frozen=True)
class Grid:
    """A fixed raster grid. Immutable on purpose — nothing should mutate this mid-run."""

    crs: CRS
    transform: object  # affine.Affine
    width: int
    height: int
    resolution: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    def describe(self) -> str:
        return (
            f"{self.crs.to_string()}  {self.width}x{self.height} px  "
            f"@{self.resolution:g} m  ({self.width * self.height / 1e6:.2f} Mpx)"
        )


def utm_crs_for(bbox: tuple[float, float, float, float]) -> CRS:
    """Pick the UTM zone containing the bbox centroid.

    Metres rather than degrees, so a 10 m pixel really is 10 m and hectare counts mean
    something. For this AOI it resolves to EPSG:32631, matching the granule's own zone —
    which means reprojection is close to a no-op and resampling error stays negligible.
    """
    lon = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def build_grid(
    bbox: tuple[float, float, float, float] = AOI,
    resolution: float = 10.0,
    crs: CRS | None = None,
) -> Grid:
    """Build the target grid, snapped so runs and AOIs stay mutually alignable."""
    crs = crs or utm_crs_for(bbox)
    tf = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = tf.transform([bbox[0], bbox[2]], [bbox[1], bbox[3]])

    west = (min(xs) // resolution) * resolution
    south = (min(ys) // resolution) * resolution
    east = -(-max(xs) // resolution) * resolution  # ceil
    north = -(-max(ys) // resolution) * resolution

    return Grid(
        crs=crs,
        transform=from_origin(west, north, resolution, resolution),
        width=int(round((east - west) / resolution)),
        height=int(round((north - south) / resolution)),
        resolution=resolution,
    )
