"""Rasterise land-cover vectors onto the grid, and cross them against burn severity.

This is where the project stops describing a burn and starts asking a question about it: fire
severity is a function of *fuel*, and BD Forêt V2 supplies the fuel labels — Scots pine, oak,
beech, heath — for every polygon in the massif.

Two jobs:

1. **Fix the area statistic.** `FINDINGS.md` F2 recorded that dNBR over farmland picks up
   harvest between the two dates and inflates the burned area. RPG says exactly where the
   declared agricultural parcels are, so the contamination can be measured rather than
   estimated, and the forest-only total becomes comparable to the EMS figure.

2. **Test the fuel-bias argument with labels instead of assertion.** The textbook expectation
   is that dNBR reads low over sparse fuel and high over dense fuel at equal fire intensity,
   and that RdNBR compresses the gap. Measured here, **both halves of that came out
   backwards** — heath reads highest, and RdNBR widens the spread. See `FINDINGS.md` F5. The
   value of having real species polygons is precisely that a plausible story could be checked
   and turned out to be wrong.

⚠️ **Rasterise categorical data with `all_touched=False` and nearest semantics.** These are
class codes; there is no meaningful interpolation between "Chênes décidus" and "Pin sylvestre".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import rasterio.features

from .config import Grid


def rasterise(gdf, grid: Grid, field: str) -> tuple[np.ndarray, dict[int, str]]:
    """Burn a categorical vector field onto the grid.

    Returns an int16 raster of class codes (-1 outside any polygon) and the code -> label map.
    """
    values = sorted(v for v in gdf[field].dropna().unique())
    codes = {v: i for i, v in enumerate(values)}

    shapes = [
        (geom, codes[val])
        for geom, val in zip(gdf.to_crs(grid.crs).geometry, gdf[field])
        if geom is not None and not geom.is_empty and val in codes
    ]
    arr = rasterio.features.rasterize(
        shapes,
        out_shape=grid.shape,
        transform=grid.transform,
        fill=-1,
        dtype="int32",
        all_touched=False,
    ).astype("int16")
    return arr, {i: v for v, i in codes.items()}


def mask_from(gdf, grid: Grid) -> np.ndarray:
    """Boolean coverage mask for a vector layer — True inside any polygon."""
    shapes = [
        (geom, 1) for geom in gdf.to_crs(grid.crs).geometry
        if geom is not None and not geom.is_empty
    ]
    return rasterio.features.rasterize(
        shapes, out_shape=grid.shape, transform=grid.transform,
        fill=0, dtype="uint8", all_touched=False,
    ).astype(bool)


def fire_footprint(burned: np.ndarray, *, min_size: int = 2000) -> np.ndarray:
    """Approximate the fire perimeter from the burn mask.

    Severity comparisons between fuel types are only meaningful *inside the fire*: a heath
    polygon 8 km from the flames is unburned because no fire reached it, not because heath
    burns cold, and including it would manufacture exactly the fuel effect being tested.

    Built by closing small gaps in the >=0.27 mask, filling interior holes (unburned islands
    genuinely inside the perimeter), and keeping only components above `min_size` pixels
    (2,000 px = 200 ha) so scattered agricultural false positives don't become "fires".
    """
    from scipy import ndimage

    closed = ndimage.binary_closing(burned, structure=np.ones((7, 7)))
    filled = ndimage.binary_fill_holes(closed)
    labels, n = ndimage.label(filled)
    if n == 0:
        return np.zeros_like(burned, dtype=bool)
    sizes = ndimage.sum(filled, labels, range(1, n + 1))
    keep = {i + 1 for i, s in enumerate(sizes) if s >= min_size}
    return np.isin(labels, list(keep))


def severity_by_class(
    classes: np.ndarray,
    labels: dict[int, str],
    delta: np.ndarray,
    rel: np.ndarray,
    footprint: np.ndarray,
    pixel_area_m2: float,
    *,
    min_ha: float = 5.0,
) -> pd.DataFrame:
    """Burn statistics per land-cover class, restricted to inside the fire footprint."""
    rows = []
    for code, label in labels.items():
        sel = (classes == code) & footprint
        n = int(sel.sum())
        ha = n * pixel_area_m2 / 1e4
        if ha < min_ha:
            continue
        d, r = delta[sel], rel[sel]
        d = d[np.isfinite(d)]
        r = r[np.isfinite(r)]
        rows.append({
            "class": label,
            "ha_in_fire": round(ha, 1),
            "pct_burned": round(100.0 * float((d >= 0.27).mean()), 1),
            "dnbr_median": round(float(np.median(d)), 3),
            "dnbr_p90": round(float(np.percentile(d, 90)), 3),
            "rdnbr_median": round(float(np.median(r)), 3) if r.size else np.nan,
        })
    return pd.DataFrame(rows).sort_values("dnbr_median", ascending=False)
