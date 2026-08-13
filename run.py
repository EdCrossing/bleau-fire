#!/usr/bin/env python
"""CLI for the Fontainebleau burn-severity work.

    uv run python run.py climbing                 # OSM climbing features -> GeoJSON
    uv run python run.py scenes --pair            # just the pre/post pair
    uv run python run.py scenes --start 2026-05-01 --end 2026-08-13 --max-cloud 25
    uv run python run.py dnbr                     # indices, severity, renders
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from bleau_fire import burn, climbing, features, render
from bleau_fire.config import (
    AOI, MASK_CLASSES, OUT_DIR, POST_SCENE, PRE_SCENE, build_grid,
)
from bleau_fire.mask import scar_shadow_overlap, valid_mask
from bleau_fire.scenes import fetch_scene, probe_scene, read_scene, scene_path
from bleau_fire.search import search, summarise

# Trois Pignons — the dense bouldering core, inside the ~1,500 ha Noisy-sur-Ecole sector.
ZOOM_TROIS_PIGNONS = (2.495, 48.355, 2.560, 48.398)


def cmd_climbing(args):
    gdf = climbing.fetch(refresh=args.refresh)
    path = climbing.save(gdf)
    print(f"\n[out] {path}  ({len(gdf)} features)")
    print("\nby type:")
    for t, n in gdf["feature_type"].value_counts().items():
        print(f"  {t:<16} {n:>5}")
    graded = gdf["climbing:grade:fb"].notna().sum()
    circuits = gdf["climbing:circuit:colour"].notna().sum()
    refs = gdf["ref:bleau.info"].notna().sum()
    print(f"\nwith Font grade: {graded}   in a circuit: {circuits}   bleau.info ref: {refs}")


def cmd_scenes(args):
    grid = build_grid()
    print(f"[grid] {grid.describe()}")
    print(f"[aoi ] {AOI}\n")

    items = search(args.start, args.end, max_cloud=args.max_cloud, refresh=args.refresh)
    if args.pair:
        wanted = {PRE_SCENE, POST_SCENE}
        items = [i for i in items if i["id"] in wanted]
        missing = wanted - {i["id"] for i in items}
        if missing:
            raise SystemExit(f"pinned scene(s) not found in catalogue: {sorted(missing)}")
    print(summarise(items))
    print()

    for n, item in enumerate(items, 1):
        cached = scene_path(item["id"]).exists()
        _, meta = fetch_scene(item, grid, refresh=args.refresh)
        tag = "cached" if cached and not args.refresh else f"{meta['seconds']}s"
        print(f"  [{n}/{len(items)}] {item['id']}  cloud={meta['eo:cloud_cover']:5.1f}%  "
              f"{meta['size_mb']:6.1f} MB  {tag}")


def cmd_probe(args):
    """Measure what each candidate scene actually looks like over the AOI."""
    grid = build_grid()
    print(f"[grid] {grid.describe()}\n")
    items = search(args.start, args.end, max_cloud=args.max_cloud, refresh=args.refresh)

    results = []
    print(f"  {'date':<12} {'scene%':>7} {'nodata%':>8} {'valid%':>7}  id")
    for item in items:
        r = probe_scene(item, grid)
        results.append(r)
        flag = "  <- unusable" if r["aoi_valid_pct"] < 50 else ""
        print(f"  {r['datetime'][:10]:<12} {r['scene_cloud']:7.1f} "
              f"{r['aoi_nodata_pct']:8.2f} {r['aoi_valid_pct']:7.2f}  {r['id']}{flag}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "scene_quality.json"
    path.write_text(json.dumps(results, indent=2))

    usable = [r for r in results if r["aoi_valid_pct"] >= 90]
    print(f"\n[out] {path}")
    print(f"  {len(usable)}/{len(results)} scenes are >=90% usable over the AOI")
    disagree = [r for r in results if r["scene_cloud"] < 15 and r["aoi_valid_pct"] < 50]
    if disagree:
        print(f"  {len(disagree)} scene(s) advertise <15% cloud but are <50% usable here:")
        for r in disagree:
            print(f"    {r['id']}  cloud={r['scene_cloud']}%  "
                  f"nodata={r['aoi_nodata_pct']}%  valid={r['aoi_valid_pct']}%")


def cmd_dnbr(args):
    grid = build_grid()
    pixel_area = grid.resolution ** 2
    print(f"[grid] {grid.describe()}")

    pre_arrays, pre_meta = read_scene(scene_path(args.pre))
    post_arrays, post_meta = read_scene(scene_path(args.post))
    print(f"[pre ] {pre_meta['id']}  {pre_meta['datetime'][:10]}  "
          f"cloud={pre_meta['eo:cloud_cover']}%  offset={pre_meta['boa_offset']:+.0f}")
    print(f"[post] {post_meta['id']}  {post_meta['datetime'][:10]}  "
          f"cloud={post_meta['eo:cloud_cover']}%  offset={post_meta['boa_offset']:+.0f}")

    nbr_pre = burn.nbr(pre_arrays["nir"], pre_arrays["swir22"])
    nbr_post = burn.nbr(post_arrays["nir"], post_arrays["swir22"])
    delta = burn.dnbr(nbr_pre, nbr_post)
    rel = burn.rdnbr(nbr_pre, delta)

    # Mask only where *either* date is unusable — a pixel needs both to be differenced.
    ok = valid_mask(pre_arrays["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
        post_arrays["scl"].astype("uint8"), MASK_CLASSES
    )
    print(f"[mask] {100 * ok.mean():.1f}% of pixels usable on both dates")

    classes = burn.classify(delta)
    classes[~ok] = -1
    areas = burn.class_areas(classes, pixel_area)
    burned = burn.burned_mask(delta) & ok

    print("\n  severity class            hectares")
    for label, ha in areas.items():
        print(f"  {label:<26} {ha:>8.1f}")
    total = sum(ha for lab, ha in areas.items() if "severity" in lab)
    print(f"  {'TOTAL burned (>=0.10)':<26} {total:>8.1f}")
    print(f"  {'burned (>=0.27)':<26} {burned.sum() * pixel_area / 1e4:>8.1f}")

    # Does SCL mistake fresh char for cloud shadow? See mask.scar_shadow_overlap.
    overlap = scar_shadow_overlap(post_arrays["scl"].astype("uint8"), burned)
    print("\n  SCL classes inside the burn scar (post-fire scene):")
    for name, pct in sorted(overlap.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<24} {pct:>6.2f}%")

    gdf = climbing.load() if not args.no_overlay else None
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pre_date = pre_meta["datetime"][:10]
    post_date = post_meta["datetime"][:10]

    print("\n[render]")
    for name, zoom, lab in (("massif", None, 0), ("trois_pignons", ZOOM_TROIS_PIGNONS, 40)):
        jobs = (
            render.plot_rgb(pre_arrays, grid, OUT_DIR / f"rgb_pre_{name}.png", gdf=gdf,
                            zoom=zoom, label_top=lab,
                            title=f"{pre_meta['id']} — pre-fire {pre_date}"),
            render.plot_rgb(post_arrays, grid, OUT_DIR / f"rgb_post_{name}.png", gdf=gdf,
                            zoom=zoom, label_top=lab,
                            title=f"{post_meta['id']} — post-fire {post_date}"),
            render.plot_dnbr(delta, grid, OUT_DIR / f"dnbr_{name}.png", gdf=gdf, zoom=zoom,
                             title=f"dNBR  {pre_date} -> {post_date}"),
            render.plot_severity(classes, grid, OUT_DIR / f"severity_{name}.png", gdf=gdf,
                                 zoom=zoom,
                                 title="Burn severity (Key & Benson classes)"),
        )
        for path in jobs:
            print(f"  {path}")

    summary = {
        "pre": pre_meta, "post": post_meta,
        "usable_pixel_pct": round(float(ok.mean()) * 100, 2),
        "class_hectares": areas,
        "burned_ha_dnbr_0.27": round(float(burned.sum()) * pixel_area / 1e4, 1),
        "scl_inside_scar_pct": overlap,
        "dnbr_stats": {
            "p50": round(float(np.nanpercentile(delta, 50)), 4),
            "p99": round(float(np.nanpercentile(delta, 99)), 4),
            "max": round(float(np.nanmax(delta)), 4),
        },
        "rdnbr_p99": round(float(np.nanpercentile(rel, 99)), 4),
    }
    (OUT_DIR / "dnbr_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n[out] {OUT_DIR / 'dnbr_summary.json'}")


def cmd_sample(args):
    """Answer the actual question: which climbing features burned, and how badly."""
    grid = build_grid()
    pre_arrays, pre_meta = read_scene(scene_path(args.pre))
    post_arrays, post_meta = read_scene(scene_path(args.post))

    delta = burn.dnbr(
        burn.nbr(pre_arrays["nir"], pre_arrays["swir22"]),
        burn.nbr(post_arrays["nir"], post_arrays["swir22"]),
    )
    ok = valid_mask(pre_arrays["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
        post_arrays["scl"].astype("uint8"), MASK_CLASSES
    )

    gdf = climbing.load()
    df = features.sample(gdf, delta, grid, radius=args.radius, valid=ok)

    print(f"[pre ] {pre_meta['id']}  {pre_meta['datetime'][:10]}")
    print(f"[post] {post_meta['id']}  {post_meta['datetime'][:10]}")
    print(f"[win ] {2 * args.radius + 1}x{2 * args.radius + 1} px "
          f"({(2 * args.radius + 1) * grid.resolution:.0f} m) around each feature\n")
    print(features.summarise(df))

    roll = features.circuit_rollup(df)
    if len(roll):
        print("\nby circuit colour:")
        print(roll.to_string())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv = OUT_DIR / "climbing_severity.csv"
    keep = [c for c in df.columns if c != "geometry"]
    df[keep].to_csv(csv, index=False)
    gj = OUT_DIR / "climbing_severity.geojson"
    df.drop(columns=[c for c in ("edge",) if c in df]).assign(
        edge=df["edge"].astype("boolean")
    ).to_file(gj, driver="GeoJSON")
    print(f"\n[out] {csv}\n[out] {gj}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("climbing", help="fetch OSM climbing features")
    c.add_argument("--refresh", action="store_true")
    c.set_defaults(func=cmd_climbing)

    s = sub.add_parser("scenes", help="pull Sentinel-2 scenes onto the grid")
    s.add_argument("--start", default="2026-05-01")
    s.add_argument("--end", default="2026-08-13")
    s.add_argument("--max-cloud", type=float, default=80.0)
    s.add_argument("--pair", action="store_true", help="only the pinned pre/post pair")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_scenes)

    q = sub.add_parser("probe", help="measure AOI-level usability of candidate scenes")
    q.add_argument("--start", default="2026-05-01")
    q.add_argument("--end", default="2026-08-13")
    q.add_argument("--max-cloud", type=float, default=80.0)
    q.add_argument("--refresh", action="store_true")
    q.set_defaults(func=cmd_probe)

    d = sub.add_parser("dnbr", help="compute indices, severity and renders")
    d.add_argument("--pre", default=PRE_SCENE)
    d.add_argument("--post", default=POST_SCENE)
    d.add_argument("--no-overlay", action="store_true")
    d.set_defaults(func=cmd_dnbr)

    f = sub.add_parser("sample", help="per-feature burn severity for climbing features")
    f.add_argument("--pre", default=PRE_SCENE)
    f.add_argument("--post", default=POST_SCENE)
    f.add_argument("--radius", type=int, default=2,
                   help="sampling window radius in pixels (default 2 -> 50 m)")
    f.set_defaults(func=cmd_sample)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
