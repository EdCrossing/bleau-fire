# bleau-fire

**New here?** [Where every layer comes from](DATA_SOURCES.md) — a plain-language guide to the data, with links you can click and browse. No code required.

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
| Climbing data | [Boolder](https://github.com/boolder-org/boolder-data) (CC BY 4.0), 17,605 problems in the area |
| Land cover | IGN BD Forêt V2, RPG farmland, ancient woodland, historic aerial (Etalab 2.0) |
| Weather | ERA5 via Open-Meteo; Fire Weather Index computed here, not downloaded |
| Atmosphere | Sentinel-5P aerosol index, CO and layer height |

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

### Why Boolder, and not a climbing app's own data

OpenStreetMap was the first source, and it failed for a specific reason recorded as F3: its
724 problem-level features sat in just three locations, all of them unburned. "No circuits
burned" would have described OSM's mapping effort, not the fire.

[Boolder](https://github.com/boolder-org/boolder-data) publishes the database behind its
Fontainebleau apps as SQLite under **CC BY 4.0** — 19,137 problems with grade, circuit colour
and circuit number, covering the burned sectors. Not UKC or 27 Crags: their terms prohibit
scraping, and a paid subscription grants access rather than redistribution rights.

## Usage

```bash
uv sync
uv run python run.py probe             # measure what each candidate scene actually contains
uv run python run.py scenes --pair     # pull the pre/post pair onto the grid
uv run python run.py dnbr              # indices, severity, hectares, renders
uv run python run.py vectors           # IGN land cover + OSM paths
uv run python run.py forest            # severity by fuel type; farmland contamination
uv run python run.py problems          # per-problem and per-circuit severity
uv run python run.py quicklooks        # every satellite pass as a frame series

uv run python analyse_weather.py       # Fire Weather Index, 1940-2026
uv run python analyse_drivers.py       # what predicts severity (answer: nothing that transfers)
uv run python analyse_plume.py         # Sentinel-5P smoke detection
uv run python export_web.py            # georeferenced layers for the viewer
uv run python build_page.py --full     # build the site
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

Every data source, with licence and a browsable link, is listed in
[`DATA_SOURCES.md`](DATA_SOURCES.md).

## Results

**1,153 of 17,605 bouldering problems** sit at moderate-low severity or worse. Rocher du
Général's yellow circuit and Rocher Guichot's orange are 100% burned; every Apremont sector is
untouched.

**994 ha of forest** burned at dNBR ≥ 0.27. The ≥ 0.10 figure of 3,711 ha is **half farmland
harvest** and should not be quoted — see F2.

**The two days this fire burned rank 1st and 2nd for fire weather in the entire 1940–2026
record.** Summer 2026 was the most extreme fire-weather summer of the 87 on record.

**Nothing predicts severity within the fire.** Terrain, fuel and access give a random-CV R² of
+0.392 and a spatially blocked R² of −0.185 — worse than predicting the mean. The random score
was measuring spatial autocorrelation.

**Sentinel-5P detected the plume** 18 km west of the fire, matching the wind direction to 25°.

See [`FINDINGS.md`](FINDINGS.md) for the full log — ten entries, five of which correct earlier
claims made in this repo.

## Status

Working end to end, with an interactive map at
**[edcrossing.github.io/bleau-fire](https://edcrossing.github.io/bleau-fire/)**.

Open: severity classes are not yet validated against the EMSR894 expert grading; the
substrate-versus-severity question in F5 needs soil-robust indices (MIRBI, BAIS2) which the
widened band set now supports but which have not been run; and harvest is excluded by a parcel
lookup rather than by a classifier.
