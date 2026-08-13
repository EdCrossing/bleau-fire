# bleau-fire

Burn severity of the **July 2026 Fontainebleau fire**, mapped onto the bouldering circuits it
crossed.

On 12 July 2026 a fire — later found to be arson — burned roughly **2,000 hectares** of the
Fontainebleau massif, about 10% of the forest and the most serious it has recorded since
record-keeping began in 1863. Two sectors were hit: Noisy-sur-École (~1,500 ha) and the
Faisanderie (~450 ha). Copernicus EMS activated as **EMSR894**.

Fontainebleau is also the most significant bouldering area in the world. This repo asks a
specific question rather than a general one:

> **Which boulders and circuits burned, and how badly?**

Not "how many hectares" — that number already exists. The point is a per-feature answer with
its provenance attached: *this problem, this severity, from these two scenes, acquired on these
dates.*

## Approach

| Step | What |
|---|---|
| Imagery | Sentinel-2 L2A via [Earth Search](https://earth-search.aws.element84.com/v1), anonymous, no tokens |
| Grid | EPSG:32631, 10 m, defined once in `config.py`; everything is forced onto it |
| Reads | Windowed `WarpedVRT` reads straight out of remote COGs — no full-granule downloads |
| Index | NBR = (B08 − B12)/(B08 + B12); dNBR pre − post; RdNBR relativised for mixed fuel |
| Severity | Key & Benson (2006) FIREMON classes — **to be validated against EMSR894, not asserted** |
| Climbing data | OpenStreetMap via Overpass (ODbL), ~1,150 features |

### The bi-temporal pair

| Role | Scene | Date | Scene cloud | **AOI usable** |
|---|---|---|---|---|
| Pre-fire | `S2A_31UDP_20260710_0_L2A` | 2026-07-10 | 7.0% | **99.6%** |
| Post-fire | `S2A_31UDP_20260720_0_L2A` | 2026-07-20 | 0.4% | **100.0%** |

Ten days apart, so phenology is effectively held constant and the difference is the fire rather
than the season. Both are Sentinel-2**A**, which removes inter-sensor calibration offsets from
a difference index rather than assuming they are negligible. The whole massif sits inside a
single MGRS granule (`31UDP`), so no mosaicking is needed.

**The pair was chosen on measured AOI usability, not on scene metadata** — see below.

### Scene-level cloud cover is not a usability metric

`run.py probe` reads one 20 m band per candidate and measures what the AOI actually contains.
Of 39 scenes from June to mid-August 2026, only **8 are ≥90% usable** over the massif, and
**7 advertise <15% cloud while being <50% usable**:

| Scene | Advertised cloud | Nodata over AOI |
|---|---|---|
| `S2C_31UDP_20260711_0_L2A` | 0.6% | **99.4%** |
| `S2C_31UDP_20260721_0_L2A` | 0.7% | **99.7%** |
| `S2C_31UDP_20260810_0_L2A` | 0.0% | **99.4%** |
| `S2A_31UDP_20260812_1_L2A` | 0.0% | **99.1%** |

These are **partial swaths**: granules clipped by the orbit edge that intersect the search bbox
while containing almost no data. Because cloud cover is computed over *valid* pixels, a nearly
empty granule reports nearly zero cloud — so the metadata ranks them as the best scenes
available. The 07-11 scene, one day before ignition at 0.6% cloud, looked like the ideal
pre-fire baseline and is empty.

This is the well-known "scene cloud describes the granule, not your AOI" problem with a sharper
edge on it: the failure is not that the metric is coarse, but that it is **inverted** — the
emptiest scenes look like the cleanest ones.

### Why OpenStreetMap and not a climbing app

OSM's Fontainebleau coverage carries individual problem start points with Font grades, circuit
colour and circuit number — and, importantly, the **`ref:bleau.info` cross-reference is already
in OSM**. The join key comes for free under an open licence, so nothing needs scraping and
nothing with unclear terms ends up in a public repo.

## Usage

```bash
uv sync
uv run python run.py climbing          # OSM features -> data/vectors/climbing.geojson
uv run python run.py scenes --pair     # pull the pre/post pair onto the grid
uv run python run.py dnbr              # indices, severity, hectares, renders
```

`data/` is gitignored.

## Things this repo tries not to get wrong

- **Fresh burn scars look like cloud shadow to SCL.** Both are dark in the visible and very
  dark in the NIR. A cloud mask applied naively can delete the burn scar — the exact pixels
  the project exists to measure. Scenes are therefore stored *unmasked* with SCL alongside, and
  `mask.scar_shadow_overlap()` quantifies the overlap rather than assuming it away.
- **dNBR favours dense fuel.** An absolute index reads open Calluna heath as less severely
  burned than Scots pine even where both burned completely, because there was less to lose.
  RdNBR is computed alongside for that reason, and the disagreement between them is
  informative.
- **Severity thresholds are borrowed, not derived.** Key & Benson's classes were calibrated
  largely on North American conifer forest. This is French mixed pine/oak/heath on sandstone.
  EMSR894 provides an independent grading of this exact fire to check them against.
- **OSM positions are contributed, not surveyed.** Metres of error is sub-pixel for a crag at
  10 m, but not negligible for a single boulder near a burn edge. That uncertainty belongs in
  the answer.

## Provenance

Grid, COG-reading and STAC-search code is adapted from [`eo-agent`](../eo-agent), vendored
rather than imported so this repo stands alone.

Data: Copernicus Sentinel-2 (ESA, free and open). OpenStreetMap contributors (ODbL).

## Results so far

Two burn scars are cleanly resolved and match the reported two-sector fire: a large one over
Trois Pignons / Noisy-sur-École, a smaller one north-east at the Faisanderie.

**27 named climbing features** at moderate-low severity or worse — 17 crags, 10 boulders.
Worst affected: Long Boyau (dNBR 0.69), Jean des Vignes (0.66), Rocher du Potala (0.55),
Rocher du Général (0.52), J.A. Martin (0.50). Roche aux Sabots and La Ségognole are in unburned
forest on the western edge.

Burned area: **1,136 ha** at dNBR ≥ 0.27, against EMS's reported ~2,000 ha. The ≥0.10 figure
(3,711 ha) is contaminated by agricultural harvest and should not be quoted — see `FINDINGS.md`
F2.

**A limitation that bounds the headline claim:** OSM's problem-level detail (the 724 features
carrying Font grades and circuit membership) exists in only three locations, all of them
unburned. The per-circuit answer therefore cannot be produced for the burned sectors from OSM
alone — see `FINDINGS.md` F3.

See [`FINDINGS.md`](FINDINGS.md) for the measured results log, including two self-corrections.

## Status

Early. Pipeline built end to end; severity numbers not yet validated against EMSR894, and area
totals not yet restricted to forest.
