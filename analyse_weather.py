#!/usr/bin/env python
"""Fire weather for the July 2026 Fontainebleau fire, and where it sits in 86 years.

Three questions:

1. How extreme were the conditions, against the full ERA5 record back to 1940?
2. What did the Fire Weather Index say, and did the drought codes have the memory to know?
3. **Did the fire spread the way the wind was blowing?** F6 found terrain, fuel and access
   explain nothing that transfers spatially, and named wind as the missing predictor. There is
   a usable Sentinel-2 scene from 13 July — during the fire — so the ground burned by then can
   be separated from the ground burned afterwards, giving a directed spread vector to test.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from bleau_fire import burn, weather
from bleau_fire.config import DATA, MASK_CLASSES, OUT_DIR, POST_SCENE, PRE_SCENE, build_grid
from bleau_fire.mask import valid_mask
from bleau_fire.scenes import read_scene, scene_path

LAT, LON = 48.38, 2.53
DURING = "S2B_31UDP_20260713_0_L2A"
CACHE = DATA / "weather"
CACHE.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. The long record
# ---------------------------------------------------------------------------
print("[1] hourly record 1940-2026, in decade chunks")
parts = []
for y0 in range(1940, 2030, 10):
    y1 = min(y0 + 9, 2026)
    if y0 > 2026:
        break
    f = CACHE / f"hourly_{y0}_{y1}.parquet"
    if f.exists():
        parts.append(pd.read_parquet(f))
        print(f"  {y0}-{y1}  cached")
        continue
    end = "2026-08-13" if y1 == 2026 else f"{y1}-12-31"
    df = weather.fetch_hourly(
        LAT, LON, start=f"{y0}-01-01", end=end,
        variables=("temperature_2m", "relative_humidity_2m",
                   "wind_speed_10m", "precipitation"),
    )
    df.to_parquet(f)
    parts.append(df)
    print(f"  {y0}-{y1}  {len(df):,} hours")
    time.sleep(12)  # pace the free tier rather than tripping its limiter

hourly = pd.concat(parts).sort_index()
hourly = hourly[~hourly.index.duplicated(keep="first")]
print(f"  total {len(hourly):,} hours, {hourly.index[0].date()} to {hourly.index[-1].date()}")

# ---------------------------------------------------------------------------
# 2. FWI across the whole record
# ---------------------------------------------------------------------------
print("\n[2] Fire Weather Index, 1940-2026")
fwi = weather.fire_weather_index(hourly)
fwi.to_csv(OUT_DIR / "fwi_1940_2026.csv")

fire_day = pd.Timestamp("2026-07-12")
row = fwi.loc[fire_day]
print(f"  12 July 2026: FWI {row['FWI']:.1f} ({weather.fwi_class(row['FWI'])})  "
      f"FFMC {row['FFMC']:.1f}  DMC {row['DMC']:.1f}  DC {row['DC']:.1f}  "
      f"ISI {row['ISI']:.1f}  BUI {row['BUI']:.1f}")
print(f"  conditions at noon LST: {row['temp']:.1f}C, {row['rh']:.0f}% RH, "
      f"{row['wind']:.0f} km/h, {row['rain24']:.1f} mm in 24 h")

# Rank against every day, and against July days only — the fair comparison, since
# ranking a July day against Februaries would flatter it for no reason.
allpct = (fwi["FWI"] < row["FWI"]).mean() * 100
jul = fwi[fwi.index.month == 7]
julpct = (jul["FWI"] < row["FWI"]).mean() * 100
print(f"  percentile vs all {len(fwi):,} days: {allpct:.2f}")
print(f"  percentile vs {len(jul):,} July days: {julpct:.2f}")

top = fwi.nlargest(8, "FWI")[["FWI", "temp", "rh", "wind", "DC"]]
print("\n  highest FWI days on record:")
print(top.to_string())

yr = fwi[fwi.index.month.isin([6, 7, 8])].groupby(fwi[fwi.index.month.isin([6, 7, 8])].index.year)
summer = yr["FWI"].mean().round(2)
print(f"\n  summer (JJA) mean FWI — 2026: {summer.get(2026, float('nan')):.2f}, "
      f"1940-2025 mean {summer.loc[:2025].mean():.2f}, "
      f"rank {int((summer.loc[:2025] < summer.get(2026, -1)).sum()) + 1} of {len(summer)}")

# ---------------------------------------------------------------------------
# 3. Wind during the fire
# ---------------------------------------------------------------------------
print("\n[3] wind during the fire")
w2026 = weather.fetch_hourly(LAT, LON, start="2026-07-10", end="2026-07-22")
runs = {
    "12 Jul 10:00-20:00 (initial run)": ("2026-07-12 10:00", "2026-07-12 20:00"),
    "12-13 Jul (first 48 h)": ("2026-07-12 00:00", "2026-07-13 23:00"),
    "13-20 Jul (after the during-scene)": ("2026-07-13 11:00", "2026-07-20 11:00"),
}
wind_summary = {}
for label, (a, b) in runs.items():
    seg = w2026.loc[a:b]
    bearing, spd = weather.wind_vector_mean(seg)
    toward = (bearing + 180) % 360
    wind_summary[label] = {"from_deg": round(bearing, 1), "toward_deg": round(toward, 1),
                           "speed_kmh": round(spd, 1),
                           "max_gust": round(float(seg["wind_gusts_10m"].max()), 1)}
    print(f"  {label:<38} from {bearing:5.1f}deg -> blowing toward {toward:5.1f}deg, "
          f"{spd:4.1f} km/h, gusts to {seg['wind_gusts_10m'].max():.0f}")

# ---------------------------------------------------------------------------
# 4. Did it spread downwind?
# ---------------------------------------------------------------------------
print("\n[4] spread direction, 13 July -> 20 July")
grid = build_grid()
pre, _ = read_scene(scene_path(PRE_SCENE))
post, _ = read_scene(scene_path(POST_SCENE))
nbr_pre = burn.nbr(pre["nir"], pre["swir22"])
ok_post = valid_mask(pre["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
    post["scl"].astype("uint8"), MASK_CLASSES)
d_post = burn.dnbr(nbr_pre, burn.nbr(post["nir"], post["swir22"]))

spread = None
if scene_path(DURING).exists():
    dur, dur_meta = read_scene(scene_path(DURING))
    ok_dur = valid_mask(pre["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
        dur["scl"].astype("uint8"), MASK_CLASSES)
    d_dur = burn.dnbr(nbr_pre, burn.nbr(dur["nir"], dur["swir22"]))

    early = burn.burned_mask(d_dur) & ok_dur          # burned by 13 July
    total = burn.burned_mask(d_post) & ok_post        # burned by 20 July
    late = total & ~early                              # burned after the 13th

    px = grid.resolution ** 2 / 1e4
    print(f"  burned by 13 Jul: {early.sum() * px:,.0f} ha")
    print(f"  burned by 20 Jul: {total.sum() * px:,.0f} ha")
    print(f"  added after 13 Jul: {late.sum() * px:,.0f} ha "
          f"({100 * late.sum() / max(total.sum(), 1):.1f}% of the final scar)")

    # ⚠️ Per sector, not massif-wide. This fire had TWO separate sectors ~8 km apart
    # (Noisy-sur-Ecole and the Faisanderie). A single centroid over both measures the balance
    # between two independent fires, not the spread of either: if one sector grows later, the
    # combined centroid lurches toward it and reads as an 8 km "spread". The first version of
    # this analysis reported exactly that — a 3.8 km shift toward 080deg — which is an artefact
    # of pooling, not a measurement of anything.
    from scipy import ndimage

    lab, n = ndimage.label(ndimage.binary_closing(total, structure=np.ones((9, 9))))
    sizes = ndimage.sum(total, lab, range(1, n + 1))
    sectors = [i + 1 for i, s in enumerate(sizes) if s * px >= 100]  # >= 100 ha
    print(f"  {len(sectors)} distinct sectors >= 100 ha (of {n} components)")

    # The fire was reported contained by the evening of 14 July, so the wind that matters for
    # growth after the 13th is 13-15 July, not the whole week to the 20th.
    active = w2026.loc["2026-07-13 11:00":"2026-07-15 00:00"]
    wb, wspd = weather.wind_vector_mean(active)
    wind_toward = (wb + 180) % 360
    print(f"  wind 13-15 Jul: from {wb:.1f}deg -> toward {wind_toward:.1f}deg, {wspd:.1f} km/h")

    def centroid(m):
        r, c = np.nonzero(m)
        return r.mean(), c.mean()

    per_sector = []
    for si in sorted(sectors, key=lambda i: -sizes[i - 1]):
        m = lab == si
        e, l = early & m, late & m
        if e.sum() < 200 or l.sum() < 200:
            print(f"  sector {si}: {m.sum() * px:,.0f} ha — too little early or late area to test")
            continue
        r0, c0 = centroid(e)
        r1, c1 = centroid(l)
        dr, dc = r1 - r0, c1 - c0
        # Image rows increase southward, so northward displacement is -(dr).
        bearing = float((np.degrees(np.arctan2(dc, -dr)) + 360) % 360)
        dist = float(np.hypot(dr, dc) * grid.resolution)
        diff = float(abs((bearing - wind_toward + 180) % 360 - 180))
        print(f"  sector {si}: {m.sum() * px:,.0f} ha  ({e.sum() * px:,.0f} by 13 Jul, "
              f"{l.sum() * px:,.0f} after)  spread {dist:,.0f} m toward {bearing:.1f}deg  "
              f"| wind toward {wind_toward:.1f}deg  | diff {diff:.1f}deg")
        per_sector.append({"sector": int(si), "ha": round(float(m.sum()) * px, 1),
                           "ha_early": round(float(e.sum()) * px, 1),
                           "ha_late": round(float(l.sum()) * px, 1),
                           "spread_bearing": round(bearing, 1),
                           "spread_distance_m": round(dist, 1),
                           "angular_difference": round(diff, 1)})

    spread = {"ha_by_13": round(float(early.sum()) * px, 1),
              "ha_by_20": round(float(total.sum()) * px, 1),
              "ha_added": round(float(late.sum()) * px, 1),
              "wind_toward_13_15": round(float(wind_toward), 1),
              "wind_speed_13_15": round(float(wspd), 1),
              "sectors": per_sector}
else:
    print(f"  {DURING} not fetched yet — skipping")

OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "weather_summary.json").write_text(json.dumps({
    "fire_day": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                 for k, v in row.to_dict().items()},
    "fwi_class": weather.fwi_class(row["FWI"]),
    "percentile_all_days": round(float(allpct), 3),
    "percentile_july_days": round(float(julpct), 3),
    "record_days": int(len(fwi)),
    "top_fwi": top.reset_index().astype(str).to_dict("records"),
    "wind": wind_summary,
    "spread": spread,
}, indent=2))
print(f"\n[out] {OUT_DIR / 'weather_summary.json'}")
