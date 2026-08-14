"""Weather, and the Canadian Fire Weather Index.

Two sources, deliberately:

**Open-Meteo's archive API** — ERA5-derived hourly surface fields, no key, no registration.
Temperature, humidity, wind speed and direction, gusts, precipitation. Enough for fire weather
and immediately reproducible by anyone reading the repo.

**ARCO-ERA5 on Google Cloud** (`gcp-public-data-arco-era5`) — the full reanalysis as zarr,
hourly from 1940, anonymous via `storage_options={"token": "anon"}`. Reach for this when the
question needs vertical structure, a long climatology, or fields Open-Meteo does not expose.
See `era5_dataset()`.

---

**The Fire Weather Index.** F6 found that terrain, fuel and access explain nothing about severity
that transfers spatially, and named the obvious suspect: *weather on the day is absent from the
predictors.* FWI is the standard way to put it back.

The Canadian Forest Fire Weather Index System (Van Wagner 1987) is what EFFIS runs for Europe.
It is not a physical model — it is a set of empirical moisture bookkeeping equations calibrated
on Canadian pine, and it works because fuel moisture is mostly a memory process. Six components:

    FFMC  fine fuel moisture   — hours of memory. Litter, needles. Drives ignition.
    DMC   duff moisture        — weeks.  Loosely organic layers.
    DC    drought code         — months. Deep compact organic matter. The seasonal signal.
    ISI   initial spread index — FFMC + wind. Expected rate of spread.
    BUI   buildup index        — DMC + DC. Total fuel available to burn.
    FWI   fire weather index   — ISI + BUI. The headline number.

⚠️ **DC and DMC have months of memory, so the calculation must be spun up.** Starting it on the
day of the fire yields the default startup values, not the actual drought state, and would
understate a dry summer badly. Start in spring and let it run.

⚠️ **Inputs are noon local standard time**, not local clock time. France keeps UTC+1 standard,
so noon LST is **11:00 UTC** — not 12:00, and not 13:00 CEST.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HEADERS = {"User-Agent": "bleau-fire/0.1 (Fontainebleau burn severity)"}

HOURLY_VARS = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "precipitation", "surface_pressure", "shortwave_radiation",
    "soil_moisture_0_to_7cm", "vapour_pressure_deficit",
)

# Noon local standard time for metropolitan France (UTC+1 standard, ignoring DST).
NOON_LST_UTC = 11


def fetch_hourly(
    lat: float = 48.38, lon: float = 2.53, *,
    start: str = "2026-04-01", end: str = "2026-08-13",
    variables: tuple[str, ...] = HOURLY_VARS,
) -> pd.DataFrame:
    """Hourly surface weather for a point, indexed by UTC timestamp."""
    params = {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
              "hourly": ",".join(variables), "timezone": "UTC"}
    # Open-Meteo's free tier rate-limits by request volume, and a decade of hourly data is a
    # large single request. Back off rather than failing a long chunked pull half way through.
    for attempt in range(6):
        r = requests.get(ARCHIVE, params=params, headers=HEADERS, timeout=300)
        if r.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"    [rate-limited] waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError("Open-Meteo kept rate-limiting after 6 attempts")
    h = r.json()["hourly"]
    df = pd.DataFrame(h)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


# ---------------------------------------------------------------------------
# Canadian FWI System — Van Wagner (1987), equations as published.
# ---------------------------------------------------------------------------

def _ffmc(temp, rh, wind, rain, ffmc_prev):
    """Fine Fuel Moisture Code. `wind` in km/h, `rain` in mm over 24 h."""
    mo = 147.2 * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0.5:
        rf = rain - 0.5
        mo += (
            42.5 * rf * np.exp(-100.0 / (251.0 - mo)) * (1.0 - np.exp(-6.93 / rf))
            + (0.0015 * (mo - 150.0) ** 2 * np.sqrt(rf) if mo > 150.0 else 0.0)
        )
        mo = min(mo, 250.0)

    ed = (0.942 * rh ** 0.679 + 11.0 * np.exp((rh - 100.0) / 10.0)
          + 0.18 * (21.1 - temp) * (1.0 - np.exp(-0.115 * rh)))
    if mo > ed:
        ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * np.sqrt(wind) * (
            1.0 - (rh / 100.0) ** 8)
        m = ed + (mo - ed) * 10.0 ** (-ko * 0.581 * np.exp(0.0365 * temp))
    else:
        ew = (0.618 * rh ** 0.753 + 10.0 * np.exp((rh - 100.0) / 10.0)
              + 0.18 * (21.1 - temp) * (1.0 - np.exp(-0.115 * rh)))
        if mo < ew:
            kl = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * np.sqrt(wind) * (
                1.0 - ((100.0 - rh) / 100.0) ** 8)
            m = ew - (ew - mo) * 10.0 ** (-kl * 0.581 * np.exp(0.0365 * temp))
        else:
            m = mo
    return 59.5 * (250.0 - m) / (147.2 + m)


# Effective day length factors: DMC by month, DC by month. Northern hemisphere.
_LE = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]
_LF = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]


def _dmc(temp, rh, rain, dmc_prev, month):
    if rain > 1.5:
        re = 0.92 * rain - 1.27
        mo = 20.0 + np.exp(5.6348 - dmc_prev / 43.43)
        if dmc_prev <= 33.0:
            b = 100.0 / (0.5 + 0.3 * dmc_prev)
        elif dmc_prev <= 65.0:
            b = 14.0 - 1.3 * np.log(dmc_prev)
        else:
            b = 6.2 * np.log(dmc_prev) - 17.2
        mr = mo + 1000.0 * re / (48.77 + b * re)
        dmc_prev = max(0.0, 244.72 - 43.43 * np.log(mr - 20.0))
    t = max(temp, -1.1)
    k = 1.894 * (t + 1.1) * (100.0 - rh) * _LE[month - 1] * 1e-6
    return dmc_prev + 100.0 * k


def _dc(temp, rain, dc_prev, month):
    if rain > 2.8:
        rd = 0.83 * rain - 1.27
        smi = 800.0 * np.exp(-dc_prev / 400.0)
        dr = dc_prev - 400.0 * np.log(1.0 + 3.937 * rd / smi)
        dc_prev = max(dr, 0.0)
    t = max(temp, -2.8)
    v = 0.36 * (t + 2.8) + _LF[month - 1]
    return max(dc_prev + 0.5 * max(v, 0.0), 0.0)


def _isi(ffmc, wind):
    m = 147.2 * (101.0 - ffmc) / (59.5 + ffmc)
    return 0.208 * np.exp(0.05039 * wind) * 91.9 * np.exp(-0.1386 * m) * (
        1.0 + m ** 5.31 / 4.93e7)


def _bui(dmc, dc):
    """Buildup Index.

    ⚠️ **Clamped at zero.** The second branch can return a small negative number when DMC is
    low, and BUI is then raised to a fractional power in `_fwi` — which in Python silently
    yields a *complex* result rather than raising. That propagates through the whole series and
    only surfaces later as a dtype error, having quietly poisoned every downstream statistic.
    """
    if dmc <= 0 and dc <= 0:
        return 0.0
    if dmc <= 0.4 * dc:
        bui = 0.8 * dmc * dc / (dmc + 0.4 * dc) if (dmc + 0.4 * dc) > 0 else 0.0
    else:
        bui = dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7)
    return max(float(bui), 0.0)


def _fwi(isi, bui):
    f = (0.626 * bui ** 0.809 + 2.0) if bui <= 80.0 else (
        1000.0 / (25.0 + 108.64 * np.exp(-0.023 * bui)))
    b = 0.1 * isi * f
    return np.exp(2.72 * (0.434 * np.log(b)) ** 0.647) if b > 1.0 else b


def fire_weather_index(
    hourly: pd.DataFrame, *,
    ffmc0: float = 85.0, dmc0: float = 6.0, dc0: float = 15.0,
) -> pd.DataFrame:
    """Daily FWI components from an hourly record.

    Noon-LST temperature, humidity and wind, with precipitation accumulated over the 24 h to
    noon. Startup values are the Van Wagner defaults for a spring start after snowmelt; because
    DC and DMC integrate for months, the series must begin well before the period of interest
    for the drought codes to mean anything.
    """
    noon = hourly[hourly.index.hour == NOON_LST_UTC].copy()
    rain24 = hourly["precipitation"].rolling(24, min_periods=1).sum()
    noon["rain24"] = rain24.reindex(noon.index)

    ffmc, dmc, dc = ffmc0, dmc0, dc0
    rows = []
    for ts, r in noon.iterrows():
        t, rh = float(r["temperature_2m"]), float(np.clip(r["relative_humidity_2m"], 0, 100))
        w, rain = float(r["wind_speed_10m"]), float(r["rain24"])
        ffmc = _ffmc(t, rh, w, rain, ffmc)
        dmc = _dmc(t, rh, rain, dmc, ts.month)
        dc = _dc(t, rain, dc, ts.month)
        isi, bui = _isi(ffmc, w), _bui(dmc, dc)
        rows.append({"date": ts.normalize(), "temp": t, "rh": rh, "wind": w, "rain24": rain,
                     "FFMC": ffmc, "DMC": dmc, "DC": dc, "ISI": isi, "BUI": bui,
                     "FWI": _fwi(isi, bui)})
    return pd.DataFrame(rows).set_index("date").round(2)


# EFFIS danger classes for FWI over Europe.
FWI_CLASSES = ((0, 5.2, "very low"), (5.2, 11.2, "low"), (11.2, 21.3, "moderate"),
               (21.3, 38.0, "high"), (38.0, 50.0, "very high"), (50.0, 1e9, "extreme"))


def fwi_class(v: float) -> str:
    for lo, hi, name in FWI_CLASSES:
        if lo <= v < hi:
            return name
    return "extreme"


def wind_vector_mean(hourly: pd.DataFrame) -> tuple[float, float]:
    """Speed-weighted mean wind direction and speed.

    ⚠️ **Directions are circular — never average degrees.** 350° and 10° average to 0°, not
    180°. Decompose to vector components, average those, then recover the bearing.
    """
    spd = hourly["wind_speed_10m"].to_numpy()
    d = np.radians(hourly["wind_direction_10m"].to_numpy())
    u, v = (spd * np.sin(d)).mean(), (spd * np.cos(d)).mean()
    return float((np.degrees(np.arctan2(u, v)) + 360) % 360), float(np.hypot(u, v))


def era5_dataset(path: str = "gs://gcp-public-data-arco-era5/ar/"
                              "full_37-1h-0p25deg-chunk-1.zarr-v3"):
    """Open ARCO-ERA5 anonymously. Requires `xarray`, `zarr` and `gcsfs`.

    Lazy — nothing transfers until a selection is computed, so a `.sel()` on a point and a
    date range costs kilobytes rather than the petabyte the store nominally holds.
    """
    import xarray as xr

    return xr.open_zarr(path, storage_options={"token": "anon"}, consolidated=True,
                        chunks=None)
