"""Cloud, shadow and nodata masking from the Sentinel-2 Scene Classification Layer.

SCL class table (L2A, 20 m):

    0  no data                     <- always mask
    1  saturated or defective      <- always mask
    2  dark area / topographic shadow   (debatable: real ground, unreliable reflectance)
    3  cloud shadow                <- mask
    4  vegetation
    5  not vegetated
    6  water
    7  unclassified                (debatable: often thin cloud edges)
    8  cloud, medium probability   <- mask
    9  cloud, high probability     <- mask
    10 thin cirrus                 <- mask
    11 snow / ice

**SCL is mediocre and you should expect it to be.** It over-masks bright surfaces and
under-detects thin cirrus and cloud edges.

⚠️ **A burn-scar-specific hazard.** Fresh burn scars are dark in the visible and very dark in
the NIR — the same signature SCL uses for cloud shadow (class 3). So SCL may mask the burn scar
itself, which would silently delete the very pixels this project exists to measure. Related
work in eo-agent (`FINDINGS.md` F1) already measured SCL assigning cloud shadow to 7-9% of
vegetation observations against 0.55% over water, a ~15x difference, cause unresolved.

`scar_shadow_overlap()` below exists to quantify that here rather than assume it away.
"""

from __future__ import annotations

import numpy as np

SCL_NAMES = {
    0: "nodata",
    1: "saturated/defective",
    2: "dark/shadow",
    3: "cloud shadow",
    4: "vegetation",
    5: "not vegetated",
    6: "water",
    7: "unclassified",
    8: "cloud medium prob",
    9: "cloud high prob",
    10: "thin cirrus",
    11: "snow/ice",
}


def valid_mask(scl: np.ndarray, mask_classes: tuple[int, ...]) -> np.ndarray:
    """Return a boolean array: True where the pixel is usable."""
    return ~np.isin(scl, np.asarray(mask_classes, dtype=scl.dtype))


def scl_histogram(scl: np.ndarray) -> dict[str, float]:
    """Percentage of AOI pixels in each SCL class. Useful for seeing why a scene died."""
    total = scl.size
    out: dict[str, float] = {}
    for code, name in SCL_NAMES.items():
        pct = float((scl == code).sum()) / total * 100.0
        if pct > 0.01:
            out[f"{code}:{name}"] = round(pct, 2)
    return out


def scar_shadow_overlap(scl_post: np.ndarray, burned: np.ndarray) -> dict[str, float]:
    """How much of the burn scar did SCL call cloud shadow?

    `burned` is a boolean burn mask derived from dNBR. If a large share of it lands in SCL
    class 3 on a scene with almost no cloud, that is SCL mistaking char for shade, not a
    genuine detection — and any masked composite over this fire would be eroding its own
    signal. Reported, not silently corrected.
    """
    if burned.sum() == 0:
        return {}
    inside = scl_post[burned]
    return {
        SCL_NAMES.get(c, str(c)): round(float((inside == c).sum()) / inside.size * 100.0, 2)
        for c in np.unique(inside)
    }
