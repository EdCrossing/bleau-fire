"""Burn severity indices.

    NBR   = (NIR - SWIR2) / (NIR + SWIR2)          per scene
    dNBR  = NBR_pre - NBR_post                      the difference; positive means burned
    RdNBR = dNBR / sqrt(|NBR_pre|)                  relativised (Miller & Thode 2007)

Why NBR works: healthy vegetation is bright in the NIR (B08) and dark in the SWIR (B12), so
NBR is high. Fire strips foliage and leaves char and exposed soil, which drops NIR and raises
SWIR, so NBR falls sharply. Differencing pre against post isolates that change from whatever
the surface was doing anyway.

**Why RdNBR is computed too.** dNBR is an *absolute* difference, so the textbook argument is
that a given fire intensity yields a larger dNBR over dense canopy than over sparse fuel purely
because there was more to lose, and that RdNBR — normalising by pre-fire condition — should
compress that gap.

⚠️ **Measured against BD Forêt fuel labels, that argument came out backwards here.** Heath
records the *highest* dNBR in the massif (median 0.486) and closed conifer the lowest (0.319),
and RdNBR widens the gap rather than closing it (0.917 vs 0.448). Heath burns to bare sand, and
exposed sandstone is bright in SWIR, so the index is partly measuring the *substrate revealed*
rather than the fire intensity. See `FINDINGS.md` F5 before treating either index as a
severity measurement across fuel types.

⚠️ **The thresholds below are not a law of nature.** They are Key & Benson's (2006) FIREMON
classes, calibrated largely on North American conifer forest. Applying them unmodified to a
French mixed pine/oak/heath massif on sandstone is an *assumption*, and this project has an
unusually good way to test it: Copernicus EMS activation EMSR894 published an independent
grading of this exact fire. Validate against it; do not assert.
"""

from __future__ import annotations

import numpy as np

# (lower_bound, label). Key & Benson (2006), FIREMON landscape assessment.
SEVERITY_CLASSES: tuple[tuple[float, str], ...] = (
    (-np.inf, "high regrowth"),
    (-0.25, "low regrowth"),
    (-0.10, "unburned"),
    (0.10, "low severity"),
    (0.27, "moderate-low severity"),
    (0.44, "moderate-high severity"),
    (0.66, "high severity"),
)

CLASS_LABELS: tuple[str, ...] = tuple(label for _, label in SEVERITY_CLASSES)

# Colours for rendering, roughly the USGS burn-severity convention.
CLASS_COLOURS: dict[str, str] = {
    "high regrowth": "#1a9850",
    "low regrowth": "#91cf60",
    "unburned": "#d9d9d9",
    "low severity": "#fee08b",
    "moderate-low severity": "#fc8d59",
    "moderate-high severity": "#e34a33",
    "high severity": "#7f0000",
}


def nbr(nir: np.ndarray, swir22: np.ndarray) -> np.ndarray:
    """Normalised Burn Ratio. NaN where the denominator vanishes rather than inf."""
    denom = nir + swir22
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (nir - swir22) / denom
    return np.where(np.abs(denom) < 1e-6, np.nan, out).astype("float32")


def dnbr(nbr_pre: np.ndarray, nbr_post: np.ndarray) -> np.ndarray:
    """Differenced NBR. Positive means the surface lost vegetation between the two dates."""
    return (nbr_pre - nbr_post).astype("float32")


def rdnbr(nbr_pre: np.ndarray, delta: np.ndarray, floor: float = 0.001) -> np.ndarray:
    """Relativised dNBR (Miller & Thode 2007), normalised by pre-fire condition.

    The `floor` guards the denominator where pre-fire NBR is near zero — bare sandstone and
    water, which have no vegetation to lose and would otherwise produce enormous meaningless
    ratios. Those pixels are better read as "not applicable" than as extreme severity.
    """
    denom = np.sqrt(np.maximum(np.abs(nbr_pre), floor))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (delta / denom).astype("float32")


def classify(delta: np.ndarray) -> np.ndarray:
    """Map dNBR to severity class indices. NaN input maps to -1 (no data)."""
    bounds = np.array([b for b, _ in SEVERITY_CLASSES[1:]], dtype="float32")
    out = np.digitize(np.nan_to_num(delta, nan=-999.0), bounds).astype("int8")
    return np.where(np.isnan(delta), -1, out).astype("int8")


def class_areas(classes: np.ndarray, pixel_area_m2: float) -> dict[str, float]:
    """Hectares in each severity class. The number that gets compared against EMS's 2,000 ha."""
    return {
        label: round(float((classes == i).sum()) * pixel_area_m2 / 10_000.0, 1)
        for i, label in enumerate(CLASS_LABELS)
    }


def burned_mask(delta: np.ndarray, threshold: float = 0.27) -> np.ndarray:
    """Boolean burn mask at the moderate-low boundary.

    0.27 rather than 0.10 deliberately: the low-severity band is where dNBR is least
    separable from ordinary phenological change and co-registration noise, so including it
    inflates the burned area with the least defensible pixels.
    """
    return np.nan_to_num(delta, nan=0.0) >= threshold
