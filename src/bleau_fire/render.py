"""Rendering. Look at your data — this is not an optional step.

Every geospatial bug worth catching early is obvious in a picture and invisible in summary
statistics: wrong CRS, off-by-one grid, transposed bands, inverted mask. The vector overlay
carries an extra check the raster alone cannot give you — if the OSM climbing points land in
plausible places (on the sandstone, not in the middle of a field), the projection chain from
EPSG:4326 through the grid CRS to pixel coordinates is right.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

from .burn import CLASS_COLOURS, CLASS_LABELS  # noqa: E402
from .config import Grid  # noqa: E402


def to_pixels(gdf: gpd.GeoDataFrame, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Project features to the grid CRS and convert to (col, row) pixel coordinates."""
    pts = gdf.to_crs(grid.crs)
    inv = ~grid.transform
    cols, rows = inv * (pts.geometry.x.values, pts.geometry.y.values)
    return np.asarray(cols), np.asarray(rows)


def _extent_window(grid: Grid, zoom: tuple[float, float, float, float] | None):
    """Convert an optional lon/lat zoom box to pixel slice bounds."""
    if zoom is None:
        return None
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    xs, ys = tf.transform([zoom[0], zoom[2]], [zoom[1], zoom[3]])
    inv = ~grid.transform
    c0, r0 = inv * (min(xs), max(ys))
    c1, r1 = inv * (max(xs), min(ys))
    return (
        max(0, int(c0)), min(grid.width, int(c1) + 1),
        max(0, int(r0)), min(grid.height, int(r1) + 1),
    )


def stretch_rgb(red: np.ndarray, green: np.ndarray, blue: np.ndarray,
                lo: float = 2.0, hi: float = 98.0) -> np.ndarray:
    """Percentile-stretched RGB. Percentiles, not min/max — one bright cloud ruins min/max."""
    rgb = np.stack([red, green, blue], axis=-1)
    finite = rgb[np.isfinite(rgb)]
    if finite.size == 0:
        return np.zeros_like(rgb)
    vlo, vhi = np.percentile(finite, [lo, hi])
    return np.clip((rgb - vlo) / max(vhi - vlo, 1e-6), 0, 1)


def _overlay(ax, gdf, grid, window, *, size=6, edge="cyan", label_top=0):
    """Scatter climbing features, optionally labelling the largest sectors."""
    if gdf is None or len(gdf) == 0:
        return
    cols, rows = to_pixels(gdf, grid)
    if window:
        c0, c1, r0, r1 = window
        keep = (cols >= c0) & (cols < c1) & (rows >= r0) & (rows < r1)
        cols, rows, gdf = cols[keep] - c0, rows[keep] - r0, gdf[keep]
    ax.scatter(cols, rows, s=size, facecolors="none", edgecolors=edge, linewidths=0.7,
               alpha=0.9, zorder=3)

    if label_top:
        crags = gdf[gdf["feature_type"] == "crag"]
        if len(crags):
            ccols, crows = to_pixels(crags, grid)
            if window:
                ccols, crows = ccols - window[0], crows - window[2]
            for (_, row), x, y in list(zip(crags.iterrows(), ccols, crows))[:label_top]:
                if row.get("name"):
                    ax.annotate(row["name"], (x, y), fontsize=6, color="white",
                                xytext=(3, 3), textcoords="offset points", zorder=4,
                                path_effects=None)


def plot_rgb(arrays: dict[str, np.ndarray], grid: Grid, out: Path, *,
             gdf: gpd.GeoDataFrame | None = None, title: str = "",
             zoom: tuple[float, float, float, float] | None = None,
             label_top: int = 0) -> Path:
    """True-colour composite with climbing features overlaid."""
    window = _extent_window(grid, zoom)
    r, g, b = arrays["red"], arrays["green"], arrays["blue"]
    if window:
        c0, c1, r0, r1 = window
        r, g, b = r[r0:r1, c0:c1], g[r0:r1, c0:c1], b[r0:r1, c0:c1]

    img = stretch_rgb(r, g, b)
    fig, ax = plt.subplots(figsize=(13, 13 * img.shape[0] / img.shape[1]))
    ax.imshow(np.nan_to_num(img))
    _overlay(ax, gdf, grid, window, label_top=label_top)
    ax.set_title(title or out.stem, fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
    plt.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    return out


def plot_dnbr(delta: np.ndarray, grid: Grid, out: Path, *,
              gdf: gpd.GeoDataFrame | None = None, title: str = "",
              zoom: tuple[float, float, float, float] | None = None,
              vmin: float = -0.3, vmax: float = 1.0) -> Path:
    """Continuous dNBR with a diverging ramp centred on zero."""
    window = _extent_window(grid, zoom)
    arr = delta[window[2]:window[3], window[0]:window[1]] if window else delta

    fig, ax = plt.subplots(figsize=(13, 13 * arr.shape[0] / arr.shape[1]))
    m = ax.imshow(arr, cmap="RdYlGn_r", vmin=vmin, vmax=vmax)
    _overlay(ax, gdf, grid, window, edge="black")
    plt.colorbar(m, ax=ax, fraction=0.040, pad=0.02, label="dNBR")
    ax.set_title(title or out.stem, fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
    plt.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    return out


def plot_severity(classes: np.ndarray, grid: Grid, out: Path, *,
                  gdf: gpd.GeoDataFrame | None = None, title: str = "",
                  zoom: tuple[float, float, float, float] | None = None) -> Path:
    """Discrete Key & Benson severity classes with a legend."""
    window = _extent_window(grid, zoom)
    arr = classes[window[2]:window[3], window[0]:window[1]] if window else classes

    cmap = ListedColormap([CLASS_COLOURS[c] for c in CLASS_LABELS])
    fig, ax = plt.subplots(figsize=(13, 13 * arr.shape[0] / arr.shape[1]))
    ax.imshow(np.ma.masked_less(arr, 0), cmap=cmap, vmin=0, vmax=len(CLASS_LABELS) - 1)
    _overlay(ax, gdf, grid, window, edge="black")
    ax.legend(
        handles=[mpatches.Patch(color=CLASS_COLOURS[c], label=c) for c in CLASS_LABELS],
        loc="upper right", fontsize=7, framealpha=0.9,
    )
    ax.set_title(title or out.stem, fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
    plt.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    return out
