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
from pathlib import Path

import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "data" / "web"
LAYERS = WEB / "layers"
OUT = ROOT / "data" / "out"
SERIES = OUT / "series"

# Series frames get their own budget: many small frames beat few large ones for a scrubber.
SERIES_WIDTH, SERIES_QUALITY = 950, 60


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

layer_uris = {}
for key, *_ in BASE_LAYERS + OVERLAYS:
    for ext in (".jpg", ".png"):
        p = LAYERS / f"{key}{ext}"
        if p.exists():
            layer_uris[key] = uri(p)
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
    small = SERIES / "web" / fm["file"]
    small.parent.mkdir(parents=True, exist_ok=True)
    if not small.exists():
        im = Image.open(src).convert("RGB")
        im = im.resize((SERIES_WIDTH, int(im.height * SERIES_WIDTH / im.width)), Image.LANCZOS)
        im.save(small, "JPEG", quality=SERIES_QUALITY, optimize=True, progressive=True)
    series.append({
        "date": fm["date"], "cloud": fm["cloud"],
        "valid": fm.get("valid"), "img": uri(small),
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
    "bounds": raw["bounds"], "areas": areas, "circuits": circuits, "grades": grades,
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

circ = pd.read_csv(OUT / "circuits_severity.csv").head(14)
CIRCUIT_ROWS = "\n".join(
    f'<tr><td class="nm">{html.escape(str(r["area_name"]))}</td>'
    f'<td><span class="dot" style="background:{r["circuit_color"]}"></span>'
    f'{html.escape(str(r["circuit_color"]))}</td>'
    f'<td class="num">{int(r["problems"])}</td>'
    f'<td class="num">{r["dnbr_median"]:.2f}</td>'
    f'<td class="num strong">{r["pct_burned"]:.0f}%</td>'
    f'<td class="bar"><span style="width:{r["pct_burned"]:.0f}%"></span></td></tr>'
    for _, r in circ.iterrows()
)

fuel = pd.read_csv(OUT / "severity_by_tfv_g11.csv")
FUEL_ROWS = "\n".join(
    f'<tr><td class="nm">{html.escape(r["class"])}</td>'
    f'<td class="num">{r["ha_in_fire"]:,.0f}</td>'
    f'<td class="num">{r["pct_burned"]:.1f}%</td>'
    f'<td class="num">{r["dnbr_median"]:.3f}</td>'
    f'<td class="num sub">{r["rdnbr_median"]:.3f}</td></tr>'
    for _, r in fuel.iterrows()
)

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
    .replace("__FUEL_ROWS__", FUEL_ROWS)
    .replace("__N_BURNED__", f"{n_burned:,}")
    .replace("__N_TOTAL__", f"{n_total:,}")
    .replace("__N_FRAMES__", str(len(series)))
    .replace("__FOREST_HA__", f"{forest['burned_0.27_forest_ha']:,.0f}")
)

out = WEB / "index.html"
out.write_text(HTML)
print(f"wrote {out}  {out.stat().st_size / 1e6:.2f} MB")
print(f"  {len(series)} series frames · {len(pts):,} points · {len(layer_uris)} layers")
print(f"  {n_burned:,}/{n_total:,} problems burned")
