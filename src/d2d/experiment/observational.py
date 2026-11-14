"""Confounding demo: reviewers choose when to open suggestions, so naive comparisons are biased.

Two exposures are compared in the same observational log:

- ``treated`` (opened ranked suggestions): a real effect (the simulator's CATE);
- ``history`` (opened the device-history panel): zero true effect, but skilled reviewers open it
  more often, so it is *associated* with conversion.

Adjusting for the observed confounders (experience, pre-period conversion, severity, device group)
separates the direct cause from the confounded association. Unconfoundedness holds here by
construction (selection depends only on observed columns); with real data it is an assumption.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from d2d.experiment.hte import features
from d2d.experiment.simulate import Cases, Floats, Ints
from d2d.experiment.stats import cluster_robust_cov, diff_in_means


def _ci(est: float, se: float) -> dict[str, float]:
    return {"estimate": est, "se": se, "ci_low": est - 1.96 * se, "ci_high": est + 1.96 * se}


def ols_adjusted(y: Floats, t: Floats, x: Floats, clusters: Ints) -> dict[str, float]:
    """Regression adjustment: coefficient on t in y ~ 1 + t + X, reviewer-clustered SE."""
    design = np.column_stack([np.ones(len(y)), t, x])
    beta = np.linalg.lstsq(design, y, rcond=None)[0]
    cov = cluster_robust_cov(design, y - design @ beta, clusters)
    return _ci(float(beta[1]), math.sqrt(cov[1, 1]))


def aipw(
    y: Floats, t: Floats, x: Floats, clusters: Ints, folds: int = 5, seed: int = 0
) -> dict[str, float]:
    """Cross-fitted augmented IPW (doubly robust) ATE; folds split by reviewer, SE clustered."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    rng = np.random.default_rng(seed)
    order = rng.permutation(np.unique(clusters))
    fold_of = {int(r): i % folds for i, r in enumerate(order)}
    fold = np.array([fold_of[int(r)] for r in clusters])
    e, m1, m0 = (np.zeros(len(y)) for _ in range(3))
    for k in range(folds):
        tr, te = fold != k, fold == k
        ps = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, random_state=seed)
        ps.fit(x[tr], t[tr])
        e[te] = ps.predict_proba(x[te])[:, 1]
        for arm, out in ((1, m1), (0, m0)):
            sel = tr & (t == arm)
            reg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, random_state=seed)
            reg.fit(x[sel], y[sel])
            out[te] = reg.predict(x[te])
    e = np.clip(e, 0.01, 0.99)
    psi = m1 - m0 + t * (y - m1) / e - (1 - t) * (y - m0) / (1 - e)
    est = float(psi.mean())
    # Cluster-robust SE of a mean: sum of within-reviewer influence, squared.
    _, inv = np.unique(clusters, return_inverse=True)
    g = np.bincount(inv, weights=psi - est)
    se = math.sqrt(float((g**2).sum())) / len(psi)
    return _ci(est, se) | {"propensity_min": float(e.min()), "propensity_max": float(e.max())}


def confounding_demo(c: Cases, seed: int = 0, with_forest: bool = True) -> dict[str, Any]:
    x, names = features(c)
    y = c.converted.astype(np.float64)
    out: dict[str, Any] = {"cases": len(c), "confounders": names, "exposures": {}}
    for exposure, other, truth in (
        ("treated", "history", float(c.true_cate.mean())),
        ("history", "treated", 0.0),
    ):
        t = getattr(c, exposure).astype(np.float64)
        xa = np.column_stack([x, getattr(c, other).astype(np.float64)])
        naive = diff_in_means(y, t, c.reviewer)
        row: dict[str, Any] = {
            "truth": truth,
            "share_exposed": float(t.mean()),
            "naive": _ci(naive.estimate, naive.se),
            "ols_adjusted": ols_adjusted(y, t, xa, c.reviewer),
            "aipw": aipw(y, t, xa, c.reviewer, seed=seed),
        }
        if with_forest:
            from d2d.experiment.hte import forest_ate

            row["causal_forest"] = forest_ate(y, t, xa, c.reviewer, seed=seed)
        for k in ("naive", "ols_adjusted", "aipw", "causal_forest"):
            if k in row:
                r = row[k]
                r["bias"] = r["estimate"] - truth
                r["covers_truth"] = r["ci_low"] <= truth <= r["ci_high"]
        out["exposures"][exposure] = row
    return out
