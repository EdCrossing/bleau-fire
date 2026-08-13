"""Fetch Sentinel-2 scenes onto the fixed grid, one GeoTIFF per acquisition.

Unlike the eo-agent pipeline, which holds every scene in memory to take a temporal median,
this writes each scene to disk as it goes. The AOI is 8.17 Mpx, so a single scene is ~163 MB
of float32 across five bands; stacking a dozen would be 2 GB of RAM for no reason, since the
analysis here is bi-temporal rather than a composite.

**Reflectance is stored unmasked, with SCL alongside.** Masking at write time would be
destructive and irreversible, and it would specifically destroy the evidence needed for the
question in `mask.scar_shadow_overlap` — whether SCL mistakes fresh char for cloud shadow.
Store the observation and the classification separately; decide what to discard downstream.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import rasterio

from .config import BANDS, MASK_BAND, MASK_CLASSES, SCENE_DIR, Grid
from .mask import scl_histogram, valid_mask
from .raster import read_classes_on_grid, read_on_grid, to_reflectance
from .search import boa_offset

# Bands as written: reflectance, then the raw scene classification.
SCENE_BANDS: tuple[str, ...] = (*BANDS, "scl")


def scene_path(item_id: str) -> Path:
    return SCENE_DIR / f"{item_id}.tif"


def fetch_scene(item: dict, grid: Grid, *, refresh: bool = False) -> tuple[Path, dict]:
    """Read one STAC Item onto the grid and write it as a compressed GeoTIFF.

    Returns the path and a metadata dict recording scene id, acquisition time, BOA offset and
    the SCL histogram — the provenance that any number derived from this scene has to carry.
    """
    out = scene_path(item["id"])
    meta_path = out.with_suffix(".json")
    if out.exists() and meta_path.exists() and not refresh:
        return out, json.loads(meta_path.read_text())

    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    assets = item["assets"]
    missing = [b for b in (*BANDS, MASK_BAND) if b not in assets]
    if missing:
        raise KeyError(f"{item['id']}: missing assets {missing}")

    t0 = time.time()
    offset = boa_offset(item)

    scl = read_classes_on_grid(assets[MASK_BAND]["href"], grid)
    cube = np.empty((len(SCENE_BANDS), *grid.shape), dtype="float32")
    for b, band in enumerate(BANDS):
        cube[b] = to_reflectance(read_on_grid(assets[band]["href"], grid), offset)
    cube[-1] = scl.astype("float32")

    profile = dict(
        driver="GTiff", height=grid.height, width=grid.width, count=len(SCENE_BANDS),
        dtype="float32", crs=grid.crs, transform=grid.transform,
        compress="deflate", predictor=2, tiled=True, blockxsize=512, blockysize=512,
        nodata=np.nan,
    )
    with rasterio.open(out, "w", **profile) as dst:
        for b, name in enumerate(SCENE_BANDS, start=1):
            dst.write(cube[b - 1], b)
            dst.set_band_description(b, name)

    meta = {
        "id": item["id"],
        "datetime": item["properties"]["datetime"],
        "eo:cloud_cover": item["properties"].get("eo:cloud_cover"),
        "s2:processing_baseline": item["properties"].get("s2:processing_baseline"),
        "boa_offset": offset,
        "platform": item["properties"].get("platform"),
        "grid": {"crs": grid.crs.to_string(), "width": grid.width, "height": grid.height,
                 "resolution": grid.resolution},
        "scl_histogram": scl_histogram(scl),
        "seconds": round(time.time() - t0, 1),
        "size_mb": round(out.stat().st_size / 1e6, 1),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    return out, meta


def probe_scene(item: dict, grid: Grid) -> dict:
    """Read only SCL and report what the AOI actually looks like on this date.

    **This step exists because scene-level metadata lies about the AOI**, in two distinct ways:

    1. `eo:cloud_cover` describes the whole 110 km granule, not the 30 km AOI. Known, and the
       reason to filter loosely at search time.
    2. More dangerous: a granule can be a **partial swath**, mostly nodata, while still
       intersecting the search bbox. Cloud cover is computed over *valid* pixels, so a granule
       containing almost no data reports almost no cloud. `S2C_31UDP_20260711_0_L2A` advertises
       0.6% cloud and is 99.4% nodata over Fontainebleau — it looked like the best pre-fire
       scene available and contains nothing.

    Reading one 20 m band per candidate is cheap and settles both questions directly.
    """
    scl = read_classes_on_grid(item["assets"][MASK_BAND]["href"], grid)
    good = valid_mask(scl, MASK_CLASSES)
    return {
        "id": item["id"],
        "datetime": item["properties"]["datetime"],
        "scene_cloud": item["properties"].get("eo:cloud_cover"),
        "aoi_nodata_pct": round(float((scl == 0).mean()) * 100, 2),
        "aoi_valid_pct": round(float(good.mean()) * 100, 2),
        "scl_histogram": scl_histogram(scl),
    }


def read_scene(path: Path) -> tuple[dict[str, np.ndarray], dict]:
    """Load a written scene back as {band_name: array} plus its metadata sidecar."""
    with rasterio.open(path) as src:
        names = [src.descriptions[i] or f"b{i + 1}" for i in range(src.count)]
        arrays = {n: src.read(i + 1) for i, n in enumerate(names)}
    meta = json.loads(path.with_suffix(".json").read_text())
    return arrays, meta
