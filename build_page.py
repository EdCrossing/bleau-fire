#!/usr/bin/env python
"""Build a self-contained HTML viewer for the burn-severity results.

Images are embedded as data URIs because the Artifact CSP blocks every external host.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "data" / "web"
OUT = ROOT / "data" / "out"


def b64(name: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode((WEB / name).read_bytes()).decode()


IMAGES = {
    f"{layer}_{scope}": b64(f"{layer}_{scope}.jpg")
    for scope in ("massif", "trois_pignons")
    for layer in ("rgb_pre", "rgb_post", "dnbr", "severity", "fuel")
}

fuel = pd.read_csv(OUT / "severity_by_tfv_g11.csv")
forest = json.loads((OUT / "forest_summary.json").read_text())

FUEL_ROWS = "\n".join(
    f'<tr><td class="nm">{html.escape(r["class"])}</td>'
    f'<td class="num">{r["ha_in_fire"]:,.0f}</td>'
    f'<td class="num">{r["pct_burned"]:.1f}%</td>'
    f'<td class="num">{r["dnbr_median"]:.3f}</td>'
    f'<td class="num sub">{r["rdnbr_median"]:.3f}</td>'
    f'<td class="bar"><span style="width:{min(100, r["dnbr_median"] / 0.5 * 100):.0f}%"></span></td></tr>'
    for _, r in fuel.iterrows()
)

df = pd.read_csv(OUT / "climbing_severity.csv")
affected = (
    df[(df["dnbr_median"] >= 0.27) & df["name"].notna()]
    .sort_values("dnbr_median", ascending=False)
)

SEV_SHORT = {
    "high severity": "high",
    "moderate-high severity": "mod-high",
    "moderate-low severity": "mod-low",
    "low severity": "low",
    "unburned": "unburned",
}
SEV_KEY = {"high severity": "s4", "moderate-high severity": "s3",
           "moderate-low severity": "s2", "low severity": "s1", "unburned": "s0"}

rows = []
for _, r in affected.iterrows():
    edge = bool(r["edge"])
    rows.append(
        f'<tr><td class="nm">{html.escape(str(r["name"]))}</td>'
        f'<td class="ty">{html.escape(str(r["feature_type"]))}</td>'
        f'<td class="num">{r["dnbr_median"]:.2f}</td>'
        f'<td class="num sub">{r["dnbr_iqr"]:.2f}</td>'
        f'<td><span class="chip {SEV_KEY[r["severity"]]}">'
        f'{SEV_SHORT[r["severity"]]}</span></td>'
        f'<td class="unc">{"± not resolved" if edge else ""}</td></tr>'
    )
TABLE_ROWS = "\n".join(rows)

summary = json.loads((OUT / "dnbr_summary.json").read_text())
quality = json.loads((OUT / "scene_quality.json").read_text())
usable = sum(1 for q in quality if q["aoi_valid_pct"] >= 90)

HTML = f"""<title>Which Bleau Boulders Burned</title>
<style>
:root {{
  --char:#EFEBE2; --plate:#FBF9F5; --panel:#FFFFFF; --ink:#221D16; --muted:#6B6154;
  --line:#D8D1C4; --marker:#136A7C; --marker-soft:#E0F0F3;
  --s0:#C9C9C9; --s1:#E8B84B; --s2:#E07A3C; --s3:#CE4326; --s4:#7F1208;
  --shadow:0 1px 2px rgba(34,29,22,.06),0 8px 24px rgba(34,29,22,.07);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --char:#15120E; --plate:#EDE9E1; --panel:#1F1B15; --ink:#EDE7DB; --muted:#9A9081;
    --line:#332C23; --marker:#5CC8DA; --marker-soft:#123038;
    --s0:#8A8A8A; --s1:#E8B84B; --s2:#E8853F; --s3:#E04E2C; --s4:#B01A0C;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 30px rgba(0,0,0,.45);
  }}
}}
:root[data-theme="dark"] {{
  --char:#15120E; --plate:#EDE9E1; --panel:#1F1B15; --ink:#EDE7DB; --muted:#9A9081;
  --line:#332C23; --marker:#5CC8DA; --marker-soft:#123038;
  --s0:#8A8A8A; --s1:#E8B84B; --s2:#E8853F; --s3:#E04E2C; --s4:#B01A0C;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 30px rgba(0,0,0,.45);
}}

*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--char); color:var(--ink);
  font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1120px; margin:0 auto; padding:clamp(24px,5vw,64px) clamp(18px,4vw,40px) 80px; }}
h1,h2,h3 {{
  font-family:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  font-weight:600; text-wrap:balance; margin:0;
}}
h1 {{ font-size:clamp(2rem,4.6vw,3.1rem); line-height:1.08; letter-spacing:-.015em; }}
h2 {{ font-size:1.45rem; letter-spacing:-.01em; }}
p {{ margin:0; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }}
.eyebrow {{
  font:600 .72rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  letter-spacing:.14em; text-transform:uppercase; color:var(--marker);
}}

header {{ display:flex; flex-direction:column; gap:18px; padding-bottom:34px; border-bottom:1px solid var(--line); }}
.lede {{ max-width:62ch; color:var(--muted); font-size:1.06rem; }}
.lede strong {{ color:var(--ink); font-weight:600; }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
  background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden; margin:34px 0; }}
.stat {{ background:var(--panel); padding:16px 18px; display:flex; flex-direction:column; gap:5px; }}
.stat .v {{ font:600 1.55rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums; letter-spacing:-.02em; }}
.stat .k {{ font-size:.78rem; color:var(--muted); line-height:1.35; }}

section {{ margin-top:52px; display:flex; flex-direction:column; gap:18px; }}
.controls {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
.seg {{ display:inline-flex; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:3px; gap:2px; }}
.seg button {{
  font:500 .82rem/1 ui-sans-serif,system-ui,sans-serif; color:var(--muted);
  background:none; border:0; padding:8px 13px; border-radius:6px; cursor:pointer;
}}
.seg button:hover {{ color:var(--ink); }}
.seg button[aria-pressed="true"] {{ background:var(--marker-soft); color:var(--marker); font-weight:640; }}
.seg button:focus-visible {{ outline:2px solid var(--marker); outline-offset:1px; }}

.stage {{ position:relative; background:var(--plate); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; box-shadow:var(--shadow); }}
.stage img {{ display:block; width:100%; height:auto; }}
.layerimg {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.clipped {{ clip-path:inset(0 0 0 var(--wipe,50%)); }}
.handle {{ position:absolute; top:0; bottom:0; left:var(--wipe,50%); width:2px;
  background:var(--marker); pointer-events:none; box-shadow:0 0 0 1px rgba(0,0,0,.25); }}
.handle::after {{ content:""; position:absolute; top:50%; left:50%; width:34px; height:34px;
  transform:translate(-50%,-50%); border-radius:50%; background:var(--marker);
  box-shadow:0 2px 8px rgba(0,0,0,.35); }}
.wipeinput {{ position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:ew-resize; margin:0; }}
.wipeinput:focus-visible + .handle {{ outline:2px solid var(--marker); outline-offset:3px; }}
.tag {{ position:absolute; top:12px; padding:5px 10px; border-radius:999px;
  font:600 .7rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; letter-spacing:.06em;
  text-transform:uppercase; background:rgba(10,8,6,.72); color:#F3EFE7; backdrop-filter:blur(6px); }}
.tag.l {{ left:12px; }} .tag.r {{ right:12px; }}
.cap {{ font-size:.86rem; color:var(--muted); max-width:70ch; }}
.cap .mono {{ color:var(--ink); }}

.ramp {{ display:flex; gap:0; border-radius:6px; overflow:hidden; border:1px solid var(--line); max-width:520px; }}
.ramp div {{ flex:1; height:22px; }}
.ramplab {{ display:flex; justify-content:space-between; max-width:520px;
  font:.7rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:var(--muted); }}

.tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--panel); }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
th {{ text-align:left; font:600 .7rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
  padding:13px 14px; border-bottom:1px solid var(--line); white-space:nowrap; }}
td {{ padding:11px 14px; border-bottom:1px solid var(--line); }}
tr:last-child td {{ border-bottom:0; }}
.nm {{ font-weight:560; }}
.ty {{ color:var(--muted); font-size:.82rem; }}
.num {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }}
.sub {{ color:var(--muted); }}
.unc {{ font-size:.76rem; color:var(--muted); white-space:nowrap; }}
.chip {{ display:inline-block; padding:3px 9px; border-radius:999px; color:#fff;
  font:600 .7rem/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; white-space:nowrap; }}
.chip.s4 {{ background:var(--s4); }} .chip.s3 {{ background:var(--s3); }}
.chip.s2 {{ background:var(--s2); }} .chip.s1 {{ background:var(--s1); color:#2A2007; }}
.bar {{ min-width:90px; }}
.bar span {{ display:block; height:7px; border-radius:4px; background:var(--s2); }}

.notes {{ display:grid; gap:14px; }}
.note {{ background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--s2);
  border-radius:8px; padding:16px 18px; display:flex; flex-direction:column; gap:7px; }}
.note h3 {{ font-size:1rem; }}
.note p {{ font-size:.9rem; color:var(--muted); max-width:72ch; }}
.note p strong {{ color:var(--ink); font-weight:600; }}
footer {{ margin-top:60px; padding-top:24px; border-top:1px solid var(--line);
  font-size:.8rem; color:var(--muted); display:flex; flex-direction:column; gap:6px; }}
@media (prefers-reduced-motion:no-preference) {{
  .seg button {{ transition:background .15s ease,color .15s ease; }}
}}
</style>

<div class="wrap">
<header>
  <p class="eyebrow">Sentinel-2 · 12 July 2026 · EMSR894</p>
  <h1>Which Bleau boulders burned</h1>
  <p class="lede">On 12 July 2026 an arson fire burned roughly <strong>2,000 hectares</strong> of the
  Fontainebleau massif — about a tenth of the forest, and the worst it has recorded since
  1863. This maps the burn onto the <strong>1,157 climbing features</strong> OpenStreetMap holds
  for the area, so the answer is a named rock rather than a hectare count.</p>
</header>

<div class="stats">
  <div class="stat"><span class="v">994</span><span class="k">hectares of <em>forest</em> burned at dNBR ≥ 0.27</span></div>
  <div class="stat"><span class="v">25</span><span class="k">named crags &amp; boulders affected</span></div>
  <div class="stat"><span class="v">50%</span><span class="k">of the naive ≥ 0.10 area is farm harvest, not fire</span></div>
  <div class="stat"><span class="v">{usable} / 39</span><span class="k">scenes actually usable over the AOI</span></div>
</div>

<section>
  <div>
    <p class="eyebrow">The imagery</p>
    <h2>Drag to burn</h2>
  </div>
  <div class="controls">
    <div class="seg" role="group" aria-label="Area">
      <button type="button" data-scope="trois_pignons" aria-pressed="true">Trois Pignons</button>
      <button type="button" data-scope="massif" aria-pressed="false">Whole massif</button>
    </div>
    <div class="seg" role="group" aria-label="Layer">
      <button type="button" data-layer="wipe" aria-pressed="true">Before / after</button>
      <button type="button" data-layer="dnbr" aria-pressed="false">dNBR</button>
      <button type="button" data-layer="severity" aria-pressed="false">Severity</button>
      <button type="button" data-layer="fuel" aria-pressed="false">Fuel &amp; paths</button>
    </div>
  </div>

  <div class="stage" id="stage">
    <img id="base" alt="Sentinel-2 imagery of the Fontainebleau massif" />
    <img id="over" class="layerimg clipped" alt="Post-fire Sentinel-2 imagery" />
    <input id="wipe" class="wipeinput" type="range" min="0" max="100" value="50"
           aria-label="Wipe between pre-fire and post-fire imagery" />
    <div class="handle" id="handle"></div>
    <span class="tag l" id="tagl">10 July</span>
    <span class="tag r" id="tagr">20 July</span>
  </div>

  <p class="cap" id="cap">Cyan rings are OpenStreetMap climbing features. Pre-fire
  <span class="mono">S2A_31UDP_20260710</span>, post-fire <span class="mono">S2A_31UDP_20260720</span> —
  ten days apart and the same satellite, so the difference is the fire rather than the season or
  a change of sensor.</p>

  <div id="legend" hidden>
    <div class="ramp">
      <div style="background:var(--s0)"></div><div style="background:var(--s1)"></div>
      <div style="background:var(--s2)"></div><div style="background:var(--s3)"></div>
      <div style="background:var(--s4)"></div>
    </div>
    <div class="ramplab"><span>unburned</span><span>low</span><span>mod-low</span>
      <span>mod-high</span><span>high</span></div>
  </div>
</section>

<section>
  <div>
    <p class="eyebrow">The answer</p>
    <h2>Named features inside the scar</h2>
  </div>
  <p class="cap">Sampled over a 50 m window around each feature, because OSM positions are
  contributed rather than surveyed. <span class="mono">iqr</span> is how varied the surface is
  inside that window; where the plausible range crosses a class boundary the severity is marked
  unresolved rather than stated flatly.</p>
  <div class="tablewrap">
    <table>
      <thead><tr><th>Feature</th><th>Type</th><th>dNBR</th><th>iqr</th>
        <th>Severity</th><th></th></tr></thead>
      <tbody>{TABLE_ROWS}</tbody>
    </table>
  </div>
</section>

<section>
  <div>
    <p class="eyebrow">What was burning</p>
    <h2>Severity by fuel type</h2>
  </div>
  <p class="cap">IGN's BD Forêt V2 labels every polygon in the massif by vegetation formation and
  dominant species, so severity can be cut by fuel instead of guessed at. Restricted to inside
  the derived fire footprint — a heath polygon 8 km from the flames is unburned because no fire
  reached it, and including it would manufacture the effect being tested.</p>
  <div class="tablewrap">
    <table>
      <thead><tr><th>Fuel type</th><th>ha in fire</th><th>burned</th>
        <th>dNBR</th><th>RdNBR</th><th style="width:22%"></th></tr></thead>
      <tbody>{FUEL_ROWS}</tbody>
    </table>
  </div>
  <p class="cap"><strong>This is the opposite of what I predicted.</strong> The textbook argument
  is that dNBR under-reads sparse fuel — less to lose — and that RdNBR compresses the gap.
  Heath reads <em>highest</em> here, closed conifer lowest, and RdNBR widens the spread rather
  than closing it. Calluna heath at Fontainebleau sits on open sand and burns to complete
  consumption, exposing bright substrate that is highly reflective in SWIR. Over heath the index
  is substantially measuring <em>the sand revealed</em>, not the energy released.</p>
</section>

<section>
  <div>
    <p class="eyebrow">Read this before quoting a number</p>
    <h2>What the data will not support</h2>
  </div>
  <div class="notes">
    <div class="note">
      <h3>Cloud cover picked the emptiest scenes</h3>
      <p>Seven scenes advertise under 15% cloud while being less than half usable here. They are
      <strong>partial swaths</strong> — granules clipped by the orbit edge. Because cloud is
      computed over valid pixels, a nearly empty granule reports nearly zero cloud, so ranking by
      it actively selects the empty ones. The 11 July scene, 0.6% cloud and one day before
      ignition, is <strong>99.4% nodata</strong> over the massif.</p>
    </div>
    <div class="note">
      <h3>Half the naive burned area is farm harvest</h3>
      <p>Fields around the massif were cut between the two dates, which strips green vegetation and
      produces the same signature dNBR detects. Cross-referenced against IGN's RPG register of
      declared agricultural parcels: of the 3,711 ha above dNBR 0.10, <strong>1,867 ha sits on
      farmland</strong>. At 0.27 the contamination falls to 118 ha. The forest-only figure is
      <strong>994 ha</strong>, and the derived fire footprint is 1,439 ha against EMS's reported
      ~2,000 ha — a gap that is probably definitional, since a rapid-mapping perimeter is the area
      <em>affected</em> rather than the area above a severity threshold. That remains unverified.</p>
    </div>
    <div class="note">
      <h3>The circuit-level answer does not exist yet</h3>
      <p>All 724 individually mapped problems — the only ones carrying Font grades and circuit
      colours — sit in three places: Bas Cuvier, Apremont and Roche aux Sabots. <strong>All three
      are unburned.</strong> So "no circuits burned" is a statement about mapping effort, not about
      the fire, and per-circuit percentages are deliberately omitted.</p>
    </div>
  </div>
</section>

<footer>
  <p>Contains modified Copernicus Sentinel-2 data (2026). Climbing features ©
  OpenStreetMap contributors, ODbL. Severity classes after Key &amp; Benson (2006), not yet
  validated against the EMSR894 grading.</p>
  <p class="mono">{summary["usable_pixel_pct"]}% of pixels usable on both dates · grid EPSG:32631 @ 10 m</p>
</footer>
</div>

<script>
const IMG = {json.dumps(IMAGES)};
const BASECAP = document.getElementById('cap').innerHTML;
const FUELCAP = 'BD For&ecirc;t V2 fuel type with the derived fire footprint outlined in red, '
  + 'the OpenStreetMap walking network in grey (14,335 ways) and climbing features in cyan. '
  + 'The fire is drawn as an outline rather than a fill so the fuel underneath stays readable '
  + '&mdash; the question here is what was burning, not where the fire was.';
const base = document.getElementById('base'), over = document.getElementById('over');
const stage = document.getElementById('stage'), wipe = document.getElementById('wipe');
const handle = document.getElementById('handle'), legend = document.getElementById('legend');
const cap = document.getElementById('cap');
const tagl = document.getElementById('tagl'), tagr = document.getElementById('tagr');
let scope = 'trois_pignons', layer = 'wipe';

function render() {{
  const isWipe = layer === 'wipe';
  base.src = isWipe ? IMG['rgb_pre_' + scope] : IMG[layer + '_' + scope];
  over.hidden = !isWipe; wipe.hidden = !isWipe; handle.hidden = !isWipe;
  tagl.hidden = !isWipe; tagr.hidden = !isWipe;
  legend.hidden = layer !== 'severity';
  cap.innerHTML = layer === 'fuel' ? FUELCAP : BASECAP;
  if (isWipe) over.src = IMG['rgb_post_' + scope];
}}
function setWipe(v) {{ stage.style.setProperty('--wipe', v + '%'); }}
wipe.addEventListener('input', e => setWipe(e.target.value));

document.querySelectorAll('[data-scope]').forEach(b => b.addEventListener('click', () => {{
  scope = b.dataset.scope;
  document.querySelectorAll('[data-scope]').forEach(o =>
    o.setAttribute('aria-pressed', String(o === b)));
  render();
}}));
document.querySelectorAll('[data-layer]').forEach(b => b.addEventListener('click', () => {{
  layer = b.dataset.layer;
  document.querySelectorAll('[data-layer]').forEach(o =>
    o.setAttribute('aria-pressed', String(o === b)));
  render();
}}));

setWipe(50); render();
</script>
"""

(ROOT / "data" / "web" / "index.html").write_text(HTML)
print(f"wrote {(ROOT / 'data' / 'web' / 'index.html')}  "
      f"{(ROOT / 'data' / 'web' / 'index.html').stat().st_size / 1e6:.2f} MB")
print(f"{len(affected)} affected named features, {usable}/39 usable scenes")
