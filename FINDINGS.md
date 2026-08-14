# Findings log

Running record of measured results. Each entry states what was tested, what held, and what
didn't. Negative results and self-corrections stay in — they are the point of keeping this.

Setup unless stated otherwise: Sentinel-2 L2A via Earth Search, EPSG:32631 at 10 m, AOI
`(2.40, 48.25, 2.80, 48.50)`, pair `S2A_31UDP_20260710` (pre) → `S2A_31UDP_20260720` (post),
99.6% of pixels usable on both dates.

---

## Headline

Two burn scars are cleanly resolved and match the reported two-sector fire: a large one over
Trois Pignons / Noisy-sur-École and a smaller one to the north-east at the Faisanderie.

| dNBR threshold | Area |
|---|---|
| ≥ 0.10 (low severity and above) | 3,711 ha |
| ≥ 0.27 (moderate-low and above) | **1,136 ha** |

Copernicus EMS reported **~2,000 ha**, which falls between the two. See F2 — the ≥0.10 figure
is contaminated, so the gap is not simply a threshold-calibration question.

**27 named climbing features** sampled at moderate-low severity or worse: 17 crags, 10 boulders.
Worst affected: Long Boyau (dNBR 0.69), Jean des Vignes (0.66), Rocher du Potala (0.55),
Rocher du Général (0.52), J.A. Martin (0.50). Roche aux Sabots and La Ségognole sit in
unburned forest on the western edge.

---

## F1 — Scene cloud cover is *inverted* for partial swaths, not merely coarse

**Holds.** Of 39 scenes (June–mid-August 2026), only **8 are ≥90% usable** over the AOI, and
**7 advertise <15% cloud while being <50% usable**:

| Scene | Advertised cloud | Nodata over AOI |
|---|---|---|
| `S2C_31UDP_20260711_0_L2A` | 0.6% | 99.4% |
| `S2C_31UDP_20260721_0_L2A` | 0.7% | 99.7% |
| `S2C_31UDP_20260810_0_L2A` | 0.0% | 99.4% |
| `S2A_31UDP_20260812_1_L2A` | 0.0% | 99.1% |

These are partial swaths — granules clipped by the orbit edge that still intersect the search
bbox. Because `eo:cloud_cover` is computed over *valid* pixels, a granule containing almost no
data reports almost no cloud.

The standard advice ("scene cloud describes the granule, not your AOI") frames this as a
resolution mismatch. It is worse than that: the metric is **anti-correlated with usability** in
this regime, so ranking scenes by advertised cloud actively selects the empty ones. The 07-11
scene — 0.6% cloud, one day before ignition — looked like the ideal pre-fire baseline and
contains nothing over Fontainebleau. It was selected, downloaded and written before the 0.9 MB
output size gave it away.

**Consequence:** `run.py probe` reads one 20 m SCL band per candidate and measures AOI validity
directly. Scene selection is now made on that, never on metadata.

---

## F2 — dNBR area totals over a mixed landscape are contaminated by agricultural harvest

**Holds, and now quantified against IGN's RPG declared-parcel register** (7,268 parcels,
27,578 ha of farmland in the AOI, including 1,190 winter-wheat and 1,202 barley parcels — all
harvested in July).

| Threshold | Total | In forest | On farmland | Neither |
|---|---|---|---|---|
| dNBR ≥ 0.10 | 3,711 ha | 1,732 ha | **1,867 ha (50%)** | 112 ha |
| dNBR ≥ 0.27 | 1,136 ha | 994 ha | 118 ha (10%) | 24 ha |

**Half the ≥0.10 "burned area" is harvest.** At ≥0.27 the contamination drops to 10%, which
justifies the earlier decision to quote only that figure — but does not make it clean.

Forest-only burned area at ≥0.27 is **994 ha**; restricted to inside the fire footprint,
**976 ha**. The derived fire footprint is **1,439 ha** against EMS's reported ~2,000 ha.

⚠️ That remaining gap is **not resolved**. It is plausibly definitional — a rapid-mapping
perimeter is the area *affected*, which is larger than the area above a severity threshold —
but that is a hypothesis, and settling it needs the EMSR894 vectors. Do not present 976 ha and
2,000 ha as measuring the same thing.

---

## F5 — Burn severity by fuel type, and a refuted prediction about RdNBR

**Refuted, my own stated hypothesis.** BD Forêt V2 supplies species polygons for the whole
massif, so the fuel-bias argument could be measured rather than asserted. Restricted to inside
the fire footprint:

| Fuel (`tfv_g11`) | ha in fire | % burned | dNBR median | RdNBR median |
|---|---|---|---|---|
| Lande (heath) | 58 | **95.2** | **0.486** | **0.917** |
| Forêt ouverte mixte | 15 | 65.4 | 0.356 | 0.591 |
| Forêt ouverte conifères | 23 | 65.3 | 0.352 | 0.620 |
| Forêt fermée feuillus | 183 | 72.2 | 0.349 | 0.455 |
| Forêt fermée mixte | 389 | 67.7 | 0.338 | 0.461 |
| Forêt fermée conifères | 746 | 67.1 | **0.319** | 0.448 |

By species: beech is the least affected closed forest (47.2% burned, dNBR 0.256), which is
ecologically sensible — moist, deeply shaded, little understory fuel. Scots pine is the single
largest burned fuel at 604 ha.

**What I predicted, in `burn.py` and in conversation:** dNBR being an absolute difference, it
should read *low* over sparse fuel like heath (less to lose) and *high* over dense closed pine,
and RdNBR should compress that gap.

**Both halves are wrong.** Heath reads *highest* by a wide margin, closed conifer *lowest*, and
RdNBR **widens** the spread (0.917 vs 0.448) instead of closing it.

**Likely cause, and it is a confound rather than a discovery about fire:** Calluna heath at
Fontainebleau sits directly on open sandstone and sand. It burns to complete consumption —
95.2% of it, the highest of any class — exposing bare bright substrate. Sand is highly
reflective in SWIR, which drives NBR down hard and dNBR up. Closed pine at 10 m still contains
standing charred trunks and partially green crowns after a surface fire, so the pixel signal is
mixed.

So over heath the index is substantially measuring **the substrate revealed**, not the energy
released. RdNBR amplifies this because pre-fire heath has a modest NBR, shrinking the
denominator.

**Consequence:** neither index should be read as comparable severity *across* fuel types on
this massif without substrate correction, and the Key & Benson classes — calibrated on North
American conifer — are least trustworthy exactly where the signal is strongest. Within a single
fuel type the ranking is still usable.

General form of the trap, and the same shape as F4: **an index validated on one ecosystem
carries that ecosystem's substrate assumptions**, and the failure appears as an unusually strong
signal rather than a weak one — which reads as success.

---

## F3 — OSM's problem-level detail does not exist where the fire was — **now resolved**

**Held for OSM.** All 724 `route_bottom` features (the individual problems, and the only ones
carrying Font grade and circuit membership) fell in just **three locations**: 509 around Bas
Cuvier / Apremont, 202 at Roche aux Sabots, 13 adjacent. All three unburned. So "0 of 724
problems burned" was true and almost meaningless — it described OSM's mapping effort, not the
fire, and reporting per-circuit percentages from it would have read as "no circuits burned".

**Resolved by switching source.** [Boolder](https://github.com/boolder-org/boolder-data)
publishes the database behind its Fontainebleau apps as SQLite under **CC BY 4.0**: 19,137
problems with coordinates, Font grade, circuit colour and circuit number, plus 271 circuits and
90 areas. **17,605 fall inside the AOI and sample successfully** — 24× OSM's coverage, and
crucially it covers the burned sectors.

Not UKC or 27 Crags: both prohibit scraping in their terms, and a paid subscription grants
access rather than redistribution rights — which matters for a public repo. Boolder is licensed
for reuse with attribution and carries `bleau_info_id`, matching OSM's `ref:bleau.info`.

### The answer the earlier data could not give

**1,153 of 17,605 problems (6.5%)** sit at moderate-low severity or worse.

| Area | Circuit | Problems | dNBR median | Burned |
|---|---|---|---|---|
| Rocher du Général | yellow | 41 | 0.44 | **100%** |
| Rocher Guichot | orange | 31 | 0.36 | **100%** |
| Rocher Guichot | blue | 20 | 0.35 | 95% |
| Rocher de la Cathédrale | orange | 44 | 0.42 | 91% |
| Mont Aigu | orange | 64 | 0.33 | 86% |
| Cul de Chien | blue | 74 | 0.31 | 64% |

Every Apremont sector reads 0%. 95.2 comes out at 0.3% despite sitting beside the scar — the
fire stopped at its edge.

⚠️ **Circuits are keyed by (area, colour), not colour alone.** The same colour recurs across
dozens of sectors; pooling by colour produced a meaningless massif-wide average in the first
attempt.

⚠️ The `edge` flag fires on 1,950 problems — high, and expected: at problem scale a patchy scar
means many individual rocks sit in mixed 50 m windows. **The circuit-level percentage is the
robust statement; a single problem's class is not.** More problems did not buy more precision.

---

## F4 — Self-correction: the uncertainty flag was a burn detector

**Refuted, my own first implementation.** Features are sampled over a window rather than a
single pixel, because OSM positions are contributed and carry ~10–20 m of error (1–2 px at
10 m). An `edge` flag marked a feature "uncertain" when the window's **min and max** fell in
different severity classes.

It fired on **100% of burned named features** and near 0% of unburned ones. That is not an
uncertainty measure; it is a burn detector with extra steps.

Cause: a real burn scar is genuinely patchy at 10 m — unburned islands, variable fuel, crown
versus surface burn. Across 25 pixels the min and max nearly always straddle a boundary
wherever anything burned at all. The flag was measuring **surface heterogeneity** and
attributing it to **positional ambiguity**.

Fixed by using the p25–p75 range for `edge` and reporting the IQR separately as heterogeneity.
The flag now fires on 83 features rather than 202, and discriminates within the burned set:
Long Boyau (IQR 0.07) is confidently high severity, J.A. Martin (IQR 0.39) genuinely is not.

General form of the trap: **a robustness measure computed from extremes will track whatever
makes the data extreme**, which in a burn scar is the burn. Percentiles or nothing.

---

## F6 — Severity is not spatially transferable within this fire. Random CV says otherwise, and is wrong

**Holds, and it is a negative result worth more than a positive one would have been.**

Question: given the fire reached a place, what determined how hard it burned there? Predictors
were elevation, slope, northness/eastness, distance to the track and road network, pre-fire NBR
as a fuel-condition proxy, and BD Forêt fuel class. Target was dNBR. 60,000 pixels sampled from
inside the footprint, **eroded by 120 m** so that boundary pixels — which are low-dNBR by
construction, since the footprint was drawn from dNBR — are dropped rather than modelled.
Gradient boosting, scored two ways:

| Validation | R² |
|---|---|
| Random 5-fold | **+0.392** ± 0.004 |
| Spatially blocked 5-fold (1 km blocks) | **−0.185** ± 0.297 |

**A negative R² means the model is worse than predicting the mean.** It has no transferable
skill whatsoever. The random-CV score of 0.39 was measuring spatial autocorrelation: with
shuffled folds, test pixels sit metres from training pixels, so the model scores well by
recognising the neighbourhood rather than by learning anything about fire.

Blocked folds: `[-0.36, +0.15, -0.66, -0.13, +0.07]`. Random folds: `[0.394, 0.397, 0.386,
0.387, 0.395]` — note how implausibly tight the random folds are. That stability was the tell,
and it is the thing to be suspicious of in anyone else's map accuracy too.

This is **Ploton et al. (2020) reproduced in miniature on our own data**, and the reason
Wadoux et al. (2021) is worth reading straight afterwards: the blocked score is not automatically
"the true accuracy" either, it answers a different question — *how well does this transfer to
unseen ground?* — and here the answer is: it does not.

### The importances are consequently void, and my own docstring said so

`drivers.py` warns that "permutation importance on a model that does not generalise measures
nothing." That caveat applies to this run. For the record the ranking was elevation (0.241),
fuel (0.172), distance to track (0.061), then aspect, pre-fire NBR and slope near zero — **but
this describes how the model uses features to fail, not what drove the fire.** It should not be
quoted as a driver ranking.

### What can honestly be said

The **marginal descriptions** stand, since they are observed means rather than model effects:

- Heath burned hardest (mean dNBR 0.485), closed conifer lowest — consistent with F5.
- Lower ground burned harder: 0.436 at 69–77 m against 0.317 at 112–120 m.
- **Denser pre-fire vegetation burned *less* severely by dNBR** (0.409 at NBR 0.36–0.43 falling
  to 0.330 at 0.62–0.76), which is the same substrate artefact as F5 rather than a fire fact.
- Aspect is flat — northness barely moves the mean, so insolation is not visibly steering this.
- Distance to track shows a weak monotonic rise (0.355 within 10 m to 0.395 beyond 70 m),
  consistent with roads acting as firebreaks but far too weak to rest anything on.

### Limitations, stated rather than buried

- **Only 31 blocks.** The footprint is ~14 km², so 1 km blocks give few groups and ~6 per fold.
  The blocked estimate is genuinely noisy (±0.297); the *sign* is consistent across folds but the
  magnitude is not. A block-size sensitivity sweep is the obvious next step.
- **n = 1 fire.** One event is one realisation of weather, wind and ignition geometry. The
  strongest candidate driver — wind on the day — is absent from the predictors entirely, and its
  absence is a plausible complete explanation for the result.
- Fuel, terrain and access are mutually correlated (plantations sit where soil suits them, roads
  follow valleys), so even a positive result would not have isolated a cause.

---

## F7 — The two days this fire burned are the two most extreme fire-weather days since 1940

**Holds, and it is the strongest result in the project.**

The Canadian Fire Weather Index computed from ERA5 hourly surface data (via Open-Meteo's
archive), **1940 to 2026 — 759,288 hours, 31,637 daily FWI values**, spun up from 1 January each
decade so the long-memory drought codes carry real state rather than startup defaults.

| Rank | Date | FWI | Temp | RH | Wind | DC |
|---|---|---|---|---|---|---|
| **1** | **2026-07-13** | **61.8** | 33.7 °C | 22% | 17.9 km/h | 496 |
| **2** | **2026-07-12** | **58.5** | 33.0 °C | 25% | 16.5 km/h | 486 |
| 3 | 2019-07-25 | 57.5 | 38.7 °C | 23% | 13.8 km/h | 500 |
| 4 | 2022-07-19 | 53.4 | 37.0 °C | 23% | 15.5 km/h | 464 |
| 5 | 1976-08-22 | 52.5 | 24.6 °C | 27% | 20.5 km/h | 722 |

The fire ignited on 12 July and made its main run on 12–13 July. **Those are ranks 2 and 1 in an
86-year record.** 12 July sits at the **99.99th percentile of all days** and the 99.93rd of July
days specifically — the fairer comparison, since ranking a July day against Februaries flatters
it for free.

Summer 2026 (JJA) mean FWI is **26.46 against a 1940–2025 mean of 10.17 — rank 87 of 87.** The
most extreme fire-weather summer in the record, not merely a bad day inside a normal one. The
Drought Code reached 486, indicating months of accumulated deficit rather than a hot week.

### This reframes F6 rather than contradicting it

F6 found that terrain, fuel and access explain nothing about severity *within* the fire that
transfers spatially. F7 says the *occurrence* of the fire is almost perfectly explained by
weather. Those are consistent, and together they say something more useful than either alone:
**for this event the informative variance is in the "when", not the "where within".** A model of
where-it-burned-hardest had little to find; a model of when-a-fire-is-possible had a great deal.

### Wind direction does **not** explain the observed spread geometry

Wind on the initial run (12 July, 10:00–20:00) blew toward **239°** at 16 km/h gusting 40. Over
13–15 July, while the fire grew before containment on the evening of the 14th, it blew toward
**211°** at 10 km/h.

Measured spread, using a 13 July Sentinel-2 scene (during the fire) against 20 July:

- 732 ha burned by 13 July, 1,136 ha by 20 July — **60% of the final scar arrived after the 13th**
- Main sector (971 ha): growth centroid displaced 1,127 m toward **292°**
- Angular difference from wind: **81°**

**Refuted — my own first version of this analysis.** Pooling both fire sectors into one centroid
gave a 3,779 m shift toward 080°, which I nearly reported. That is an artefact: the fire had
**two sectors ~8 km apart**, and when one grows later the combined centroid lurches toward it and
reads as an eight-kilometre "spread". Two separate fires are not one object with a centroid. Now
computed per connected component; the second sector (426 ha) has too little early-vs-late split
to test at all.

Even corrected, the honest verdict is that **this method is too crude to conclude much**:

- **Suppression targets the head of the fire** — the downwind edge, by definition. Four Canadairs,
  two Dash aircraft and three helicopters made 187 drops. A fire actively prevented from running
  downwind while creeping elsewhere will show exactly this signature, so "spread ≠ wind" may be
  measuring firefighting rather than fire behaviour.
- Growth is constrained by fuel continuity, roads and firebreaks, none of which are downwind.
- A centroid displacement is a poor summary of an irregular perimeter's growth.

So: no support for wind-aligned spread here, and no confidence that its absence means anything.
A proper test needs the fire's actual progression isochrones (Copernicus EMS produced them for
EMSR894), not two satellite passes and a centroid.

---

## F8 — TROPOMI saw the plume, and it confirms the wind that the burn scar could not

**Holds.** Sentinel-5P / TROPOMI, read from MEEO's anonymous AWS COGs (`meeo-s5p`), against a
median of 8–10 July as baseline. This is the first evidence in the project from a **different
satellite, instrument and physical principle** — atmospheric absorption rather than surface
reflectance — so it is genuinely independent of everything else here.

### 13 July, the day of the main run

| Product | Peak anomaly | Distance from fire | Bearing |
|---|---|---|---|
| UV Aerosol Index | **+2.31** | 18 km | **264°** |
| CO total column | **+0.062 mol/m²** | 2 km | — |

The CO enhancement more than **doubles** the regional background (~0.029 mol/m²) and sits
essentially on the fire — 2 km is well inside one TROPOMI pixel, so its bearing is not
meaningful. The whole upper tail moved with it (p99 rose from 0.033 on 8 July to 0.050 on the
13th), so this is not one noisy pixel.

**The aerosol maximum is the useful one.** An Aerosol Index of +2.3 is unambiguous absorbing
aerosol, and it sits **18 km west of the fire at bearing 264°**. Wind on the initial run blew
toward **239°**. That is a **25° agreement** — well within what a 5.5 × 3.5 km footprint can
resolve.

### Why this matters for F7

F7 tried to confirm wind direction from burn-scar growth and failed (81° discrepancy), and I
argued the test was untrustworthy because **suppression targets the downwind head of a fire** —
aircraft and crews attack exactly where the wind is pushing it. The plume cannot be confounded
that way: nobody was water-bombing the smoke. So the atmospheric evidence supports downwind
transport where the ground evidence could not, and the disagreement between them is itself the
argument that the scar-geometry method was measuring firefighting.

### 12 July shows nothing, and that is the timing, not the fire

On the ignition day the peak anomalies are +0.30 (AI) and +0.012 (CO), both >100 km away —
i.e. background. Sentinel-5P crosses at ~12:00 UTC and the fire ignited that day and made its
run into the afternoon and the 13th. **The satellite passed before there was much to see.**

### Refuted — my own orbit selection silently deleted the ignition day

The first version picked the single orbit whose sensing window was closest to local noon. TROPOMI
flies 14 orbits a day and each swath is a strip: the nearest-in-time orbit can miss the region
entirely, returning an all-NaN array that is **indistinguishable from "the instrument saw
nothing"**. It reported 0% coverage for 10 and 12 July — including the ignition day — and I
nearly wrote that up as absence of signal. Fixed by trying candidates in time order and keeping
the first with real coverage. All six days now return 100% (or near) coverage.

General form: **an empty result from a data-access layer must be distinguishable from an empty
result from the instrument.** They are not the same claim and they should never share a code path
that cannot tell them apart.

### CAMS surface air quality is inconclusive, and says so

CAMS European surface fields (via Open-Meteo, no key) sampled on a ring of 16 points at 25 and
60 km. PM2.5 rose **+1.02 µg/m³ across the whole ring** during 12–14 July — a broad regional
increase consistent with heatwave stagnation rather than a plume. The downwind-minus-upwind
contrast is only **+0.48 µg/m³**, and the largest PM2.5 rise is to the *south* (135–180°), not
the west-south-west.

CO is somewhat cleaner: positive on the west and south-west (+8 to +9) and **negative to the
north** (−6.6), which is directionally consistent with transport toward ~239°. But a modelled
surface field at ~11 km resolution, driven by fire emissions it may or may not have assimilated,
is weak evidence either way. **The TROPOMI observation is the one to rely on**; CAMS is a model
and here it mostly shows the heatwave.

---

## Open

- Validate severity classes against EMSR894's independent grading rather than asserting
  Key & Benson thresholds calibrated on North American conifer.
- SCL did **not** mistake char for cloud shadow on this pair — class 3 is absent inside the
  scar; the scar reads as class 5 (not vegetated, 47.8%), 7 (unclassified, 23.9%) and 2
  (dark, 10.2%). Class 2 is not in the mask set, so the scar survived. Worth re-checking on
  the during-fire scene (07-13) and on any pipeline that masks class 2.
- RdNBR is computed but not yet compared against dNBR across fuel types. That comparison is
  the point of computing it.
