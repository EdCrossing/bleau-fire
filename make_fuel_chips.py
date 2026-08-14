#!/usr/bin/env python
"""Cut one pre/post true-colour chip per BD Forêt fuel type, for the web page.

The "severity by fuel type" table gives numbers per class and no sense of what the classes
*are*. A reader who has never seen a Fontainebleau pine stand cannot tell why closed conifer
and heath behave differently. So each row gets a 640 m image chip of that fuel, cut from our
own Sentinel-2 pair — the same two scenes every statistic in the page comes from.

Three decisions worth stating, because each of them could be made wrongly and still produce a
picture that looks fine:

1. **Fixed stretch, not per-chip percentiles.** Reflectance / 0.30, clipped — identical to
   `webexport.write_rgb`. ⚠️ A per-chip percentile stretch would renormalise the post-fire chip
   and hide the darkening the section exists to show. The two chips must share one radiometric
   scale or the comparison is theatre.

2. **Chips are cut inside the fire footprint** wherever the class has a patch large enough.
   The largest patch of closed conifer in the massif is nowhere near the flames, and a pre/post
   pair from there would be two identical pictures captioned as a fire.

3. **Purity is measured on the window actually cut**, not asserted. A class centroid can easily
   land in a hole in its own polygon (the centroid of a horseshoe is outside it), so the
   centroid is a first guess that gets verified and, if it fails, replaced by the pixel of the
   same connected component whose window is purest.

Run:  uv run python make_fuel_chips.py
Out:  data/web/fuel_chips/<slug>_{pre,post}.jpg  and  data/web/fuel_meta.json
"""

from __future__ import annotations

import json
import unicodedata

import numpy as np
import pandas as pd
from PIL import Image
from pyproj import Transformer
from scipy import ndimage

from bleau_fire import burn, ign, landcover
from bleau_fire.config import (
    DATA,
    MASK_CLASSES,
    OUT_DIR,
    POST_SCENE,
    PRE_SCENE,
    build_grid,
)
from bleau_fire.mask import valid_mask
from bleau_fire.scenes import read_scene, scene_path

# Preferred chip width in grid pixels, then the fallbacks. 64 px @ 10 m = 640 m across.
# ⚠️ The open and heath classes are genuinely small and fragmented — heath holds only 58 ha
# inside the fire — so no 640 m window anywhere in the massif is 70% heath. Shrinking the
# window rather than lowering the purity bar keeps the chip an honest picture of the class;
# the ground width is recorded per chip so the page can say what it is showing.
CHIP_SIZES: tuple[int, ...] = (64, 48, 32, 24)
DISPLAY_PX = 128      # upscaled so the chip is legible on the page
JPEG_QUALITY = 80
MIN_PURITY = 0.70     # fraction of the window that must be the target class
STRETCH = 0.30        # reflectance value mapped to white; see webexport.write_rgb
SIZE_BUDGET_KB = 400  # the chips get inlined into a size-constrained page

CHIP_DIR = DATA / "web" / "fuel_chips"
META_PATH = DATA / "web" / "fuel_meta.json"

# Written from the classes actually present in severity_by_tfv_g11.csv. Every figure quoted
# is one this project measured (see data/out/severity_by_tfv_g11.csv and FINDINGS.md F5);
# nothing here is inferred from the literature.
DESCRIPTIONS: dict[str, str] = {
    "Forêt fermée conifères": (
        "Closed-canopy Scots pine on sand, over a deep bed of resinous needle litter. The "
        "most extensive fuel inside the fire at 746 ha, yet the lowest median dNBR of any "
        "class (0.319)."
    ),
    "Forêt fermée feuillus": (
        "Closed broadleaf canopy — oak, beech and hornbeam — holding shade and moisture at "
        "ground level. Beech stands came through least affected of any fuel in the fire."
    ),
    "Forêt fermée mixte": (
        "Closed canopy mixing pine with oak and beech: the conifers supply the flammable "
        "crowns, the broadleaves the damp understorey. 389 ha of it lay inside the fire."
    ),
    "Forêt ouverte conifères": (
        "Open pine, its crowns separated by heather, bracken and bare sand. Sun and wind "
        "reach the surface fuels directly and dry the litter between the trees."
    ),
    "Forêt ouverte mixte": (
        "Open mixed woodland: scattered pine and broadleaf over heath and grass. The broken "
        "canopy leaves fine surface fuels dry, and two thirds of it burned."
    ),
    "Lande": (
        "Heath — Calluna dwarf-shrub on open sandstone and sand. Fine, continuous, resinous "
        "fuel that burns almost entirely: 95.2% of its area, and the highest dNBR (0.486)."
    ),
}


def slugify(name: str) -> str:
    """Filesystem-safe ASCII slug. Accents are stripped, not transliterated by locale.

    ⚠️ NFKD then ASCII-drop, so 'Forêt fermée conifères' -> 'foret_fermee_coniferes' on any
    machine. Encoding a bare UTF-8 filename would work here and break on the first Windows
    checkout or naive web server.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    return "_".join(ascii_name.lower().split()).replace("/", "_")


def window_purity(mask: np.ndarray, size: int) -> np.ndarray:
    """Fraction of each `size`-wide window that is True, as a map over window centres.

    A separable box filter, so this is O(n) rather than O(n·size²) — it evaluates every
    possible chip position at once, which is what makes "pick the purest spot" cheap.
    ⚠️ `uniform_filter` with an even size is offset by half a pixel; that is under the
    resolution of the decision, and the purity finally reported is recomputed exactly on the
    window that is actually cut.
    """
    return ndimage.uniform_filter(mask.astype("float32"), size=size, mode="constant")


def largest_component(mask: np.ndarray) -> np.ndarray:
    """The biggest 8-connected blob in `mask`. Empty mask -> empty result."""
    labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return np.zeros_like(mask, dtype=bool)
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    return labels == (int(np.argmax(sizes)) + 1)


def clamp_window(row: int, col: int, shape: tuple[int, int], size: int) -> tuple[int, int]:
    """Top-left corner of a `size` window centred on (row, col), shifted to stay in bounds."""
    r = int(np.clip(row - size // 2, 0, shape[0] - size))
    c = int(np.clip(col - size // 2, 0, shape[1] - size))
    return r, c


def pick_at_size(
    class_mask: np.ndarray, burned: np.ndarray, size: int
) -> tuple[tuple[int, int], float, str]:
    """Choose a chip centre for one class at one window size: centroid, then verify.

    Returns ((row, col), purity, how) where `how` records which rule produced the answer, so
    the summary can show when the centroid was good enough and when it had to be rescued.

    ⚠️ The rescue ranks candidates by *burned* fraction, not by purity, among the windows that
    already clear the purity bar. Ranking on purity alone put the open-conifer chip on a
    hectare of bare sand — legitimately inside the polygon, 70% pure, and identical before and
    after, because sand does not burn. Purity is the constraint; showing the fire is the goal.
    """
    comp = largest_component(class_mask)
    if not comp.any():
        return (-1, -1), 0.0, "none"

    purity = window_purity(class_mask, size)
    rows, cols = np.nonzero(comp)
    cy, cx = int(round(rows.mean())), int(round(cols.mean()))

    # The centroid is only valid as a chip centre if it is inside the patch *and* its window
    # is mostly this class — a crescent-shaped stand fails both.
    if comp[cy, cx] and purity[cy, cx] >= MIN_PURITY:
        return (cy, cx), float(purity[cy, cx]), "centroid"

    eligible = comp & (purity >= MIN_PURITY)
    if eligible.any():
        score, how = window_purity(burned, size), "burnt-in-patch"
    else:
        score, how = purity, "purest-in-patch"
        eligible = comp

    best = int(np.argmax(np.where(eligible, score, -1.0)))
    by, bx = np.unravel_index(best, purity.shape)
    return (int(by), int(bx)), float(purity[by, bx]), how


def pick_location(
    class_mask: np.ndarray, burned: np.ndarray
) -> tuple[tuple[int, int], int, float, str]:
    """Largest chip window that still clears the purity bar for this class.

    Widest first, so a big homogeneous stand is shown at full 640 m and only a fragmented
    class pays the resolution cost. If nothing clears the bar the best attempt at the
    narrowest size is returned and flagged by the caller, rather than silently dropped.
    """
    best: tuple[tuple[int, int], int, float, str] | None = None
    for size in CHIP_SIZES:
        (r, c), purity, how = pick_at_size(class_mask, burned, size)
        if r < 0:
            return (-1, -1), size, 0.0, "none"
        if purity >= MIN_PURITY:
            return (r, c), size, purity, how
        if best is None or purity > best[2]:
            best = ((r, c), size, purity, how)
    assert best is not None
    return best


def cut_chip(arrays: dict[str, np.ndarray], r0: int, c0: int, size: int) -> Image.Image:
    """True-colour chip on the project's fixed stretch, upscaled for display.

    LANCZOS on the 2x upscale to match `webexport._resize`; the alternative, nearest, would
    hand back visible 10 m blocks and cost more JPEG bytes for no extra information.
    """
    sl = (slice(r0, r0 + size), slice(c0, c0 + size))
    rgb = np.stack([arrays["red"][sl], arrays["green"][sl], arrays["blue"][sl]], axis=-1)
    img = np.clip(np.nan_to_num(rgb) / STRETCH, 0, 1)
    return Image.fromarray((img * 255).astype("uint8")).resize(
        (DISPLAY_PX, DISPLAY_PX), Image.LANCZOS
    )


def main() -> None:
    grid = build_grid()
    print(f"[grid ] {grid.describe()}")

    pre, pre_meta = read_scene(scene_path(PRE_SCENE))
    post, post_meta = read_scene(scene_path(POST_SCENE))
    print(f"[scene] pre {pre_meta['id']} · post {post_meta['id']}")

    # The footprint, rebuilt exactly as run.py's `forest` command builds it, so chips come
    # from the same fire the table describes.
    nbr_pre = burn.nbr(pre["nir"], pre["swir22"])
    delta = burn.dnbr(nbr_pre, burn.nbr(post["nir"], post["swir22"]))
    ok = valid_mask(pre["scl"].astype("uint8"), MASK_CLASSES) & valid_mask(
        post["scl"].astype("uint8"), MASK_CLASSES
    )
    burned = burn.burned_mask(delta) & ok
    footprint = landcover.fire_footprint(burned)
    print(f"[fire ] footprint {footprint.sum() * grid.resolution ** 2 / 1e4:,.0f} ha")

    forest_gdf = ign.load("bdforet")
    classes, labels = landcover.rasterise(forest_gdf, grid, "tfv_g11")
    code_of = {label: code for code, label in labels.items()}

    wanted = pd.read_csv(OUT_DIR / "severity_by_tfv_g11.csv")["class"].tolist()
    missing_desc = [c for c in wanted if c not in DESCRIPTIONS]
    if missing_desc:
        raise KeyError(f"no description written for {missing_desc}")

    CHIP_DIR.mkdir(parents=True, exist_ok=True)
    to_lonlat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)

    meta: dict[str, dict] = {}
    rows = []
    for name in wanted:
        code = code_of[name]
        # Restricted to the fire footprint on purpose: the largest closed-conifer stand in
        # the massif is kilometres from the flames, and a pre/post pair cut there would be
        # two identical pictures presented as evidence of a fire.
        (r, c), size, purity, how = pick_location((classes == code) & footprint, burned)
        if r < 0:
            print(f"  !! {name}: no patch of this class inside the footprint, skipped")
            continue

        r0, c0 = clamp_window(r, c, classes.shape, size)
        # Exact purity of the window actually cut, after clamping to the grid edge.
        exact = float((classes[r0:r0 + size, c0:c0 + size] == code).mean())

        slug = slugify(name)
        paths = {}
        for tag, arrays in (("pre", pre), ("post", post)):
            out = CHIP_DIR / f"{slug}_{tag}.jpg"
            cut_chip(arrays, r0, c0, size).save(
                out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
            )
            paths[tag] = out

        x, y = (grid.transform * (c0 + size / 2, r0 + size / 2))
        lon, lat = to_lonlat.transform(x, y)

        meta[name] = {
            "description": DESCRIPTIONS[name],
            "slug": slug,
            "pre": f"fuel_chips/{slug}_pre.jpg",
            "post": f"fuel_chips/{slug}_post.jpg",
            "width_m": int(size * grid.resolution),
            "purity": round(exact, 3),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
        }
        rows.append({
            "class": name,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "purity": exact,
            "width_m": int(size * grid.resolution),
            "how": how,
            "pre_kb": paths["pre"].stat().st_size / 1024,
            "post_kb": paths["post"].stat().st_size / 1024,
        })

    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  {'class':<24} {'lat':>9} {'lon':>8} {'purity':>7} {'chip':>7} "
          f"{'picked':>16} {'pre KB':>7} {'post KB':>8}")
    for row in rows:
        flag = " " if row["purity"] >= MIN_PURITY else "!"
        print(f"{flag} {row['class']:<24} {row['lat']:>9.5f} {row['lon']:>8.5f} "
              f"{row['purity'] * 100:>6.1f}% {row['width_m']:>5} m {row['how']:>16} "
              f"{row['pre_kb']:>7.1f} {row['post_kb']:>8.1f}")

    total_kb = sum(p.stat().st_size for p in CHIP_DIR.glob("*.jpg")) / 1024
    n_files = len(list(CHIP_DIR.glob("*.jpg")))
    verdict = "OK" if total_kb < SIZE_BUDGET_KB else "OVER BUDGET"
    print(f"\n[size ] {n_files} chips, {total_kb:.1f} KB total "
          f"(budget {SIZE_BUDGET_KB} KB) — {verdict}")
    print(f"[out  ] {CHIP_DIR}")
    print(f"[out  ] {META_PATH}")


if __name__ == "__main__":
    main()
