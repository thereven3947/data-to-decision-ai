"""Heterogeneous treatment effects with EconML causal forests (optional ``experiment`` group).

Predictions are cross-fitted by reviewer: each case's CATE comes from a forest that never saw
that case's reviewer, so the validation against the simulator's ground truth is out-of-sample.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from d2d.experiment.simulate import SEVERITIES, Cases, Floats, Ints
from d2d.experiment.stats import cluster_robust_cov, diff_in_means

GROUPS = ("appliance", "energy", "plug_light", "security")


def features(c: Cases) -> tuple[Floats, list[str]]:
    cols = [c.experience, c.pre_conversion]
    names = ["experience_years", "pre_conversion"]
    for g in GROUPS:
        cols.append((c.device_group == g).astype(np.float64))
        names.append(f"device_group={g}")
    for s in SEVERITIES:
        cols.append((c.severity == s).astype(np.float64))
        names.append(f"severity={s}")
    return np.column_stack(cols).astype(np.float64), names


def _forest(seed: int, n_estimators: int, randomized: bool) -> Any:
    from econml.dml import CausalForestDML
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    # Randomized: the propensity is known, and a learned one only adds noise to the treatment
    # residual, which attenuates the final-stage effect toward zero.
    model_t = (
        DummyClassifier(strategy="prior")
        if randomized
        else HistGradientBoostingClassifier(max_iter=100, learning_rate=0.05, random_state=seed)
    )
    return CausalForestDML(
        model_y=HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, random_state=seed),
        model_t=model_t,
        discrete_treatment=True,
        cv=3,
        n_estimators=n_estimators,
        min_samples_leaf=40,
        max_samples=0.4,
        honest=True,
        random_state=seed,
        n_jobs=-1,
    )


def cross_fit_cate(
    c: Cases, folds: int = 5, seed: int = 0, n_estimators: int = 400, alpha: float = 0.1
) -> tuple[Floats, Floats, Floats]:
    """Out-of-fold CATE point estimates and (1 - alpha) intervals, folds split by reviewer."""
    x, _ = features(c)
    rng = np.random.default_rng(seed)
    reviewers = rng.permutation(np.unique(c.reviewer))
    fold_of = {int(r): i % folds for i, r in enumerate(reviewers)}
    fold = np.array([fold_of[int(r)] for r in c.reviewer])
    tau, lo, hi = (np.zeros(len(c)) for _ in range(3))
    for k in range(folds):
        train, test = fold != k, fold == k
        est = _forest(seed + k, n_estimators, randomized=True)
        est.fit(
            c.converted[train].astype(np.float64),
            c.treated[train],
            X=x[train],
            groups=c.reviewer[train],
        )
        tau[test] = est.effect(x[test]).ravel()
        low, high = est.effect_interval(x[test], alpha=alpha)
        lo[test], hi[test] = np.ravel(low), np.ravel(high)
    return tau, lo, hi


def _segments(c: Cases) -> dict[str, Any]:
    terciles = np.quantile(c.experience, [1 / 3, 2 / 3])
    exp_bucket = np.where(
        c.experience < terciles[0], "junior", np.where(c.experience < terciles[1], "mid", "senior")
    )
    return {"device_group": c.device_group, "severity": c.severity, "experience": exp_bucket}


def validate_cate(
    c: Cases, tau: Floats, lo: Floats, hi: Floats, alpha: float = 0.1, bins: int = 5
) -> dict[str, Any]:
    """Recovery of the simulator's known CATE, per-segment error, coverage and calibration.

    The reference is the in-window effect (``true_effect_today``, includes the novelty boost),
    which is what the experiment's data identify; the long-run mean is reported alongside.
    """
    truth = c.true_effect_today
    per_segment: dict[str, list[dict[str, Any]]] = {}
    for name, values in _segments(c).items():
        rows = []
        for v in sorted(set(values.tolist())):
            m = values == v
            rows.append(
                {
                    "segment": v,
                    "cases": int(m.sum()),
                    "true": float(truth[m].mean()),
                    "estimated": float(tau[m].mean()),
                    "abs_error": float(abs(tau[m].mean() - truth[m].mean())),
                }
            )
        per_segment[name] = rows

    # Calibration: within bins of predicted CATE, compare the mean prediction with the
    # experiment's own difference in means (no ground truth used), clustered by reviewer.
    edges = np.quantile(tau, np.linspace(0, 1, bins + 1))
    which = np.clip(np.searchsorted(edges, tau, side="right") - 1, 0, bins - 1)
    calib = []
    for b in range(bins):
        m = which == b
        sub = c.subset(m)
        e = diff_in_means(sub.converted, sub.treated, sub.reviewer, metric="conversion")
        calib.append(
            {
                "bin": b + 1,
                "cases": int(m.sum()),
                "predicted": float(tau[m].mean()),
                "observed": e.estimate,
                "observed_ci_low": e.ci_low,
                "observed_ci_high": e.ci_high,
                "true": float(truth[m].mean()),
            }
        )
    pred = np.array([r["predicted"] for r in calib])
    obs = np.array([r["observed"] for r in calib])
    slope = float(np.polyfit(pred, obs, 1)[0]) if np.ptp(pred) > 0 else float("nan")

    # Best-linear-predictor test on the transformed outcome (propensity 0.5 by design):
    # E[Y* | X] = tau(X), so regressing Y* on centered predictions gives slope 1 if calibrated.
    e_t = float(c.treated.mean())
    y_star = c.converted * (c.treated - e_t) / (e_t * (1 - e_t))
    design = np.column_stack([np.ones(len(c)), tau - tau.mean()])
    beta = np.linalg.lstsq(design, y_star, rcond=None)[0]
    blp_se = float(np.sqrt(cluster_robust_cov(design, y_star - design @ beta, c.reviewer)[1, 1]))
    blp_slope = float(beta[1])

    return {
        "cases": len(c),
        "pearson_r": float(np.corrcoef(tau, truth)[0, 1]),
        "rmse": float(np.sqrt(((tau - truth) ** 2).mean())),
        "mean_estimated": float(tau.mean()),
        "mean_true": float(truth.mean()),
        "mean_true_long_run": float(c.true_cate.mean()),
        "true_sd": float(truth.std()),
        "estimated_sd": float(tau.std()),
        "interval_level": 1 - alpha,
        "interval_coverage": float(((lo <= truth) & (truth <= hi)).mean()),
        "per_segment": per_segment,
        "calibration_bins": calib,
        "calibration_slope_bins": slope,
        "blp_slope": blp_slope,
        "blp_slope_ci": [blp_slope - 1.96 * blp_se, blp_slope + 1.96 * blp_se],
    }


def forest_ate(
    y: Floats, t: Floats, x: Floats, groups: Ints, seed: int = 0, n_estimators: int = 400
) -> dict[str, float]:
    """Doubly robust ATE from a causal forest fitted on observational data."""
    est = _forest(seed, n_estimators, randomized=False)
    est.fit(y, t, X=x, groups=groups)
    ate, se = float(np.ravel(est.ate_)[0]), float(np.ravel(est.ate_stderr_)[0])
    return {"estimate": ate, "se": se, "ci_low": ate - 1.96 * se, "ci_high": ate + 1.96 * se}
