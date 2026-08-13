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

## F3 — OSM's problem-level detail does not exist where the fire was

**Holds, and it bounds what this project can claim.** All 724 `route_bottom` features (the
individual problems, and the only ones carrying Font grade and circuit membership) fall in just
**three locations**: 509 around Bas Cuvier / Apremont, 202 at Roche aux Sabots, 13 adjacent.

All three are unburned. So "0 of 724 problems burned" is true and almost meaningless — it
describes OSM's mapping effort, not the fire.

**Consequence:** the per-circuit answer, which was the most attractive output, **cannot be
produced for the burned sectors** from OSM alone. The crag- and boulder-level answer stands
(27 named features affected). Circuit-level would need bleau.info's own data; OSM carries
`ref:bleau.info` on 223 features, so the join exists, but the coverage does not.

Reporting per-circuit percentages computed only over mapped sectors would be badly misleading —
it would read as "no circuits burned".

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

## Open

- Validate severity classes against EMSR894's independent grading rather than asserting
  Key & Benson thresholds calibrated on North American conifer.
- SCL did **not** mistake char for cloud shadow on this pair — class 3 is absent inside the
  scar; the scar reads as class 5 (not vegetated, 47.8%), 7 (unclassified, 23.9%) and 2
  (dark, 10.2%). Class 2 is not in the mask set, so the scar survived. Worth re-checking on
  the during-fire scene (07-13) and on any pipeline that masks class 2.
- RdNBR is computed but not yet compared against dNBR across fuel types. That comparison is
  the point of computing it.
