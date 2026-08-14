"""Sentinel-5P / TROPOMI, for the smoke rather than the scar.

Everything else in this repo measures the ground. This measures what the fire put into the air,
from a different satellite, a different instrument and a different physical principle — so it is
a genuinely independent line of evidence rather than another cut of the same data.

MEEO publish Sentinel-5P L2 on AWS (`meeo-s5p`, anonymous) already reprojected to EPSG:4326 as
Cloud Optimized GeoTIFFs, one file per orbit per variable, keyed by date. That avoids the
NetCDF-with-irregular-swath-coordinates handling the native product would need, and means the
same windowed `WarpedVRT` read used for Sentinel-2 works unchanged.

Products that matter for a fire plume:

    L2__AER_AI   UV Aerosol Index — the smoke detector. Positive values indicate UV-absorbing
                 aerosol: smoke, dust, ash. Near zero or negative for cloud and clear sky.
                 Unusually robust, because it is a spectral contrast rather than a retrieval,
                 so it works over bright surfaces and does not need an aerosol model.
    L2__CO____   Carbon monoxide total column. Combustion tracer, long-lived enough to follow
                 a plume downwind.
    L2__AER_LH   Aerosol layer height — is the plume in the boundary layer or lofted above it?

⚠️ **Resolution vs the target.** TROPOMI ground pixels are ~5.5 x 3.5 km. The fire is ~2,000 ha,
i.e. roughly 20 km² — **about one TROPOMI pixel**. Any signal is therefore a heavily diluted
mixture of plume and background, and a null result would say more about the instrument's
footprint than about the fire. Stated up front so a weak signal is not over-read.

⚠️ **One overpass a day, early afternoon.** Sentinel-5P is in a 13:30 ascending-node sun-
synchronous orbit, so France is imaged around 11:00-13:00 UTC. A fire that peaks in the evening
is sampled hours before its worst, and the plume seen is the morning's.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

BUCKET = "https://meeo-s5p.s3.amazonaws.com"
S5P_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MULTIPLEX="YES",
    VSI_CACHE="TRUE",
    AWS_NO_SIGN_REQUEST="YES",
)

PRODUCTS = {
    "aer_ai": ("L2__AER_AI", "PRODUCT_aerosol_index_340_380"),
    "co": ("L2__CO____", "PRODUCT_carbonmonoxide_total_column"),
    # NB the variable is "aerosol_mid_height", not "aerosol_layer_height" — the retrieval
    # reports the mid-altitude of a single assumed scattering layer, not a layer thickness.
    "aer_lh": ("L2__AER_LH", "PRODUCT_aerosol_mid_height"),
    "aer_lp": ("L2__AER_LH", "PRODUCT_aerosol_mid_pressure"),
    "no2": ("L2__NO2___", "PRODUCT_nitrogendioxide_tropospheric_column"),
}

_TIMES = re.compile(r"_(\d{8}T\d{6})_(\d{8}T\d{6})_")


def list_products(product: str, date: str, *, stream: str = "OFFL") -> list[str]:
    """List COG keys for one product on one date (`YYYY-MM-DD`)."""
    folder, _ = PRODUCTS[product]
    y, m, d = date.split("-")
    prefix = f"COGT/{stream}/{folder}/{y}/{m}/{d}/"
    keys, token = [], None
    while True:
        url = f"{BUCKET}/?list-type=2&max-keys=1000&prefix={prefix}"
        if token:
            url += f"&continuation-token={requests.utils.quote(token, safe='')}"
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys += [e.text for e in root.findall(".//s3:Contents/s3:Key", ns)
                 if e.text and e.text.endswith(".tif")]
        nxt = root.find(".//s3:NextContinuationToken", ns)
        if nxt is None or not nxt.text:
            break
        token = nxt.text
    return keys


def pick_orbit(keys: list[str], product: str, *, hour_utc: float = 12.0) -> str | None:
    """Choose the orbit whose sensing window is closest to a target UTC hour.

    For France that is early afternoon; picking by name order instead would silently return the
    first orbit of the day, which images the far side of the planet.
    """
    _, var = PRODUCTS[product]
    best, best_gap = None, 1e9
    for k in keys:
        if var not in k:
            continue
        m = _TIMES.search(k)
        if not m:
            continue
        t0 = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
        t1 = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S")
        mid = (t0.hour + t0.minute / 60 + t1.hour + t1.minute / 60) / 2
        gap = abs(mid - hour_utc)
        if gap < best_gap:
            best, best_gap = k, gap
    return best


def read_bbox(key: str, bbox: tuple[float, float, float, float], *, width: int = 400
              ) -> tuple[np.ndarray, tuple]:
    """Read a COG over a WGS84 bbox onto a regular grid. Returns (array, transform)."""
    from rasterio.transform import from_bounds

    w, s, e, n = bbox
    height = max(1, int(round(width * (n - s) / (e - w))))
    transform = from_bounds(w, s, e, n, width, height)
    with rasterio.Env(**S5P_ENV):
        with rasterio.open(f"/vsicurl/{BUCKET}/{key}") as src:
            with WarpedVRT(src, crs="EPSG:4326", transform=transform,
                           width=width, height=height,
                           resampling=Resampling.bilinear) as vrt:
                arr = vrt.read(1, masked=True)
    return arr.astype("float32").filled(np.nan), transform


def qa_key_for(key: str) -> str:
    """The qa_value COG belonging to the same granule as `key`.

    ⚠️ **Aerosol layer height must be quality-filtered or it is meaningless.** The retrieval
    solves for the mid-altitude of a *single assumed scattering layer*; over a clear scene there
    is no such layer and it returns a fitted number anyway. Reading it unfiltered produces a
    full field of confident-looking heights for air containing nothing.
    """
    return re.sub(r"PRODUCT_[a-z0-9_]+_4326\.tif$", "PRODUCT_qa_value_4326.tif", key)


def candidate_orbits(keys: list[str], product: str, *, hour_utc: float = 12.0) -> list[str]:
    """All orbits for the product, ordered by how close their sensing window is to `hour_utc`."""
    _, var = PRODUCTS[product]
    scored = []
    for k in keys:
        if var not in k:
            continue
        m = _TIMES.search(k)
        if not m:
            continue
        t0 = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
        t1 = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S")
        mid = (t0.hour + t0.minute / 60 + t1.hour + t1.minute / 60) / 2
        scored.append((abs(mid - hour_utc), k))
    return [k for _, k in sorted(scored)]


def sample_day(product: str, date: str, bbox: tuple[float, float, float, float],
               *, hour_utc: float = 12.0, width: int = 400, min_coverage: float = 20.0,
               max_tries: int = 5):
    """Read the best-covering daytime orbit over the region.

    ⚠️ **Nearest-in-time is not the same as covering your area.** Sentinel-5P flies 14 orbits a
    day and each swath is a strip; the orbit whose mid-time is closest to local noon may still
    miss the region entirely, returning an all-NaN array that looks exactly like "the instrument
    saw nothing". A first version of this did precisely that and silently dropped **12 July —
    the ignition day** — reporting 0% coverage as if no data existed.

    So candidates are tried in order of time-proximity and the first with real coverage wins.
    """
    keys = list_products(product, date)
    best, best_arr, best_tf, best_cov = None, None, None, -1.0
    for key in candidate_orbits(keys, product, hour_utc=hour_utc)[:max_tries]:
        arr, transform = read_bbox(key, bbox, width=width)
        cov = float(np.isfinite(arr).mean()) * 100
        if cov > best_cov:
            best, best_arr, best_tf, best_cov = key, arr, transform, cov
        if cov >= min_coverage:
            break
    if best is None:
        return None, None, None
    return best_arr, best_tf, best
