"""Place-name labels for the web viewer, from OpenStreetMap and the Boolder database.

The map viewer shows a burn scar over bare imagery. Without labels it is unreadable to anyone
without local knowledge: the scar is somewhere south-west of *something*, and the sector that
burned has no name on the screen. Two complementary sources fix that, and neither alone is
enough:

    **OSM settlements** (`place=city|town|village|hamlet`) — the frame of reference. These are
    what a visitor recognises and what a news report names: Milly-la-Forêt, Noisy-sur-École,
    Barbizon. They carry `population`, which gives an honest importance ordering instead of a
    guessed one.

    **OSM `natural=peak` and named `place=locality`** — Fontainebleau's landmarks. There are no
    summits here in any alpine sense; `natural=peak` is how the massif's named sandstone
    platières and rochers are mapped ("Rocher de la Cathédrale", "Mont Aigu"), and it is the
    richest source of in-forest toponymy. `place=locality` is the same idea mapped differently,
    but in this AOI 400 of the ~460 localities are `Carrefour de ...` — forest road junctions,
    which are furniture rather than places and would swamp the label budget with long names.
    `JUNCTION_PREFIXES` drops them.

    **Boolder `areas`** — the climbing sectors. This is the layer that makes the map legible to
    the audience that cares: "Franchard Isatis" and "Cuvier Rempart" are the units a climber
    navigates by, and OSM maps them inconsistently or not at all. Boolder carries a bounding box
    and `problems_count` per area, so importance is *measured* (how much climbing is there)
    rather than asserted.

Sector position is the **bbox centre**, not a random problem. An area's problems are strung
along a chain of boulders; any single one places the label off to one end.

⚠️ **Overpass rejects the default python-requests User-Agent with HTTP 406.** It is not rate
limiting and not a query error, so it reads like a broken query and sends you off debugging
Overpass QL. Send `HEADERS` — the same ones `ign.py` uses — on every call.

⚠️ **Axis order, again.** Overpass bboxes are `(south, west, north, east)` — latitude first,
matching the WFS/WMS trap documented in `ign.py` and `webexport.py`, and opposite to this
project's own `AOI` tuple, which is lon-first `(w, s, e, n)`. The conversion happens once, in
`fetch_places`.

⚠️ **The raw response is cached to disk.** OSM is a moving target and the mirrors are
best-effort; without a cache the label set silently changes between builds and a published page
cannot be reproduced. Delete the cache or pass `refresh=True` to re-fetch deliberately.

⚠️ **The output is inlined into a size-constrained HTML page.** Coordinates are rounded to 5 dp
(~1 m, far finer than any of these positions is actually known) and the label count is capped.
`rank` exists so the viewer can thin labels by zoom: 0 is drawn first, higher ranks appear only
when zoomed in. Nothing here should grow without checking the byte count printed at the end.

Settlements and Boolder sectors together already use ~190 of the 200 slots, so the landmarks
compete for what is left, and *which* of the ninety survive is a real choice. Sorting them by
name and truncating would keep an alphabetical prefix — twelve labels from A and B, landing
wherever the alphabet happens to put them. Instead `combine` runs **farthest-point sampling**:
it repeatedly adds the landmark furthest from every label already placed, and stops when the
best remaining candidate is within `MIN_SEPARATION_M` of an existing one. The survivors spread
across the massif, and peaks already covered by a named sector are the first to be skipped.

The binding constraint is visual clutter, not bytes: at ~80 bytes a label the 60 KB budget
would hold ~700. Raise `cap` only after looking at the rendered map.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import requests

from .config import AOI, DATA, VECTOR_DIR

WEB_DIR = DATA / "web"
BOOLDER_DB = VECTOR_DIR / "boolder.db"

HEADERS = {"User-Agent": "bleau-fire/0.1 (Fontainebleau burn severity)"}

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

# Nodes only: settlements and landmarks are mapped as nodes, and `out center` on ways would
# drag in every named area polygon in the massif for no extra legibility.
PLACE_QUERY = """
[out:json][timeout:180];
(
  node["place"~"^(city|town|village|hamlet)$"]["name"]({s},{w},{n},{e});
  node["natural"="peak"]["name"]({s},{w},{n},{e});
  node["place"="locality"]["name"]({s},{w},{n},{e});
);
out body;
"""

# Rank floor per OSM place class. Cities and towns share the top slot: this AOI has no city,
# and a `place=city` here would be a mis-tag rather than a genuine metropolis.
PLACE_RANK = {"city": 0, "town": 0, "village": 2, "hamlet": 4}

# Settlement classes collapse onto the three kinds the viewer knows about.
PLACE_KIND = {"city": "town", "town": "town", "village": "village", "hamlet": "hamlet"}

# Peaks and localities are landmarks, not settlements — they ride in as "sector" so the viewer
# styles them like the climbing areas they usually are, at the lowest priority.
LANDMARK_RANK = 5

# Localities that are really road junctions. Case-folded prefix match on the name.
JUNCTION_PREFIXES = ("carrefour", "rond-point", "rond point", "croix de", "place de")

# Minimum spacing between a kept landmark and any label already accepted, in metres.
MIN_SEPARATION_M = 700.0

# Boolder sector rank thresholds, by problems_count. Deliberately coarse: the exact ordering of
# two 300-problem areas is noise, but "one of the massif's major sectors" versus "a roadside
# handful" is a real distinction and the only one a zoom threshold can express.
SECTOR_TIERS = ((300, 1), (150, 2), (60, 3), (20, 4))

MAX_LABELS = 200


def fetch_places(
    bbox: tuple[float, float, float, float] = AOI, *, refresh: bool = False
) -> list[dict]:
    """Raw OSM place nodes for the bbox, cached to disk.

    Returns the Overpass `elements` list untouched, so the cache stays a faithful record of what
    the API said and every downstream choice remains re-derivable from it.
    """
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    cache = VECTOR_DIR / "osm_places_raw.json"
    if cache.exists() and not refresh:
        elements = json.loads(cache.read_text())["elements"]
        print(f"  [osm] places: {len(elements)} nodes from cache")
        return elements

    w, s, e, n = bbox  # AOI is lon-first; Overpass wants lat-first — see the module docstring
    q = PLACE_QUERY.format(s=s, w=w, n=n, e=e)
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
        raise RuntimeError("all Overpass mirrors failed for the place query")

    cache.write_text(json.dumps(payload, ensure_ascii=False))
    elements = payload["elements"]
    print(f"  [osm] places: {len(elements)} nodes -> {cache.name}")
    return elements


def _population(tags: dict) -> int:
    """OSM `population` is free text: "1 234", "1234 (2019)", occasionally nonsense."""
    raw = "".join(ch for ch in tags.get("population", "") if ch.isdigit())
    return int(raw) if raw else 0


def osm_labels(elements: list[dict]) -> list[dict]:
    """Turn raw Overpass nodes into labels, ranked by class then population."""
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or "lat" not in el:
            continue

        place = tags.get("place")
        if place in PLACE_RANK:
            kind, rank, src = PLACE_KIND[place], PLACE_RANK[place], "osm-place"
            pop = _population(tags)
            # Population nudges by one step only. It corrects the common case of a large
            # village tagged like a tiny one, without letting a hamlet outrank a town —
            # `place` is a surveyed classification and population is often stale or absent.
            if pop >= 10000:
                rank = max(rank - 1, 0)
            elif pop and pop < 500:
                rank += 1
        elif tags.get("natural") == "peak" or place == "locality":
            if name.lower().startswith(JUNCTION_PREFIXES):
                continue
            kind, rank, pop, src = "sector", LANDMARK_RANK, 0, "osm-landmark"
        else:
            continue

        rows.append({
            "name": name,
            "lat": el["lat"],
            "lon": el["lon"],
            "kind": kind,
            "rank": max(rank, 0),
            "_pop": pop,
            "_src": src,
        })
    return rows


def boolder_sectors(
    db: Path = BOOLDER_DB, bbox: tuple[float, float, float, float] = AOI
) -> list[dict]:
    """Climbing sector labels from Boolder's `areas`, positioned at the bbox centre.

    Clipped to the AOI: Boolder covers the whole massif, and a handful of northern areas sit
    outside the grid this viewer renders, where a label would point off the edge of the image.
    """
    if not db.exists():
        raise FileNotFoundError(
            f"{db} missing — download it with:\n"
            f"  curl -sL -o {db} "
            f"https://github.com/boolder-org/boolder-data/raw/main/boolder.db"
        )
    con = sqlite3.connect(db)
    rows = con.execute(
        """
        SELECT name, south_west_lat, south_west_lon, north_east_lat, north_east_lon,
               problems_count
        FROM areas
        WHERE name IS NOT NULL AND problems_count > 0
          AND south_west_lat IS NOT NULL AND north_east_lat IS NOT NULL
        ORDER BY problems_count DESC
        """
    ).fetchall()
    con.close()

    w, s, e, n = bbox
    out = []
    for name, sw_lat, sw_lon, ne_lat, ne_lon, count in rows:
        lat, lon = (sw_lat + ne_lat) / 2, (sw_lon + ne_lon) / 2
        if not (w <= lon <= e and s <= lat <= n):
            continue
        rank = next((r for threshold, r in SECTOR_TIERS if count >= threshold), 5)
        out.append({
            "name": name, "lat": lat, "lon": lon, "kind": "sector",
            "rank": rank, "_pop": count, "_src": "boolder",
        })
    print(f"  [boolder] sectors: {len(out)} of {len(rows)} areas inside the AOI")
    return out


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _metres(a: dict, b: dict, *, lat0: float = 48.38) -> float:
    """Planar approximation, adequate over a 30 km AOI and cheap enough for an O(n²) pass."""
    import math

    dy = (a["lat"] - b["lat"]) * 111_320.0
    dx = (a["lon"] - b["lon"]) * 111_320.0 * math.cos(math.radians(lat0))
    return math.hypot(dx, dy)


def combine(osm: list[dict], sectors: list[dict], *, cap: int = MAX_LABELS) -> list[dict]:
    """Merge, de-duplicate by name, thin the landmarks spatially and cap.

    Boolder wins name ties: where OSM also maps a sector as a peak or locality, Boolder's entry
    carries a measured importance and a bbox-centre position, while the OSM node is usually one
    rock within the same area.
    """
    seen: dict[str, dict] = {}
    for row in sectors + osm:  # sectors first, so they claim the name
        k = _key(row["name"])
        if k in seen:
            continue
        seen[k] = row

    ordered = sorted(seen.values(), key=lambda r: (r["rank"], -r["_pop"], r["name"]))
    # Split on provenance, not on rank: a small Boolder sector can share rank 5 with the
    # landmarks, and it is not a thinning candidate — it is a real destination.
    kept = [r for r in ordered if r["_src"] != "osm-landmark"][:cap]
    landmarks = [r for r in ordered if r["_src"] == "osm-landmark"]

    # Farthest-point sampling for whatever budget the landmarks get: repeatedly take the one
    # furthest from every label already on the map. Sorting them by name and truncating would
    # have kept an alphabetical prefix — here, a dozen survivors from ninety candidates
    # ("Allée des Marsaules" through "Butte Ronde"), which is not a thinning rule at all.
    while len(kept) < cap and landmarks:
        best = max(landmarks, key=lambda r: min(_metres(r, k) for k in kept))
        if min(_metres(best, k) for k in kept) < MIN_SEPARATION_M:
            break  # every remaining landmark collides with a label already placed
        landmarks.remove(best)
        kept.append(best)

    kept.sort(key=lambda r: (r["rank"], -r["_pop"], r["name"]))
    return [
        {
            "name": r["name"],
            # 5 dp is ~1 m — finer than any of these positions is genuinely known, and every
            # extra digit is bytes in an inlined payload.
            "lat": round(r["lat"], 5),
            "lon": round(r["lon"], 5),
            "kind": r["kind"],
            "rank": r["rank"],
        }
        for r in kept
    ]


def write(places: list[dict], out: Path = WEB_DIR / "places.json") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"places": places}, separators=(",", ":"), ensure_ascii=False)
    )
    return out


def build(*, refresh: bool = False, cap: int = MAX_LABELS) -> Path:
    """Fetch, merge, write, and report."""
    osm = osm_labels(fetch_places(refresh=refresh))
    sectors = boolder_sectors()
    places = combine(osm, sectors, cap=cap)
    out = write(places)

    counts: dict[str, int] = {}
    for p in places:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    size = out.stat().st_size

    print(f"\n  {len(places)} labels -> {out}  ({size / 1024:.1f} KB)")
    print("  by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("\n  top 15 by rank:")
    for p in places[:15]:
        print(f"    {p['rank']}  {p['kind']:<8} {p['name']}  ({p['lat']}, {p['lon']})")
    if size > 60_000:
        print(f"\n  ⚠️ {size / 1024:.1f} KB exceeds the 60 KB budget — lower `cap`")
    return out


if __name__ == "__main__":
    build()
