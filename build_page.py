#!/usr/bin/env python
"""Build the self-contained map viewer.

Everything inlines as data URIs because the Artifact CSP blocks external hosts, and base64
inflates by a third — so the size budget is managed here rather than discovered at publish
time. Strings in the point payload are dictionary-encoded for the same reason.
"""

from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path

import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "data" / "web"
LAYERS = WEB / "layers"
OUT = ROOT / "data" / "out"
SERIES = OUT / "series"

# Two build profiles, because the two destinations have different constraints.
#
#   full    — GitHub Pages. No size cap, so nothing is degraded.
#   compact — a published Artifact, which must stay under 16 MB *after* base64 inflates
#             everything by a third. Layers are re-encoded smaller and the series is coarser.
#
# Frames are sized by COVERAGE — how much of the area the satellite actually observed — not by
# how much survived cloud masking. Those are different things and conflating them was a bug: a
# pass at 79% cloud with 0% nodata is a complete, real picture of cloud and deserves full
# resolution, while a 98%-nodata pass is flat hatching and gains nothing from it. 21 frames were
# needlessly degraded before this distinction was drawn.
FULL = "--full" in sys.argv
PROFILE = "full" if FULL else "compact"
SEEN_MIN = 50.0   # percent of the AOI actually observed (100 - nodata)
if FULL:
    SERIES_GOOD, SERIES_EMPTY = (1500, 76), (760, 52)
    LAYER_MAX = {}                      # keep every layer as exported
else:
    SERIES_GOOD, SERIES_EMPTY = (880, 58), (520, 42)
    LAYER_MAX = {"rgb_pre": 2100, "rgb_post": 2100, "ortho_now": 1800,
                 "ortho1950": 1800, "etatmajor": 1900, "anciennes": 1700}
SHRUNK = WEB / "_shrunk"


def uri(path: Path) -> str:
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------
BASE_LAYERS = [
    ("rgb_pre", "10 July 2026", "Sentinel-2A, two days before ignition"),
    ("rgb_post", "20 July 2026", "Sentinel-2A, eight days after"),
    ("ortho_now", "Aerial, current", "IGN orthophotography"),
    ("ortho1950", "Aerial, 1950–65", "IGN historic orthophotography"),
    ("etatmajor", "État-Major, 1820–66", "The survey that defines ancient woodland"),
]
OVERLAYS = [
    ("dnbr", "dNBR", "Burn severity, continuous. Transparent below 0.10."),
    ("severity", "Severity classes", "Key &amp; Benson classes, moderate-low and above."),
    ("fuel", "Forest type", "BD Forêt V2 vegetation formation."),
    ("anciennes", "Ancient woodland", "Continuously wooded since the 1820–66 survey."),
    ("footprint", "Fire footprint", "Derived here, not official — see note below."),
]
CONTEXT_LAYERS = [("ctx_map", "", ""), ("ctx_ortho", "", "")]

def sized(key: str, path: Path) -> Path:
    """Re-encode a layer smaller for the compact profile, caching the result."""
    cap = LAYER_MAX.get(key)
    if not cap:
        return path
    im = Image.open(path)
    if im.width <= cap:
        return path
    SHRUNK.mkdir(parents=True, exist_ok=True)
    out = SHRUNK / f"{PROFILE}_{key}{path.suffix}"
    if not out.exists():
        r = im.resize((cap, round(im.height * cap / im.width)),
                      Image.NEAREST if path.suffix == ".png" else Image.LANCZOS)
        if path.suffix == ".png":
            r.save(out, "PNG", optimize=True)
        else:
            r.convert("RGB").save(out, "JPEG", quality=80, optimize=True, progressive=True)
    return out


layer_uris = {}
for key, *_ in BASE_LAYERS + OVERLAYS + CONTEXT_LAYERS:
    for ext in (".jpg", ".png"):
        p = LAYERS / f"{key}{ext}"
        if p.exists():
            layer_uris[key] = uri(sized(key, p))
            break

# ---------------------------------------------------------------------------
# Time series — recompressed to fit the budget
# ---------------------------------------------------------------------------
if (SERIES / "frames.json").exists():
    frames_meta = json.loads((SERIES / "frames.json").read_text())
else:
    # The series job writes frames.json only at the end, so fall back to the directory —
    # a partial series should still build rather than silently producing an empty scrubber.
    q = {r["id"]: r for r in json.loads((OUT / "scene_quality.json").read_text())}
    frames_meta = [
        {"file": p.name, "date": p.name[:10], "id": p.stem[11:],
         "cloud": round(q.get(p.stem[11:], {}).get("scene_cloud", 0.0), 1),
         "valid": q.get(p.stem[11:], {}).get("aoi_valid_pct")}
        for p in sorted(SERIES.glob("*.jpg"))
    ]
    print(f"[series] frames.json absent — using {len(frames_meta)} files on disk")
series = []
for fm in frames_meta:
    src = SERIES / fm["file"]
    if not src.exists():
        continue
    nod = fm.get("nodata")
    seen = None if nod is None else 100.0 - nod
    w, q = SERIES_GOOD if (seen is None or seen >= SEEN_MIN) else SERIES_EMPTY
    small = SERIES / "web" / PROFILE / f"{w}_{q}_{fm['file']}"
    small.parent.mkdir(parents=True, exist_ok=True)
    if not small.exists():
        im = Image.open(src).convert("RGB")
        im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        im.save(small, "JPEG", quality=q, optimize=True, progressive=True)
    series.append({
        "date": fm["date"], "cloud": fm["cloud"],
        "seen": None if seen is None else round(seen, 1),   # coverage
        "clear": fm.get("valid"),                            # usable after cloud masking
        "img": uri(small),
    })

# ---------------------------------------------------------------------------
# Points — dictionary-encoded
# ---------------------------------------------------------------------------
raw = json.loads((WEB / "points.json").read_text())
areas, circuits, grades = [], [], []
aidx, cidx, gidx = {}, {}, {}


def intern(v, store, index):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return -1
    if v not in index:
        index[v] = len(store)
        store.append(v)
    return index[v]


pts = []
for lon, lat, name, grade, area, circuit, num, dnbr, sev, edge in raw["points"]:
    pts.append([
        round(lon, 5), round(lat, 5), name or "",
        intern(grade, grades, gidx), intern(area, areas, aidx),
        intern(circuit, circuits, cidx),
        # Circuit numbers are not all integers — Boolder uses "D" for départ, "bis", etc.
        "" if num is None or (isinstance(num, float) and pd.isna(num)) else str(num),
        dnbr, sev, edge,
    ])

POINTS = {
    "bounds": raw["bounds"], "context_bounds": raw.get("context_bounds"),
    "areas": areas, "circuits": circuits, "grades": grades,
    "fuel_labels": raw["fuel_labels"], "fuel_colours": raw["fuel_colours"], "points": pts,
}

# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
SEV_LABELS = ["high regrowth", "low regrowth", "unburned", "low severity",
              "moderate-low severity", "moderate-high severity", "high severity"]
SEV_KEY = {"low severity": "s1", "moderate-low severity": "s2",
           "moderate-high severity": "s3", "high severity": "s4"}
SEV_SHORT = {"low severity": "low", "moderate-low severity": "mod-low",
             "moderate-high severity": "mod-high", "high severity": "high"}

circ = pd.read_csv(OUT / "circuits_severity.csv").head(16)
CIRCUIT_ROWS = "\n".join(
    f'<tr data-area="{html.escape(str(r["area_name"]), quote=True)}" '
    f'data-circuit="{html.escape(str(r["circuit_color"]), quote=True)}">'
    f'<td>{html.escape(str(r["area_name"]))}</td>'
    f'<td class="dimtd"><span class="dot" style="background:{r["circuit_color"]}"></span>'
    f'{html.escape(str(r["circuit_color"]))}</td>'
    f'<td class="n">{int(r["problems"])}</td>'
    f'<td class="n">{r["dnbr_median"]:.2f}</td>'
    f'<td class="n b">{r["pct_burned"]:.0f}%</td>'
    f'<td><div class="bar"><span style="width:{r["pct_burned"]:.0f}%"></span></div></td></tr>'
    for _, r in circ.iterrows()
)

# --- fuel cards: chips + description when the chip job has produced them ---
FUEL_DESC_PATH = WEB / "fuel_meta.json"
CHIPS = WEB / "fuel_chips"
fuel_meta = json.loads(FUEL_DESC_PATH.read_text()) if FUEL_DESC_PATH.exists() else {}
fuel_stats = pd.read_csv(OUT / "severity_by_tfv_g11.csv")

def chip_html(slug, when):
    f = CHIPS / f"{slug}_{when}.jpg"
    if not f.exists():
        return ""
    return (f'<div class="chip"><img src="{uri(f)}" alt="{when}-fire imagery">'
            f'<span>{when}</span></div>')

cards = []
for _, r in fuel_stats.iterrows():
    name = r["class"]
    meta = fuel_meta.get(name, {})
    slug = meta.get("slug", "")
    pre, post = (chip_html(slug, "pre"), chip_html(slug, "post")) if slug else ("", "")
    chips = f'<div class="chips">{pre}{post}</div>' if pre or post else ""
    desc = meta.get("description", "")
    cards.append(
        f'<div class="fuel">{chips}'
        f'<h3>{html.escape(name)}</h3>'
        f'<div class="stat"><span>dNBR <b>{r["dnbr_median"]:.3f}</b></span>'
        f'<span>burned <b>{r["pct_burned"]:.0f}%</b></span>'
        f'<span><b>{r["ha_in_fire"]:,.0f}</b> ha</span></div>'
        + (f'<p>{html.escape(desc)}</p>' if desc else "")
        + "</div>"
    )
FUEL_CARDS = "\n".join(cards)

# --- fire weather: fuller ranking with the fire days highlighted in place ---
fwi = pd.read_csv(OUT / "fwi_1940_2026.csv", parse_dates=["date"])
top = fwi.nlargest(20, "FWI").reset_index(drop=True)
FIRE_DAYS = {"2026-07-12", "2026-07-13"}
FWI_ROWS = "\n".join(
    f'<tr class="{"hl" if r["date"].strftime("%Y-%m-%d") in FIRE_DAYS else ""}">'
    f'<td class="n dimtd">{i + 1}</td>'
    f'<td>{r["date"].strftime("%d %b %Y")}</td>'
    f'<td class="n b">{r["FWI"]:.1f}</td>'
    f'<td class="n dimtd">{r["temp"]:.1f}&deg;C</td>'
    f'<td class="n dimtd">{r["rh"]:.0f}%</td>'
    f'<td class="n dimtd">{r["wind"]:.0f}</td>'
    f'<td class="n dimtd">{r["DC"]:.0f}</td></tr>'
    for i, r in top.iterrows()
)

# Distribution across all 31,637 days. The final bin holds the two fire days, so it is
# marked — the point of the figure is how far into the tail they sit.
import numpy as _np
vals = fwi["FWI"].to_numpy()
NB = 46
hi_edge = float(_np.nanmax(vals))
counts, edges = _np.histogram(vals, bins=NB, range=(0, hi_edge))
peak = counts.max() or 1
fire_bin = int(_np.digitize(58.53, edges) - 1)
HIST_BARS = "".join(
    f'<i class="{"fire" if b >= fire_bin else ""}" '
    f'style="height:{max(1, round(100 * c / peak))}%"></i>'
    for b, c in enumerate(counts)
)
HIST_MID = f"{hi_edge / 2:.0f}"
HIST_MAX = f"{hi_edge:.0f}"

# --- place labels, if the places job has produced them ---
PLACES_PATH = WEB / "places.json"
PLACES = (json.loads(PLACES_PATH.read_text()).get("places", [])
          if PLACES_PATH.exists() else [])
PLACES.sort(key=lambda p: p.get("rank", 99))

problems = pd.read_csv(OUT / "problems_severity.csv")
n_burned = int((problems["dnbr_median"] >= 0.27).sum())
n_total = int(problems["dnbr_median"].notna().sum())
forest = json.loads((OUT / "forest_summary.json").read_text())

TPL = Path(__file__).parent / "page_template.html"
HTML = TPL.read_text()
HTML = (
    HTML.replace("__LAYERS__", json.dumps(layer_uris))
    .replace("__POINTS__", json.dumps(POINTS, separators=(",", ":"), ensure_ascii=False))
    .replace("__SERIES__", json.dumps(series, separators=(",", ":")))
    .replace("__SEVLABELS__", json.dumps(SEV_LABELS))
    .replace("__CIRCUIT_ROWS__", CIRCUIT_ROWS)
    .replace("__FUEL_CARDS__", FUEL_CARDS)
    .replace("__FWI_ROWS__", FWI_ROWS)
    .replace("__HIST_BARS__", HIST_BARS)
    .replace("__HIST_MID__", HIST_MID)
    .replace("__HIST_MAX__", HIST_MAX)
    .replace("__PLACES__", json.dumps(PLACES, separators=(",", ":"),
                                     ensure_ascii=False))
    .replace("__N_BURNED__", f"{n_burned:,}")
    .replace("__N_TOTAL__", f"{n_total:,}")
    .replace("__FOREST_HA__", f"{forest['burned_0.27_forest_ha']:,.0f}")
)

out = WEB / "index.html"
out.write_text(HTML)
nice = sum(1 for f in frames_meta if (100.0 - (f.get("nodata") or 0)) >= SEEN_MIN)
print(f"wrote {out}  {out.stat().st_size / 1e6:.2f} MB  [profile: {PROFILE}]")
print(f"  series: {nice} passes at {SERIES_GOOD[0]}px, "
      f"{len(series) - nice} near-empty at {SERIES_EMPTY[0]}px")
print(f"  {len(series)} series frames · {len(pts):,} points · {len(layer_uris)} layers")
print(f"  {n_burned:,}/{n_total:,} problems burned")
