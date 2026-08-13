"""Climbing features from OpenStreetMap via the Overpass API.

**Why OSM rather than 27 Crags or bleau.info.** Fontainebleau is exceptionally well mapped for
climbing in OSM — the massif carries ~1,150 features, including individual problem start points
tagged with Font grade, circuit colour and circuit number. Crucially the cross-reference to
bleau.info is *already in OSM* (`ref:bleau.info`, `climbing:url:bleauinfo`), so the join key
comes for free under ODbL. Nothing needs scraping, and nothing with unclear terms ends up in a
public repo.

Three feature types matter, and they answer different questions:

    climbing=crag          ~224  a sector, e.g. "Roche aux Sabots". The right unit for a map.
    climbing=boulder       ~197  a single named rock.
    climbing=route_bottom  ~724  one problem's start. The right unit for a precise answer,
                                 and the only one carrying grade and circuit membership.

⚠️ OSM positions are contributed, not surveyed. Expect metres of error on individual problems —
which is sub-pixel at 10 m for a crag but *not* negligible for a single boulder sitting near a
burn edge. That uncertainty has to reach the final answer rather than be quietly dropped.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point

from .config import AOI, VECTOR_DIR

# Overpass instances are volunteer-run and rate-limited, and 429/504 under load is normal
# rather than exceptional. Rotate mirrors and back off; the cached raw payload means this
# only has to succeed once.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

# Overpass returns 406 to the default `python-requests` user agent. Identifying the client is
# also just good manners on a free endpoint.
HEADERS = {"User-Agent": "bleau-fire/0.1 (Fontainebleau burn severity; contact via repo)"}

# Tags kept from the raw OSM payload. Everything else is dropped to keep the table readable.
KEEP = (
    "name", "climbing", "sport",
    "climbing:grade:fb", "climbing:grade:french",
    "climbing:circuit:colour", "climbing:circuit:number", "climbing:circuit:id",
    "climbing:boulder", "climbing:length", "climbing:start",
    "ref:bleau.info", "climbing:url:bleauinfo", "climbing:url:boolder",
    "description", "note",
)


def _query(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    return f"""
[out:json][timeout:180];
(
  node["sport"="climbing"]{box};
  way["sport"="climbing"]{box};
  node["climbing"]{box};
  way["climbing"]{box};
);
out tags center;
"""


def _post_with_retry(query: str, *, attempts: int = 3, timeout: int = 300) -> dict:
    """POST the query, rotating mirrors and backing off on transient failures."""
    last: Exception | None = None
    for attempt in range(attempts):
        for url in OVERPASS_MIRRORS:
            try:
                r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=timeout)
                if r.status_code in (429, 502, 503, 504):
                    print(f"  [osm] {url.split('/')[2]} -> {r.status_code}, trying next")
                    last = requests.HTTPError(f"{r.status_code} from {url}")
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as exc:
                print(f"  [osm] {url.split('/')[2]} -> {type(exc).__name__}, trying next")
                last = exc
        if attempt < attempts - 1:
            wait = 10 * (attempt + 1)
            print(f"  [osm] all mirrors busy, waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"all Overpass mirrors failed after {attempts} rounds") from last


def fetch(
    bbox: tuple[float, float, float, float] = AOI,
    *,
    refresh: bool = False,
    raw_path: Path | None = None,
) -> gpd.GeoDataFrame:
    """Fetch climbing features and return them as an EPSG:4326 GeoDataFrame.

    The raw Overpass response is cached verbatim. OSM changes continuously, so a run that
    cannot be reproduced from a stored payload is not reproducible at all — the same reasoning
    as caching the STAC search.
    """
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = raw_path or VECTOR_DIR / "osm_climbing_raw.json"

    if raw_path.exists() and not refresh:
        payload = json.loads(raw_path.read_text())
        print(f"  [osm] {len(payload['elements'])} elements from cache {raw_path.name}")
    else:
        t0 = time.time()
        payload = _post_with_retry(_query(bbox))
        raw_path.write_text(json.dumps(payload))
        print(f"  [osm] {len(payload['elements'])} elements in {time.time() - t0:.1f}s "
              f"-> cached {raw_path.name}")

    rows = []
    for el in payload["elements"]:
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        row = {"osm_id": f"{el['type']}/{el['id']}", "osm_type": el["type"]}
        row.update({k: tags.get(k) for k in KEEP})
        row["geometry"] = Point(lon, lat)
        rows.append(row)

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf["feature_type"] = gdf["climbing"].fillna("unknown")
    return gdf


def save(gdf: gpd.GeoDataFrame, path: Path | None = None) -> Path:
    path = path or VECTOR_DIR / "climbing.geojson"
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    return path


def load(path: Path | None = None) -> gpd.GeoDataFrame:
    path = path or VECTOR_DIR / "climbing.geojson"
    return gpd.read_file(path)
