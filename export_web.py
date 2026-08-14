#!/usr/bin/env python
"""Export every layer as a bare, exactly-georeferenced image plus vector data for the viewer."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bleau_fire import boulders, burn, features, ign, landcover, webexport
from bleau_fire.config import AOI, MASK_CLASSES, OUT_DIR, POST_SCENE, PRE_SCENE, build_grid
from bleau_fire.mask import valid_mask
from bleau_fire.render import FUEL_COLOURS
from bleau_fire.scenes import read_scene, scene_path

WEB = Path(__file__).resolve().parent / "data" / "web"
LAYERS = WEB / "layers"

grid = build_grid()
bounds = webexport.grid_bounds_wgs84(grid)
print(f"[grid] {grid.describe()}\n[bbox] {bounds}")

pre_arrays, pre_meta = read_scene(scene_path(PRE_SCENE))
post_arrays, post_meta = read_scene(scene_path(POST_SCENE))

nbr_pre = burn.nbr(pre_arrays["nir"], pre_arrays["swir22"])
delta = burn.dnbr(nbr_pre, burn.nbr(post_arrays["nir"], post_arrays["swir22"]))
ok = valid_mask(pre_arrays["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
    post_arrays["scl"].astype("uint8"), MASK_CLASSES
)

# Resolution is spent where zoom actually goes. The two Sentinel-2 scenes are what people
# zoom into to look at individual boulders, so they get close to the native 2,978 px grid;
# the historic layers are context and are natively coarse anyway.
W_SCENE, W_ORTHO, W_HIST, W_OVERLAY = 3000, 2400, 1700, 1900

print("\n[base layers]")
print(" ", webexport.write_rgb(pre_arrays, LAYERS / "rgb_pre.jpg", width=W_SCENE))
print(" ", webexport.write_rgb(post_arrays, LAYERS / "rgb_post.jpg", width=W_SCENE))

print("\n[overlays]")
# Transparent below 0.10 so the layer annotates the imagery instead of replacing it.
print(" ", webexport.write_colormapped(delta, LAYERS / "dnbr.png", width=W_OVERLAY,
                                       vmin=-0.1, vmax=0.9, alpha_below=0.10))
classes = burn.classify(delta)
classes[~ok] = -1
print(" ", webexport.write_classes(classes, [burn.CLASS_COLOURS[c] for c in burn.CLASS_LABELS],
                                   LAYERS / "severity.png", width=W_OVERLAY,
                                   transparent_below=3))

forest_gdf = ign.load("bdforet")
fuel_classes, fuel_labels = landcover.rasterise(forest_gdf, grid, "tfv_g11")
present = [fuel_labels[c] for c in sorted(fuel_labels)]
print(" ", webexport.write_classes(
    fuel_classes, [FUEL_COLOURS.get(lb, "#B9B2A4") for lb in present], LAYERS / "fuel.png",
    width=W_OVERLAY))

burned = burn.burned_mask(delta) & ok
footprint = landcover.fire_footprint(burned)
print(" ", webexport.write_classes(footprint.astype("int16"), ["#00000000", "#B0180A"],
                                   LAYERS / "footprint.png", width=W_OVERLAY,
                                   transparent_below=1))

print("\n[historic — IGN WMS]")
for key, layer in webexport.HISTORIC.items():
    try:
        w = W_ORTHO if key == "ortho_now" else W_HIST
        p = webexport.fetch_wms(layer, LAYERS / f"{key}.jpg", width=w, refresh=True)
        print(f"  {key:<11} {p.stat().st_size / 1e6:5.2f} MB  {layer}")
    except Exception as exc:
        print(f"  !! {key}: {type(exc).__name__}: {exc}")

print("\n[vectors]")
gdf = boulders.clip(boulders.load(), AOI)
df = features.sample(gdf, delta, grid, radius=2, valid=ok)
pts = df[df["dnbr_median"].notna()].copy()
# Compact payload: rounded coords and a class index rather than a label string per point.
sev_idx = {lb: i for i, lb in enumerate(burn.CLASS_LABELS)}
payload = {
    "bounds": bounds,
    "fuel_labels": present,
    "fuel_colours": [FUEL_COLOURS.get(lb, "#B9B2A4") for lb in present],
    "cols": ["lon", "lat", "name", "grade", "area", "circuit", "num", "dnbr", "sev", "edge"],
    "points": [
        [round(float(r.longitude), 5), round(float(r.latitude), 5), r["name"], r.grade,
         r.area_name, r.circuit_color, r.circuit_number,
         round(float(r.dnbr_median), 3), sev_idx.get(r.severity, 0), int(bool(r.edge))]
        for _, r in pts.iterrows()
    ],
}
WEB.mkdir(parents=True, exist_ok=True)
(WEB / "points.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
print(f"  {len(payload['points'])} problems -> points.json "
      f"({(WEB / 'points.json').stat().st_size / 1e6:.2f} MB)")

total = sum(p.stat().st_size for p in LAYERS.glob("*")) + (WEB / "points.json").stat().st_size
print(f"\n[total] {total / 1e6:.2f} MB of layers + vectors")
