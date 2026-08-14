#!/usr/bin/env python
"""Was the smoke in the boundary layer, or lofted above it?

F8 detected the plume: UV Aerosol Index +2.31, 18 km west of the fire, matching the wind to
25 degrees. But CAMS *surface* PM2.5 barely moved — the whole ring rose about 1 ug/m3, which is
heatwave stagnation rather than smoke.

Both can be true if the plume was **above the boundary layer**. Smoke injected above the mixing
height is decoupled from the surface: it shows up strongly in a column-integrated satellite
measurement while contributing almost nothing to what people breathe, and it travels much
further because there is no dry deposition and little turbulent mixing to remove it.

The test compares two independent things at the same place and hour:

    TROPOMI L2__AER_LH aerosol_mid_height   the retrieved altitude of the smoke layer
    ERA5 boundary_layer_height              the depth of the mixed layer

⚠️ **Layer height is only meaningful where there is a layer.** The retrieval assumes a single
scattering layer and will happily fit one to clear air. Restricted here to pixels that are both
quality-flagged good *and* independently confirmed as smoke by the Aerosol Index.

⚠️ **The retrieval is known to be biased low over bright surfaces and thin plumes**, so a result
near the boundary-layer top should be read as ambiguous, not as a clean separation.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from bleau_fire import s5p, weather
from bleau_fire.config import OUT_DIR

FIRE_LAT, FIRE_LON = 48.38, 2.53
REGION = (0.3, 47.2, 4.6, 49.7)
DAYS = ["2026-07-09", "2026-07-13", "2026-07-14"]
AI_SMOKE = 1.0        # Aerosol Index above this is unambiguous absorbing aerosol
QA_MIN = 0.5          # ESA's recommended minimum for AER_LH

print("[ERA5] boundary layer height at the fire")
met = weather.fetch_hourly(FIRE_LAT, FIRE_LON, start="2026-07-08", end="2026-07-15",
                           variables=("boundary_layer_height", "temperature_2m",
                                      "wind_speed_10m", "wind_direction_10m"))
for d in DAYS:
    sl = met.loc[f"{d} 10:00":f"{d} 14:00", "boundary_layer_height"]
    print(f"  {d}  BLH 10-14 UTC: {sl.min():.0f}-{sl.max():.0f} m "
          f"(12:00 = {met.loc[f'{d} 12:00', 'boundary_layer_height']:.0f} m)")

results = {}
for date in DAYS:
    print(f"\n[{date}]")
    ai, tf, ai_key = s5p.sample_day("aer_ai", date, REGION, hour_utc=12.0)
    lh, _, lh_key = s5p.sample_day("aer_lh", date, REGION, hour_utc=12.0)
    if ai is None or lh is None:
        print("  missing product")
        continue
    qa, _ = s5p.read_bbox(s5p.qa_key_for(lh_key), REGION, width=400)

    good = np.isfinite(lh) & np.isfinite(qa) & (qa >= QA_MIN)
    smoke = good & np.isfinite(ai) & (ai >= AI_SMOKE)
    blh = float(met.loc[f"{date} 12:00", "boundary_layer_height"])

    print(f"  qa>={QA_MIN}: {good.sum():,} px · of which AI>={AI_SMOKE}: {smoke.sum():,} px")
    print(f"  ERA5 boundary layer height at 12:00 UTC: {blh:,.0f} m")

    row = {"blh_m": round(blh, 1), "n_good": int(good.sum()), "n_smoke": int(smoke.sum())}
    if smoke.sum() >= 5:
        h = lh[smoke]
        above = float((h > blh).mean()) * 100
        print(f"  smoke-layer height: median {np.median(h):,.0f} m  "
              f"p25-p75 {np.percentile(h, 25):,.0f}-{np.percentile(h, 75):,.0f} m  "
              f"max {h.max():,.0f} m")
        print(f"  fraction of smoke pixels ABOVE the boundary layer: {above:.0f}%")
        # Where is the smoke, relative to the fire?
        rr, cc = np.nonzero(smoke)
        lons = tf.c + (cc + 0.5) * tf.a
        lats = tf.f + (rr + 0.5) * tf.e
        dist = np.hypot((lons - FIRE_LON) * 74.0, (lats - FIRE_LAT) * 111.0)
        brg = (np.degrees(np.arctan2((lons - FIRE_LON) * 74.0,
                                     (lats - FIRE_LAT) * 111.0)) + 360) % 360
        near = dist <= 120
        if near.any():
            bs, ss = weather.wind_vector_mean(
                pd.DataFrame({"wind_speed_10m": np.ones(near.sum()),
                              "wind_direction_10m": brg[near]}))
            print(f"  smoke within 120 km: {near.sum()} px, "
                  f"mean bearing from fire {bs:.0f}deg, mean distance {dist[near].mean():.0f} km")
            row |= {"smoke_px_within_120km": int(near.sum()),
                    "mean_bearing": round(float(bs), 1),
                    "mean_distance_km": round(float(dist[near].mean()), 1)}
        row |= {"height_median_m": round(float(np.median(h)), 1),
                "height_p25_m": round(float(np.percentile(h, 25)), 1),
                "height_p75_m": round(float(np.percentile(h, 75)), 1),
                "height_max_m": round(float(h.max()), 1),
                "pct_above_blh": round(above, 1)}
    else:
        print("  too few confirmed-smoke pixels to characterise a height")
    results[date] = row

OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "plume_height.json").write_text(json.dumps(results, indent=2))
print(f"\n[out] {OUT_DIR / 'plume_height.json'}")
