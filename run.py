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

from bleau_fire import boulders, burn, climbing, features, ign, landcover, render
from bleau_fire.config import (
    AOI, MASK_CLASSES, OUT_DIR, POST_SCENE, PRE_SCENE, build_grid,
)
from bleau_fire.mask import scar_shadow_overlap, valid_mask
from bleau_fire.raster import read_on_grid, to_reflectance
from bleau_fire.scenes import fetch_scene, probe_scene, read_scene, scene_path
from bleau_fire.search import boa_offset, search, summarise

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


def cmd_problems(args):
    """Per-problem and per-circuit burn severity from Boolder's 19k-problem database."""
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

    gdf = boulders.clip(boulders.load(), AOI)
    print(f"[data] {len(gdf)} Boolder problems inside the AOI "
          f"({gdf['area_name'].nunique()} areas, {gdf['circuit_color'].notna().sum()} in circuits)")

    df = features.sample(gdf, delta, grid, radius=args.radius, valid=ok)
    burned = df[df["dnbr_median"] >= 0.27]
    print(f"[burn] {len(burned)} of {int(df['dnbr_median'].notna().sum())} problems "
          f"at moderate-low severity or worse "
          f"({100 * len(burned) / max(int(df['dnbr_median'].notna().sum()), 1):.1f}%)")
    print(f"[unc ] {int(df['edge'].fillna(False).sum())} unresolved at the burn edge")

    areas = boulders.area_rollup(df)
    print("\n  worst-hit areas (>=10 problems):")
    print(areas.head(18).to_string(index=False))

    circ = boulders.circuit_rollup(df)
    print(f"\n  worst-hit circuits (>=5 problems), {len(circ)} circuits total:")
    print(circ.head(18).to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    keep = [c for c in df.columns if c != "geometry"]
    df[keep].to_csv(OUT_DIR / "problems_severity.csv", index=False)
    areas.to_csv(OUT_DIR / "areas_severity.csv", index=False)
    circ.to_csv(OUT_DIR / "circuits_severity.csv", index=False)
    print(f"\n[out] {OUT_DIR / 'problems_severity.csv'} and area/circuit rollups")


def cmd_quicklooks(args):
    """Render every pass in the window, cloud and all, as a consistent-stretch JPEG series.

    Deliberately includes unusable scenes. A scrubbable series where some frames are solid
    cloud and some are half-empty swath tells you more about what working with optical EO is
    actually like than a curated set of eight clear days.
    """
    from PIL import Image

    grid = build_grid(resolution=args.resolution)
    print(f"[grid] {grid.describe()}")
    items = search(args.start, args.end, max_cloud=100.0)
    qdir = OUT_DIR / "series"
    qdir.mkdir(parents=True, exist_ok=True)

    quality = {q["id"]: q for q in json.loads((OUT_DIR / "scene_quality.json").read_text())} \
        if (OUT_DIR / "scene_quality.json").exists() else {}

    frames = []
    for n, item in enumerate(items, 1):
        out = qdir / f"{item['properties']['datetime'][:10]}_{item['id']}.jpg"
        if not out.exists() or args.refresh:
            assets = item["assets"]
            offset = boa_offset(item)
            try:
                rgb = np.stack([
                    to_reflectance(read_on_grid(assets[b]["href"], grid), offset)
                    for b in ("red", "green", "blue")
                ], axis=-1)
            except Exception as exc:
                print(f"  [{n}/{len(items)}] {item['id']}: {type(exc).__name__}, skipped")
                continue
            # Fixed stretch, NOT per-scene percentiles. A per-frame stretch makes the series
            # flicker and hides exactly the thing worth seeing — that some passes are cloud.
            img = np.clip(np.nan_to_num(rgb) / 0.30, 0, 1)
            Image.fromarray((img * 255).astype("uint8")).save(
                out, "JPEG", quality=78, optimize=True
            )
        q = quality.get(item["id"], {})
        frames.append({
            "id": item["id"], "date": item["properties"]["datetime"][:10],
            "file": out.name,
            "cloud": round(float(item["properties"].get("eo:cloud_cover", 0)), 1),
            "valid": q.get("aoi_valid_pct"), "nodata": q.get("aoi_nodata_pct"),
        })
        print(f"  [{n}/{len(items)}] {frames[-1]['date']}  cloud={frames[-1]['cloud']:5.1f}%  "
              f"valid={frames[-1]['valid']}  {out.name}")

    (qdir / "frames.json").write_text(json.dumps(frames, indent=2))
    print(f"\n[out] {len(frames)} frames -> {qdir}")


def cmd_vectors(args):
    """Fetch the IGN land-cover layers and the OSM walking network."""
    for key in ("bdforet", "rpg", "roads"):
        try:
            g = ign.fetch_layer(key, refresh=args.refresh)
            print(f"  -> {key}: {len(g)} features")
        except Exception as exc:
            print(f"  !! {key}: {type(exc).__name__}: {exc}")
    try:
        paths = ign.fetch_paths(refresh=args.refresh)
        print(f"  -> paths: {len(paths)} ways")
    except Exception as exc:
        print(f"  !! paths: {type(exc).__name__}: {exc}")


def cmd_forest(args):
    """Cross burn severity against BD Foret fuel types and RPG farmland."""
    grid = build_grid()
    pixel_area = grid.resolution ** 2
    pre_arrays, pre_meta = read_scene(scene_path(args.pre))
    post_arrays, post_meta = read_scene(scene_path(args.post))

    nbr_pre = burn.nbr(pre_arrays["nir"], pre_arrays["swir22"])
    delta = burn.dnbr(nbr_pre, burn.nbr(post_arrays["nir"], post_arrays["swir22"]))
    rel = burn.rdnbr(nbr_pre, delta)
    ok = valid_mask(pre_arrays["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
        post_arrays["scl"].astype("uint8"), MASK_CLASSES
    )

    forest_gdf = ign.load("bdforet")
    rpg_gdf = ign.load("rpg")
    forest = landcover.mask_from(forest_gdf, grid)
    agri = landcover.mask_from(rpg_gdf, grid)

    burned_all = burn.burned_mask(delta) & ok
    low_all = (np.nan_to_num(delta, nan=0.0) >= 0.10) & ok

    def ha(m):
        return float(m.sum()) * pixel_area / 1e4

    print(f"[cover] forest {ha(forest):,.0f} ha · farmland {ha(agri):,.0f} ha · "
          f"overlap {ha(forest & agri):,.0f} ha\n")

    print("  F2 — where does the 'burned' area actually fall?")
    print(f"  {'threshold':<12} {'total':>10} {'in forest':>11} {'on farmland':>13} {'neither':>10}")
    for name, m in (("dNBR>=0.10", low_all), ("dNBR>=0.27", burned_all)):
        print(f"  {name:<12} {ha(m):>10,.0f} {ha(m & forest):>11,.0f} "
              f"{ha(m & ~forest & agri):>13,.0f} {ha(m & ~forest & ~agri):>10,.0f}")

    footprint = landcover.fire_footprint(burned_all)
    print(f"\n[fire ] footprint {ha(footprint):,.0f} ha "
          f"(forest {ha(footprint & forest):,.0f} ha)")
    print(f"[fire ] burned >=0.27 inside footprint and forest: "
          f"{ha(burned_all & footprint & forest):,.0f} ha")

    for field in ("tfv_g11", "essence"):
        classes, labels = landcover.rasterise(forest_gdf, grid, field)
        df = landcover.severity_by_class(
            classes, labels, delta, rel, footprint & ok, pixel_area
        )
        print(f"\n  severity by {field} (inside the fire footprint only):")
        print(df.to_string(index=False))
        df.to_csv(OUT_DIR / f"severity_by_{field}.csv", index=False)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "pre": pre_meta["id"], "post": post_meta["id"],
        "forest_ha": round(ha(forest), 1), "farmland_ha": round(ha(agri), 1),
        "burned_0.27_total_ha": round(ha(burned_all), 1),
        "burned_0.27_forest_ha": round(ha(burned_all & forest), 1),
        "burned_0.10_total_ha": round(ha(low_all), 1),
        "burned_0.10_on_farmland_ha": round(ha(low_all & ~forest & agri), 1),
        "fire_footprint_ha": round(ha(footprint), 1),
        "burned_in_footprint_forest_ha": round(ha(burned_all & footprint & forest), 1),
    }
    (OUT_DIR / "forest_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n[out] {OUT_DIR / 'forest_summary.json'}")

    classes, labels = landcover.rasterise(forest_gdf, grid, "tfv_g11")
    paths = ign.fetch_paths()
    gdf = climbing.load()
    print("\n[render]")
    for name, zoom in (("massif", None), ("trois_pignons", ZOOM_TROIS_PIGNONS)):
        p = render.plot_landcover(
            classes, labels, grid, OUT_DIR / f"fuel_{name}.png",
            footprint=footprint, paths=paths, gdf=gdf, zoom=zoom,
            title="BD Forêt fuel type, fire footprint and path network",
        )
        print(f"  {p}")


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

    pr = sub.add_parser("problems", help="per-problem/circuit severity from Boolder data")
    pr.add_argument("--pre", default=PRE_SCENE)
    pr.add_argument("--post", default=POST_SCENE)
    pr.add_argument("--radius", type=int, default=2)
    pr.set_defaults(func=cmd_problems)

    ql = sub.add_parser("quicklooks", help="render every pass as a scrubbable JPEG series")
    ql.add_argument("--start", default="2026-04-01")
    ql.add_argument("--end", default="2026-08-13")
    ql.add_argument("--resolution", type=float, default=20.0)
    ql.add_argument("--refresh", action="store_true")
    ql.set_defaults(func=cmd_quicklooks)

    v = sub.add_parser("vectors", help="fetch IGN land-cover layers and OSM paths")
    v.add_argument("--refresh", action="store_true")
    v.set_defaults(func=cmd_vectors)

    fo = sub.add_parser("forest", help="cross burn severity against fuel type and farmland")
    fo.add_argument("--pre", default=PRE_SCENE)
    fo.add_argument("--post", default=POST_SCENE)
    fo.set_defaults(func=cmd_forest)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
