# Where every layer comes from

Every dataset used here is **public and free**, and none of it required an account, a key or a
payment. This page lists what each layer is, who publishes it, what licence it carries, and
where to get it yourself. No code needed — most of these you can click and browse.

If you only follow one link: **[cartes.gouv.fr](https://cartes.gouv.fr)** is France's public
map portal, and most of the land-cover layers below can be viewed there directly in a browser.

---

## The satellite imagery

| What | Who | Resolution | Licence |
|---|---|---|---|
| **Sentinel-2** — the optical images the burn scar is measured from | ESA / Copernicus | 10 m | Free and open |
| **Sentinel-5P** — atmospheric gases, used for the smoke plume | ESA / Copernicus | ~5 km | Free and open |
| **Copernicus DEM** — ground elevation | ESA / Copernicus | 30 m | Free and open |

Sentinel-2 passes over Fontainebleau every 2–3 days and photographs it in 13 colours, including
several the eye cannot see. The burn measurement uses two of those: near-infrared, which healthy
leaves reflect strongly, and shortwave infrared, which they absorb. Fire flips both, which is
what makes a burn scar stand out so clearly.

Browse it without any software: **[Copernicus Browser](https://browser.dataspace.copernicus.eu)**
— pick a date and place and you get the same pictures used here.

Fetched programmatically from [Earth Search](https://earth-search.aws.element84.com/v1) (a free
public index of Sentinel-2 on AWS) and, for Sentinel-5P, the `meeo-s5p` public bucket.

---

## The French land-cover layers

All published by **IGN**, France's national mapping agency, under
**[Licence Ouverte / Etalab 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/)** —
free to use and redistribute with attribution. All viewable at
[cartes.gouv.fr](https://cartes.gouv.fr) or downloadable from
[geoservices.ign.fr](https://geoservices.ign.fr).

| Layer | What it actually is |
|---|---|
| **[BD Forêt® V2](https://geoservices.ign.fr/bdforet)** | Every patch of French woodland, classified by dominant tree species — oak, beech, Scots pine, heath. This is what "severity by fuel type" is cut by. |
| **[BD Forêts Anciennes](https://geoservices.ign.fr/bdforets-anciennes)** | Woodland that has been continuously forested since the 1850s. Built by overlaying the Victorian-era État-Major map onto today's forest map. Three classes: *ancient* (wooded then and now), *recent* (grew since), *disappeared* (cleared since). |
| **[RPG](https://geoservices.ign.fr/rpg)** — Registre Parcellaire Graphique | Every declared agricultural field in France and what crop is grown on it. Used here to prove that half the apparent "burned area" was actually harvested wheat and barley. |
| **[BD TOPO®](https://geoservices.ign.fr/bdtopo)** | Roads, tracks and forest paths. |
| **Historic aerial photography, 1950–65** | The whole country photographed from the air, mid-century. |
| **[État-Major map, 1820–66](https://geoservices.ign.fr/georeferencement-carte-etat-major)** | The Napoleonic-era military survey. The reference for what counted as woodland before modern forestry. |

For browsing the historic material specifically, IGN runs
**[remonterletemps.ign.fr](https://remonterletemps.ign.fr)** — slide between today and the 1800s
anywhere in France. It is the nicest thing on this list and needs no technical knowledge at all.

---

## The weather

| What | Who | Notes |
|---|---|---|
| **ERA5** | ECMWF / Copernicus | Hourly weather for anywhere on Earth, back to 1940. Reconstructed by feeding every historical observation through a modern weather model. |
| **CAMS** | Copernicus | European air quality — particulates, carbon monoxide. |

Both are fetched through **[Open-Meteo](https://open-meteo.com)**, a free API that needs no
registration. The Fire Weather Index shown on the site is **not downloaded** — it is calculated
here from raw ERA5 temperature, humidity, wind and rainfall using the Canadian Forest Fire
Weather Index equations (Van Wagner, 1987), the same system Copernicus uses for Europe.

---

## The bouldering

**[Boolder](https://www.boolder.com)** publishes the database behind its Fontainebleau apps
openly at [github.com/boolder-org/boolder-data](https://github.com/boolder-org/boolder-data)
under **CC BY 4.0** — 19,137 problems with grades, circuit colours and coordinates.

Deliberately *not* scraped from UKC or 27 Crags: their terms prohibit it, and a paid
subscription buys access rather than the right to republish.

**[OpenStreetMap](https://www.openstreetmap.org)** supplies footpaths, tracks and place names,
under the [ODbL](https://opendatacommons.org/licenses/odbl/) licence. Queried via
[Overpass Turbo](https://overpass-turbo.eu), which is worth a look — you can write a plain-text
query like "all footpaths in this box" and see results instantly on a map.

---

## The basemaps

The aerial and street views under the map are live tiles from **IGN** (France, down to ~0.3 m
per pixel), **[Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9)**
(global) and **OpenStreetMap** (global). Google is not used: their terms do not permit fetching
map tiles outside their own paid API.

---

## Not yet used, but worth knowing about

**[Copernicus Emergency Management Service](https://mapping.emergency.copernicus.eu)** maps
major disasters from satellite within hours, and publishes the results free. This fire is
activation **EMSR894**. Their expert-drawn fire perimeter would be the proper check on the
burn boundary estimated here.

**[EFFIS](https://effis.jrc.ec.europa.eu)** — the European Forest Fire Information System —
publishes daily fire danger and burnt-area maps for the whole continent.

---

## Reproducing it

Everything above is fetched by code in this repository, and every download is cached to disk so
a second run costs nothing. The relevant files:

| File | Fetches |
|---|---|
| `src/bleau_fire/search.py`, `scenes.py` | Sentinel-2 |
| `src/bleau_fire/ign.py` | BD Forêt, RPG, BD TOPO, OSM paths |
| `src/bleau_fire/webexport.py` | Ancient woodland, historic aerial, État-Major |
| `src/bleau_fire/weather.py` | ERA5, and the Fire Weather Index calculation |
| `src/bleau_fire/s5p.py` | Sentinel-5P |
| `src/bleau_fire/terrain.py` | Elevation |
| `src/bleau_fire/boulders.py` | Boolder |
| `src/bleau_fire/places.py` | Place names |
