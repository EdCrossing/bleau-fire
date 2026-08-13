"""Vector layers from IGN's Géoplateforme WFS, plus paths from OpenStreetMap.

France publishes an unusually good open geospatial stack, and all of it is anonymous and free
under Licence Ouverte / Etalab 2.0. The layers that matter here:

    LANDCOVER.FORESTINVENTORY.V2:formation_vegetale
        **BD Forêt® V2** — the national forest map. Polygons carrying `tfv` (type de formation
        végétale), `tfv_g11` (an 11-class grouping) and `essence` (dominant species). This is
        real fuel-type data, which turns "how much burned" into "what kind of forest burned",
        and lets the dNBR-vs-RdNBR question be tested against labels instead of argued.

    IGNF_RPG_PARCELLES-AGRICOLES-CATEGORISEES_2024:...
        **RPG** — declared agricultural parcels. The direct fix for the harvest contamination
        in FINDINGS.md F2: crops cut between the two dates read as low-severity burn, and this
        says exactly where the crops are.

    BDTOPO_V3:troncon_de_route      roads, forest tracks and their surface/importance
    ONF.FORETS_PUBLIQUES            state-managed forest boundaries

⚠️ **WFS pagination is not optional.** The service caps a single response, so a naive request
silently returns the first page and looks like a complete answer — a whole class of "why is my
layer clipped" bugs. `fetch_layer` pages with `startIndex` until a short page arrives.

⚠️ **Axis order.** In WFS 2.0 with `urn:ogc:def:crs:EPSG::4326`, the BBOX is
`miny,minx,maxy,maxx` — latitude first. Passing lon-first returns either nothing or the wrong
part of the world, and it fails quietly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import requests

from .config import AOI, VECTOR_DIR

WFS = "https://data.geopf.fr/wfs/ows"
PAGE = 5000
HEADERS = {"User-Agent": "bleau-fire/0.1 (Fontainebleau burn severity)"}

LAYERS: dict[str, str] = {
    "bdforet": "LANDCOVER.FORESTINVENTORY.V2:formation_vegetale",
    "rpg": "IGNF_RPG_PARCELLES-AGRICOLES-CATEGORISEES_2024:parcelles_agricole_categorisees_2024",
    "roads": "BDTOPO_V3:troncon_de_route",
    "onf": "ONF.FORETS_PUBLIQUES:ONF_FORETS_PUBLIQUES",
    "vegetation": "BDTOPO_V3:zone_de_vegetation",
}


def fetch_layer(
    key: str,
    *,
    bbox: tuple[float, float, float, float] = AOI,
    refresh: bool = False,
    max_pages: int = 40,
) -> gpd.GeoDataFrame:
    """Fetch one WFS layer clipped to the bbox, paging until exhausted."""
    typename = LAYERS[key]
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    cache = VECTOR_DIR / f"ign_{key}.geojson"
    if cache.exists() and not refresh:
        gdf = gpd.read_file(cache)
        print(f"  [ign] {key}: {len(gdf)} features from cache")
        return gdf

    w, s, e, n = bbox
    feats: list[dict] = []
    t0 = time.time()
    for page in range(max_pages):
        params = {
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": typename, "SRSNAME": "EPSG:4326",
            # latitude first — see the module docstring
            "BBOX": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326",
            "COUNT": str(PAGE), "STARTINDEX": str(page * PAGE),
            "OUTPUTFORMAT": "application/json",
        }
        r = requests.get(WFS, params=params, headers=HEADERS, timeout=300)
        r.raise_for_status()
        batch = r.json().get("features", [])
        feats.extend(batch)
        print(f"  [ign] {key}: page {page + 1} -> {len(batch)} ({len(feats)} total)")
        if len(batch) < PAGE:
            break
    else:
        print(f"  [ign] {key}: hit max_pages={max_pages}; layer may be truncated")

    if not feats:
        raise RuntimeError(f"{key}: WFS returned no features for bbox {bbox}")

    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf.to_file(cache, driver="GeoJSON")
    print(f"  [ign] {key}: {len(gdf)} features in {time.time() - t0:.1f}s -> {cache.name}")
    return gdf


def load(key: str) -> gpd.GeoDataFrame:
    return gpd.read_file(VECTOR_DIR / f"ign_{key}.geojson")


# ---------------------------------------------------------------------------
# Paths from OpenStreetMap.
#
# BD TOPO's `troncon_de_route` covers roads and forest tracks well but is thin on the informal
# sandy footpaths that actually connect Fontainebleau's boulders — and those paths, including
# the painted circuit approaches and the GR footpaths, are exactly what OSM is good at.
# Use both: BD TOPO for vehicle access, OSM for walking.
# ---------------------------------------------------------------------------

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

PATH_QUERY = """
[out:json][timeout:180];
(
  way["highway"~"^(path|track|footway|bridleway|cycleway)$"]({s},{w},{n},{e});
  relation["route"="hiking"]({s},{w},{n},{e});
);
out geom;
"""


def fetch_paths(
    bbox: tuple[float, float, float, float] = AOI, *, refresh: bool = False
) -> gpd.GeoDataFrame:
    """Walking and track network from OSM, as LineStrings."""
    from shapely.geometry import LineString

    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    cache = VECTOR_DIR / "osm_paths.geojson"
    if cache.exists() and not refresh:
        gdf = gpd.read_file(cache)
        print(f"  [osm] paths: {len(gdf)} from cache")
        return gdf

    w, s, e, n = bbox
    q = PATH_QUERY.format(s=s, w=w, n=n, e=e)
    payload = None
    for url in OVERPASS_MIRRORS:
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=300)
            if r.status_code in (429, 502, 503, 504):
                print(f"  [osm] {url.split('/')[2]} -> {r.status_code}, trying next")
                continue
            r.raise_for_status()
            payload = r.json()
            break
        except requests.RequestException as exc:
            print(f"  [osm] {url.split('/')[2]} -> {type(exc).__name__}, trying next")
    if payload is None:
        raise RuntimeError("all Overpass mirrors failed for the path query")

    rows = []
    for el in payload["elements"]:
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "highway": tags.get("highway"),
            "name": tags.get("name"),
            "surface": tags.get("surface"),
            "geometry": LineString([(p["lon"], p["lat"]) for p in geom]),
        })

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(cache, driver="GeoJSON")
    print(f"  [osm] paths: {len(gdf)} -> {cache.name}")
    return gdf
