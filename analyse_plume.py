#!/usr/bin/env python
"""Did the fire leave a signal in the atmosphere?

Two independent lines, neither of which touches the Sentinel-2 imagery the rest of the project
rests on:

  * **Sentinel-5P / TROPOMI** — UV Aerosol Index and CO total column over northern France on
    the fire days, against a pre-fire baseline.
  * **CAMS European air quality** (via Open-Meteo, no key) — surface PM2.5 and CO sampled on a
    ring around the fire, to test whether any enhancement sits **downwind** rather than
    everywhere. That is an independent check on transport direction that, unlike the burn-scar
    geometry in F7, cannot be confounded by firefighting.

Set up to be readable as a null result. A ~2,000 ha fire is about one TROPOMI pixel, so the
honest prior is that this is at or below the detection limit.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import requests

from bleau_fire import s5p
from bleau_fire.config import OUT_DIR

FIRE_LAT, FIRE_LON = 48.38, 2.53
# Wide enough to hold a plume advected ~100 km west-south-west.
REGION = (0.3, 47.2, 4.6, 49.7)
BASELINE = ["2026-07-08", "2026-07-09", "2026-07-10"]
FIRE_DAYS = ["2026-07-12", "2026-07-13", "2026-07-14"]

OUT_DIR.mkdir(parents=True, exist_ok=True)
results = {}

# ---------------------------------------------------------------------------
# 1. TROPOMI
# ---------------------------------------------------------------------------
for product, label, unit in (("aer_ai", "UV Aerosol Index", ""),
                             ("co", "CO total column", " mol/m2")):
    print(f"\n[TROPOMI] {label}")
    fields, meta = {}, {}
    for date in BASELINE + FIRE_DAYS:
        try:
            arr, transform, key = s5p.sample_day(product, date, REGION, hour_utc=12.0)
        except Exception as exc:
            print(f"  {date}: {type(exc).__name__}: {exc}")
            continue
        if arr is None:
            print(f"  {date}: no orbit found")
            continue
        cov = float(np.isfinite(arr).mean()) * 100
        fields[date] = arr
        meta[date] = {"key": key.split("/")[-1][:60], "coverage_pct": round(cov, 1)}
        print(f"  {date}  coverage {cov:5.1f}%  median {np.nanmedian(arr):+.4g}"
              f"  p99 {np.nanpercentile(arr, 99):+.4g}{unit}")

    base = [fields[d] for d in BASELINE if d in fields]
    if not base or not any(d in fields for d in FIRE_DAYS):
        print("  insufficient data for an anomaly")
        continue
    ref = np.nanmedian(np.stack(base), axis=0)

    prod_res = {"meta": meta, "anomaly": {}}
    for d in FIRE_DAYS:
        if d not in fields:
            continue
        anom = fields[d] - ref
        # Where is the strongest enhancement, and is it near the fire?
        flat = np.where(np.isfinite(anom), anom, -np.inf)
        idx = np.unravel_index(np.argmax(flat), flat.shape)
        lon = transform.c + (idx[1] + 0.5) * transform.a
        lat = transform.f + (idx[0] + 0.5) * transform.e
        dist = np.hypot((lon - FIRE_LON) * 74.0, (lat - FIRE_LAT) * 111.0)
        bearing = (np.degrees(np.arctan2((lon - FIRE_LON) * 74.0,
                                         (lat - FIRE_LAT) * 111.0)) + 360) % 360
        print(f"  {d}  anomaly median {np.nanmedian(anom):+.4g}  max {np.nanmax(anom):+.4g}"
              f"  at {lat:.2f}N {lon:.2f}E — {dist:.0f} km from the fire, bearing {bearing:.0f}deg")
        prod_res["anomaly"][d] = {
            "median": round(float(np.nanmedian(anom)), 6),
            "max": round(float(np.nanmax(anom)), 6),
            "max_lat": round(float(lat), 3), "max_lon": round(float(lon), 3),
            "km_from_fire": round(float(dist), 1), "bearing": round(float(bearing), 1),
        }
    results[product] = prod_res

# ---------------------------------------------------------------------------
# 2. CAMS surface, on a ring around the fire
# ---------------------------------------------------------------------------
print("\n[CAMS] surface PM2.5 and CO on a ring around the fire")
RADII_KM = (25, 60)
BEARINGS = range(0, 360, 45)
pts = [(FIRE_LAT, FIRE_LON, 0, -1)]
for rad in RADII_KM:
    for b in BEARINGS:
        rl = np.radians(b)
        pts.append((FIRE_LAT + rad * np.cos(rl) / 111.0,
                    FIRE_LON + rad * np.sin(rl) / 74.0, rad, b))

r = requests.get(
    "https://air-quality-api.open-meteo.com/v1/air-quality",
    params={"latitude": ",".join(f"{p[0]:.4f}" for p in pts),
            "longitude": ",".join(f"{p[1]:.4f}" for p in pts),
            "start_date": "2026-07-06", "end_date": "2026-07-16",
            "hourly": "pm2_5,carbon_monoxide", "domains": "cams_europe", "timezone": "UTC"},
    headers={"User-Agent": "bleau-fire/0.1"}, timeout=300,
)
r.raise_for_status()
payload = r.json()
if isinstance(payload, dict):
    payload = [payload]

rows = []
for (lat, lon, rad, brg), blk in zip(pts, payload):
    h = blk["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    pre = df.loc["2026-07-06":"2026-07-11"]
    fire = df.loc["2026-07-12":"2026-07-14"]
    rows.append({
        "radius_km": rad, "bearing": brg, "lat": round(lat, 3), "lon": round(lon, 3),
        "pm25_pre": round(float(pre["pm2_5"].mean()), 2),
        "pm25_fire": round(float(fire["pm2_5"].mean()), 2),
        "pm25_delta": round(float(fire["pm2_5"].mean() - pre["pm2_5"].mean()), 2),
        "pm25_peak": round(float(fire["pm2_5"].max()), 2),
        "co_delta": round(float(fire["carbon_monoxide"].mean()
                                - pre["carbon_monoxide"].mean()), 1),
    })

ring = pd.DataFrame(rows)
print(ring.to_string(index=False))

# Wind blew toward 239deg on the initial run. If the fire drove surface concentrations,
# the enhancement should be largest near that bearing, not uniform.
downwind = ring[(ring.bearing.isin([225, 270])) & (ring.radius_km > 0)]["pm25_delta"].mean()
upwind = ring[(ring.bearing.isin([45, 90])) & (ring.radius_km > 0)]["pm25_delta"].mean()
allring = ring[ring.radius_km > 0]["pm25_delta"].mean()
print(f"\n  mean PM2.5 change, downwind sectors (225/270deg): {downwind:+.2f} ug/m3")
print(f"  mean PM2.5 change, upwind sectors (45/90deg):     {upwind:+.2f} ug/m3")
print(f"  mean PM2.5 change, whole ring:                    {allring:+.2f} ug/m3")
print(f"  downwind minus upwind: {downwind - upwind:+.2f} ug/m3")

ring.to_csv(OUT_DIR / "plume_ring.csv", index=False)
results["cams_ring"] = {
    "downwind_delta": round(float(downwind), 3),
    "upwind_delta": round(float(upwind), 3),
    "ring_mean_delta": round(float(allring), 3),
    "contrast": round(float(downwind - upwind), 3),
}
(OUT_DIR / "plume_summary.json").write_text(json.dumps(results, indent=2))
print(f"\n[out] {OUT_DIR / 'plume_summary.json'}")
