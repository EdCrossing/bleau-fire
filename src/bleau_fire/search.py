"""STAC search over Earth Search, cached to disk.

Adapted from `~/projects/eo-agent` (`eo_agent.search`).

A STAC API is four nested things and nothing more exotic:

    Catalog     -> a tree of things
    Collection  -> a dataset, e.g. "sentinel-2-l2a"
    Item        -> ONE acquisition by one satellite at one moment. Literally a GeoJSON
                   Feature: footprint polygon + datetime + properties + assets.
    Asset       -> a named file, usually one per spectral band, as a URL.

Responses are cached because catalogues change under you — items get reprocessed and hrefs
move. Since this repo pins two specific scene IDs as the bi-temporal pair, a shifting
catalogue would quietly change the headline result. The cache makes the run reproducible.
"""

from __future__ import annotations

import hashlib
import json

from pystac_client import Client

from .config import AOI, CACHE_DIR, COLLECTION, STAC_API


def search(
    start: str,
    end: str,
    *,
    bbox: tuple[float, float, float, float] = AOI,
    max_cloud: float = 80.0,
    refresh: bool = False,
) -> list[dict]:
    """Return matching STAC Items as plain dicts, sorted by acquisition time.

    `max_cloud` is deliberately loose. Scene-level cloud cover describes the whole 110 km
    granule, not the 30 km AOI — a 60%-cloudy scene can be perfectly clear over the massif.
    Filter loosely here, then mask per pixel.
    """
    key = json.dumps(
        {"bbox": bbox, "start": start, "end": end, "cloud": max_cloud,
         "collection": COLLECTION, "api": STAC_API},
        sort_keys=True,
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    cache = CACHE_DIR / f"{start[:10]}_{end[:10]}_{digest}.json"

    if cache.exists() and not refresh:
        items = json.loads(cache.read_text())["features"]
        print(f"  [search] {len(items)} items from cache {cache.name}")
        return items

    client = Client.open(STAC_API)
    result = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    items = [i.to_dict() for i in result.items()]
    items.sort(key=lambda i: i["properties"]["datetime"])

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"features": items}))
    print(f"  [search] {len(items)} items -> cached {cache.name}")
    return items


def boa_offset(item: dict) -> float:
    """Return the additive offset needed to convert DN to reflectance correctly.

    A genuine trap. Products from processing baseline 04.00 (Jan 2022 onward) carry a
    BOA_ADD_OFFSET of -1000:

        reflectance = (DN + offset) / 10000

    Ignore it and every scene after Jan 2022 is biased by 0.1 reflectance relative to earlier
    ones. For a *difference* index like dNBR the risk is subtler than for a time series: if
    both scenes share a baseline the offset largely cancels, so the bug hides — until it
    doesn't, when a pre/post pair straddles a baseline change. Check rather than assume.
    """
    props = item["properties"]
    if props.get("earthsearch:boa_offset_applied") is True:
        return 0.0
    baseline = str(props.get("s2:processing_baseline", "00.00"))
    try:
        return -1000.0 if float(baseline) >= 4.0 else 0.0
    except ValueError:
        return 0.0


def summarise(items: list[dict]) -> str:
    if not items:
        return "  (no items)"
    return "\n".join(
        f"    {i['properties']['datetime'][:10]}  "
        f"cloud={i['properties'].get('eo:cloud_cover', 0):5.1f}%  "
        f"offset={boa_offset(i):+.0f}  {i['id']}"
        for i in items
    )
