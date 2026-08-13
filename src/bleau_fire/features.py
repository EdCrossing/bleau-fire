"""Sample burn severity at each climbing feature.

This is where the project's actual question gets answered: not "how many hectares burned",
which already exists, but *which named boulders and circuits burned, and how badly*.

**Sampling is windowed, not point-in-pixel, and that is a correctness decision rather than a
smoothing preference.** OSM climbing positions are contributed by climbers with phone GPS, not
surveyed. Ten to twenty metres of error is normal, which is one to two pixels at 10 m. A single
pixel lookup would therefore assign a confident severity class to a rock that may genuinely sit
in the neighbouring pixel — and at a burn edge those two pixels can differ by the entire range
of the scale.

So each feature is sampled over a window and reported with:

  * `dnbr_median`  — the robust central value, used for classification
  * `dnbr_p25/p75` — the plausible range given position error, robust to single odd pixels
  * `dnbr_iqr`     — how heterogeneous the surface is inside the window
  * `edge`         — True when the p25-p75 range straddles a class boundary, i.e. the
                     classification is not resolvable and should not be stated flatly

The `edge` flag is the honest output. A feature 400 m inside the scar and a feature on its
boundary are both "burned", and only one of them is actually known to be.

⚠️ **This flag was originally computed from the window min and max, and that was wrong.** A
real burn scar is patchy at 10 m — unburned islands, variable fuel, crown versus surface burn —
so across 25 pixels the min and max nearly always straddle a class boundary. The flag fired on
100% of burned features while sitting near 0% on unburned ones, which made it a burn detector
rather than an uncertainty measure. Percentiles separate the two: `edge` now reflects
positional ambiguity, and `dnbr_iqr` carries the heterogeneity that was being conflated with it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .burn import CLASS_LABELS, SEVERITY_CLASSES, classify
from .config import Grid
from .render import to_pixels


def sample(
    gdf,
    delta: np.ndarray,
    grid: Grid,
    *,
    radius: int = 2,
    valid: np.ndarray | None = None,
) -> pd.DataFrame:
    """Sample dNBR around each feature. `radius` in pixels (2 -> a 50 x 50 m window at 10 m)."""
    cols, rows = to_pixels(gdf, grid)
    h, w = delta.shape

    records = []
    for i, (c, r) in enumerate(zip(cols, rows)):
        ci, ri = int(round(c)), int(round(r))
        if not (0 <= ci < w and 0 <= ri < h):
            records.append({"in_aoi": False})
            continue

        r0, r1 = max(0, ri - radius), min(h, ri + radius + 1)
        c0, c1 = max(0, ci - radius), min(w, ci + radius + 1)
        win = delta[r0:r1, c0:c1]
        if valid is not None:
            vwin = valid[r0:r1, c0:c1]
            win = win[vwin] if vwin.any() else win.ravel()
        win = win[np.isfinite(win)]
        if win.size == 0:
            records.append({"in_aoi": True})
            continue

        med = float(np.median(win))
        p25, p75 = (float(v) for v in np.percentile(win, [25, 75]))
        cls_lo = int(classify(np.array([p25]))[0])
        cls_hi = int(classify(np.array([p75]))[0])
        records.append({
            "in_aoi": True,
            "dnbr_median": round(med, 4),
            "dnbr_p25": round(p25, 4),
            "dnbr_p75": round(p75, 4),
            "dnbr_iqr": round(p75 - p25, 4),
            "dnbr_min": round(float(win.min()), 4),
            "dnbr_max": round(float(win.max()), 4),
            "severity": CLASS_LABELS[int(classify(np.array([med]))[0])],
            "edge": cls_lo != cls_hi,
            "n_px": int(win.size),
        })

    out = gdf.copy().reset_index(drop=True)
    return pd.concat([out, pd.DataFrame(records)], axis=1)


def summarise(df: pd.DataFrame) -> str:
    """Human-readable rollup, kept separate from the data so the CSV stays clean."""
    lines = []
    burned = df[df["severity"].isin(
        [c for c in CLASS_LABELS if "severity" in c and c != "low severity"]
    )]
    lines.append(f"features sampled: {int(df['in_aoi'].sum())} of {len(df)} inside the AOI")
    lines.append(f"unresolvable at the burn edge: {int(df['edge'].fillna(False).sum())}")

    lines.append("\nby severity class (all feature types):")
    counts = df["severity"].value_counts()
    for label in CLASS_LABELS:
        if label in counts:
            lines.append(f"  {label:<26} {counts[label]:>5}")

    for ftype in ("crag", "boulder", "route_bottom"):
        sub = df[df["feature_type"] == ftype]
        if not len(sub):
            continue
        hit = sub[sub["dnbr_median"] >= 0.27]
        lines.append(f"\n{ftype}: {len(hit)}/{len(sub)} at moderate-low severity or worse")

    named = burned[burned["name"].notna()].sort_values("dnbr_median", ascending=False)
    if len(named):
        lines.append("\nmost severely burned named features "
                     "(iqr = surface heterogeneity in the window):")
        for _, r in named.head(30).iterrows():
            flag = "  ~uncertain" if r["edge"] else ""
            lines.append(f"  {r['dnbr_median']:5.2f}  iqr={r['dnbr_iqr']:.2f}  "
                         f"{r['severity']:<24} {str(r['name'])[:38]}{flag}")
    return "\n".join(lines)


def circuit_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate problems by circuit — the unit a climber actually plans around.

    A circuit is a numbered sequence of problems following a colour. Reporting "the red circuit
    at 95.2 is 80% inside the scar" is far more useful than 40 separate rows, and it is the
    granularity at which OSM's data is genuinely reliable.
    """
    sub = df[df["climbing:circuit:colour"].notna() & df["dnbr_median"].notna()]
    if not len(sub):
        return pd.DataFrame()
    g = sub.groupby("climbing:circuit:colour").agg(
        problems=("dnbr_median", "size"),
        dnbr_median=("dnbr_median", "median"),
        dnbr_max=("dnbr_median", "max"),
        pct_burned=("dnbr_median", lambda s: round(100.0 * (s >= 0.27).mean(), 1)),
        n_edge=("edge", "sum"),
    )
    return g.sort_values("pct_burned", ascending=False)
