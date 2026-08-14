"""What drove severity where the fire burned?

Given that the fire reached a place, what determined how hard it burned there — fuel type,
terrain, or access? This is the first genuinely research-shaped question the project can ask,
and it is also the easiest one to get quietly, confidently wrong. Three traps, all of which
this repo or its sibling has already fallen into once:

**1. Circularity (the F1 trap, again).** The fire footprint is *derived from dNBR*. Restricting
to it and then modelling dNBR means the analysis domain was defined by the outcome. Pixels near
the footprint boundary are low-dNBR **by construction**, so any predictor correlated with being
near the edge — distance to a road that stopped the fire, for instance — gets handed a spurious
effect. Mitigated by **eroding the footprint** before sampling, so boundary pixels are dropped
rather than modelled. Any predictor derived from dNBR is excluded outright.

**2. Spatial autocorrelation.** Neighbouring 10 m pixels are not independent observations. A
model scored by random k-fold has training pixels metres from test pixels, so it can score
excellently by memorising the local neighbourhood while learning nothing transferable. This is
exactly Ploton et al. (2020) vs Wadoux et al. (2021), and both scores are reported here so the
gap is visible rather than assumed. **The blocked score is the honest one.**

**3. Importance without a baseline.** Permutation importance on a model that does not generalise
measures nothing. Importances are computed under the blocked split only.

Nothing here establishes causation. Fuel type, terrain and access are mutually correlated —
plantations sit where the soil suits them and roads follow valleys — so a strong association is
a starting point for a question, not an answer to one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BLOCK_M = 1000.0  # spatial CV block edge, metres


def assemble(
    delta: np.ndarray,
    footprint: np.ndarray,
    valid: np.ndarray,
    predictors: dict[str, np.ndarray],
    grid,
    *,
    erode_m: float = 120.0,
    max_samples: int = 60000,
    seed: int = 0,
) -> pd.DataFrame:
    """Sample pixels well inside the fire footprint into a tidy predictor table.

    `erode_m` pulls the sampling domain in from the footprint boundary. 120 m is four DEM cells
    and twelve image pixels — enough to clear the band where dNBR is depressed simply because
    the footprint was drawn there.
    """
    from scipy import ndimage

    r = max(1, int(round(erode_m / grid.resolution)))
    core = ndimage.binary_erosion(footprint, structure=np.ones((3, 3)), iterations=r)
    sel = core & valid & np.isfinite(delta)
    for arr in predictors.values():
        if arr.dtype.kind == "f":
            sel &= np.isfinite(arr)

    rows, cols = np.nonzero(sel)
    print(f"  [sample] footprint {footprint.sum():,} px -> eroded core {core.sum():,} px "
          f"-> usable {len(rows):,} px")
    if len(rows) > max_samples:
        rng = np.random.default_rng(seed)
        keep = rng.choice(len(rows), max_samples, replace=False)
        rows, cols = rows[keep], cols[keep]

    df = pd.DataFrame({name: arr[rows, cols] for name, arr in predictors.items()})
    df["dnbr"] = delta[rows, cols]

    # Spatial CV group id: which BLOCK_M-sided tile the pixel falls in.
    df["block"] = (
        (rows * grid.resolution // BLOCK_M).astype(int) * 100000
        + (cols * grid.resolution // BLOCK_M).astype(int)
    )
    df["row"], df["col"] = rows, cols
    return df


def compare_cv(df: pd.DataFrame, feature_cols: list[str], *, seed: int = 0) -> dict:
    """Score the same model under random and spatially blocked k-fold.

    The gap between them is the finding. A large one means the random score was measuring
    spatial autocorrelation rather than a transferable relationship.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold, KFold, cross_val_score

    X = df[feature_cols]
    y = df["dnbr"].to_numpy()

    def model():
        return HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.06, max_depth=6,
            categorical_features=[c for c in feature_cols if X[c].dtype.name == "category"],
            random_state=seed,
        )

    rnd = cross_val_score(model(), X, y, cv=KFold(5, shuffle=True, random_state=seed),
                          scoring="r2", n_jobs=1)
    blk = cross_val_score(model(), X, y, cv=GroupKFold(5), groups=df["block"],
                          scoring="r2", n_jobs=1)
    return {
        "r2_random_mean": round(float(rnd.mean()), 4),
        "r2_random_std": round(float(rnd.std()), 4),
        "r2_blocked_mean": round(float(blk.mean()), 4),
        "r2_blocked_std": round(float(blk.std()), 4),
        "inflation": round(float(rnd.mean() - blk.mean()), 4),
        "folds_random": [round(float(v), 4) for v in rnd],
        "folds_blocked": [round(float(v), 4) for v in blk],
    }


def importances(df: pd.DataFrame, feature_cols: list[str], *, seed: int = 0,
                n_repeats: int = 5) -> pd.DataFrame:
    """Permutation importance under a **spatially blocked** hold-out.

    Held out by block, not at random, for the same reason the scores are: importance measured
    against a test set interleaved with training pixels rewards memorisation.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.model_selection import GroupKFold

    X, y = df[feature_cols], df["dnbr"].to_numpy()
    tr, te = next(GroupKFold(5).split(X, y, groups=df["block"]))
    m = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.06, max_depth=6,
        categorical_features=[c for c in feature_cols if X[c].dtype.name == "category"],
        random_state=seed,
    ).fit(X.iloc[tr], y[tr])

    pi = permutation_importance(m, X.iloc[te], y[te], n_repeats=n_repeats,
                                random_state=seed, scoring="r2", n_jobs=1)
    return (
        pd.DataFrame({
            "feature": feature_cols,
            "importance": pi.importances_mean.round(4),
            "std": pi.importances_std.round(4),
        })
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def partial_effects(df: pd.DataFrame, col: str, *, bins: int = 8) -> pd.DataFrame:
    """Observed mean dNBR by binned predictor — a description, not a model effect.

    Kept deliberately simple and separate from the model: with correlated predictors a marginal
    trend is not an isolated effect, and presenting it as one would overstate what is known.
    """
    if df[col].dtype.name == "category":
        g = df.groupby(col, observed=True)["dnbr"].agg(["mean", "median", "size"])
    else:
        q = pd.qcut(df[col], bins, duplicates="drop")
        g = df.groupby(q, observed=True)["dnbr"].agg(["mean", "median", "size"])
    return g.round(3).reset_index()
