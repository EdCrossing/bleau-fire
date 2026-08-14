"""Bouldering problems from Boolder's open database.

`FINDINGS.md` F3 recorded the limitation that killed the most interesting output: OSM's
problem-level detail — the only features carrying Font grades and circuit membership — existed
in just three locations, all of them unburned. So the per-circuit answer could not be produced
for the sectors that actually burned.

**Boolder resolves that.** The database behind the Boolder apps is published on GitHub as
SQLite under **CC BY 4.0** (`boolder-org/boolder-data`), and it holds **19,137 problems** with
coordinates, Font grade, circuit colour and circuit number, plus 271 circuits and 90 areas —
covering the whole massif, burned sectors included.

Why this and not UKC or 27 Crags: those prohibit scraping in their terms, and a paid
subscription grants access rather than redistribution rights, which matters for a public repo.
Boolder is explicitly licensed for reuse with attribution and is Fontainebleau-specific.
`bleau_info_id` also carries the cross-reference to bleau.info, matching OSM's `ref:bleau.info`.

⚠️ **These are still contributed positions, not survey.** The same windowed-sampling and `edge`
discipline from `features.py` applies — more problems does not mean more precise ones.
"""

from __future__ import annotations

import sqlite3

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .config import VECTOR_DIR

DB = VECTOR_DIR / "boolder.db"

# Boolder stores Font grades as text ("6a+", "3", "7b"). This orders them for aggregation
# without pretending the scale is linear — it is ordinal, and averaging grades is meaningless.
GRADE_ORDER = [
    f"{n}{s}" for n in range(1, 10) for s in ("", "a", "a+", "b", "b+", "c", "c+")
]


def load(db=DB) -> gpd.GeoDataFrame:
    """Problems joined to their area and circuit metadata, as an EPSG:4326 GeoDataFrame."""
    if not db.exists():
        raise FileNotFoundError(
            f"{db} missing — download it with:\n"
            f"  curl -sL -o {db} "
            f"https://github.com/boolder-org/boolder-data/raw/main/boolder.db"
        )
    con = sqlite3.connect(db)
    df = pd.read_sql_query(
        """
        SELECT p.id, p.name, p.grade, p.latitude, p.longitude,
               p.circuit_id, p.circuit_number, p.circuit_color,
               p.steepness, p.popularity, p.bleau_info_id,
               a.name AS area_name, c.name AS cluster_name
        FROM problems p
        LEFT JOIN areas a ON a.id = p.area_id
        LEFT JOIN clusters c ON c.id = a.cluster_id
        WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
        """,
        con,
    )
    con.close()

    gdf = gpd.GeoDataFrame(
        df,
        geometry=[Point(xy) for xy in zip(df["longitude"], df["latitude"])],
        crs="EPSG:4326",
    )
    gdf["feature_type"] = "problem"
    gdf["grade_rank"] = gdf["grade"].map({g: i for i, g in enumerate(GRADE_ORDER)})
    return gdf


def clip(gdf: gpd.GeoDataFrame, bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    w, s, e, n = bbox
    return gdf.cx[w:e, s:n].copy()


def circuit_rollup(df: pd.DataFrame, *, threshold: float = 0.27) -> pd.DataFrame:
    """Aggregate sampled problems by circuit — the unit a climber actually plans around.

    A Fontainebleau circuit is a numbered sequence following a painted colour, so "the red
    circuit at 95.2 is 80% inside the scar" is the useful statement, and far more robust than
    any single problem's position.

    Circuits are keyed by (area, colour) rather than colour alone: the same colour recurs
    across dozens of areas, and pooling them produces a meaningless massif-wide average.
    """
    sub = df[df["circuit_color"].notna() & df["dnbr_median"].notna()].copy()
    if not len(sub):
        return pd.DataFrame()
    g = (
        sub.groupby(["area_name", "circuit_color"])
        .agg(
            problems=("dnbr_median", "size"),
            dnbr_median=("dnbr_median", "median"),
            dnbr_p90=("dnbr_median", lambda s: s.quantile(0.9)),
            pct_burned=("dnbr_median", lambda s: round(100.0 * (s >= threshold).mean(), 1)),
            n_unresolved=("edge", "sum"),
        )
        .reset_index()
    )
    return g[g["problems"] >= 5].sort_values(
        ["pct_burned", "dnbr_median"], ascending=False
    )


def area_rollup(df: pd.DataFrame, *, threshold: float = 0.27) -> pd.DataFrame:
    """Aggregate by named area — the sector a climber drives to."""
    sub = df[df["dnbr_median"].notna()]
    g = (
        sub.groupby("area_name")
        .agg(
            problems=("dnbr_median", "size"),
            dnbr_median=("dnbr_median", "median"),
            pct_burned=("dnbr_median", lambda s: round(100.0 * (s >= threshold).mean(), 1)),
        )
        .reset_index()
    )
    return g[g["problems"] >= 10].sort_values("pct_burned", ascending=False)
