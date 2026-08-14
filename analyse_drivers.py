#!/usr/bin/env python
"""What drove burn severity inside the Fontainebleau fire footprint?"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from bleau_fire import burn, drivers, ign, landcover, terrain
from bleau_fire.config import MASK_CLASSES, OUT_DIR, POST_SCENE, PRE_SCENE, build_grid
from bleau_fire.mask import valid_mask
from bleau_fire.scenes import read_scene, scene_path

grid = build_grid()
print(f"[grid] {grid.describe()}")

pre, pre_meta = read_scene(scene_path(PRE_SCENE))
post, post_meta = read_scene(scene_path(POST_SCENE))
nbr_pre = burn.nbr(pre["nir"], pre["swir22"])
delta = burn.dnbr(nbr_pre, burn.nbr(post["nir"], post["swir22"]))
ok = valid_mask(pre["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
    post["scl"].astype("uint8"), MASK_CLASSES)
footprint = landcover.fire_footprint(burn.burned_mask(delta) & ok)

print("\n[terrain] Copernicus GLO-30")
elev = terrain.elevation_on_grid(grid)
slope, north, east = terrain.slope_aspect(grid)
print(f"  elevation {np.nanmin(elev):.0f}-{np.nanmax(elev):.0f} m · "
      f"slope median {np.nanmedian(slope):.2f}° p99 {np.nanpercentile(slope, 99):.2f}°")

print("[access ] distance to the track and road network")
roads = ign.load("roads")
paths = ign.fetch_paths()
net = terrain.rasterise_lines(roads, grid) | terrain.rasterise_lines(paths, grid)
dist_track = terrain.distance_to(net, grid)
print(f"  network covers {net.mean() * 100:.2f}% of cells · "
      f"median distance {np.nanmedian(dist_track):.0f} m")

print("[fuel   ] BD Forêt V2")
forest_gdf = ign.load("bdforet")
fuel_cls, fuel_labels = landcover.rasterise(forest_gdf, grid, "tfv_g11")

predictors = {
    "elevation": elev,
    "slope": slope,
    "northness": north,
    "eastness": east,
    "dist_track": dist_track,
    # Pre-fire NBR is a fuel-condition proxy: greener, denser vegetation before the fire.
    # It is NOT derived from the post-fire scene, so it does not leak the outcome.
    "nbr_pre": nbr_pre,
    "fuel": fuel_cls.astype("float32"),
}

print("\n[sample]")
df = drivers.assemble(delta, footprint, ok, predictors, grid)
df["fuel"] = pd.Categorical(
    df["fuel"].astype(int).map({k: v for k, v in fuel_labels.items()}).fillna("none")
)
FEATURES = ["elevation", "slope", "northness", "eastness", "dist_track", "nbr_pre", "fuel"]
print(f"  {len(df):,} pixels · {df['block'].nunique()} spatial blocks of "
      f"{drivers.BLOCK_M:.0f} m · dNBR median {df['dnbr'].median():.3f}")

print("\n[cv] random vs spatially blocked — the gap is the point")
cv = drivers.compare_cv(df, FEATURES)
print(f"  random  k-fold R2 = {cv['r2_random_mean']:+.3f} ± {cv['r2_random_std']:.3f}")
print(f"  blocked k-fold R2 = {cv['r2_blocked_mean']:+.3f} ± {cv['r2_blocked_std']:.3f}")
print(f"  inflation from ignoring spatial structure: {cv['inflation']:+.3f}")
print(f"  blocked folds: {cv['folds_blocked']}")

print("\n[importance] permutation, blocked hold-out")
imp = drivers.importances(df, FEATURES)
print(imp.to_string(index=False))

print("\n[marginal] observed mean dNBR by predictor bin")
tables = {}
for col in ("fuel", "slope", "northness", "dist_track", "elevation", "nbr_pre"):
    t = drivers.partial_effects(df, col)
    t.columns = [col, "mean", "median", "n"]
    tables[col] = t.to_dict("records")
    print(f"\n  {col}:")
    print(t.to_string(index=False))

OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "drivers_summary.json").write_text(json.dumps({
    "pre": pre_meta["id"], "post": post_meta["id"],
    "n_pixels": int(len(df)), "n_blocks": int(df["block"].nunique()),
    "block_m": drivers.BLOCK_M, "cv": cv,
    "importance": imp.to_dict("records"), "marginal": tables,
}, indent=2, default=str))
imp.to_csv(OUT_DIR / "drivers_importance.csv", index=False)
print(f"\n[out] {OUT_DIR / 'drivers_summary.json'}")
