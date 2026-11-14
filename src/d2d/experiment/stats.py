"""Experiment statistics: power, SRM, difference in means (iid / cluster-robust), CUPED,
multiple-testing correction and group-sequential O'Brien-Fleming boundaries."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import stats as sps

Floats = npt.NDArray[np.float64]
Array = npt.NDArray[Any]

_N = NormalDist()


def z_quantile(p: float) -> float:
    return _N.inv_cdf(p)


# ---------- power ----------


def design_effect(cluster_size: float, icc: float) -> float:
    """Variance inflation of a cluster-randomized design (Kish): 1 + (m - 1) * ICC."""
    return 1.0 + (cluster_size - 1.0) * icc


def icc_anova(y: Array, clusters: Array) -> float:
    """One-way ANOVA estimator of the intraclass correlation (truncated at 0)."""
    y = np.asarray(y, dtype=np.float64)
    _, inv, sizes = np.unique(clusters, return_inverse=True, return_counts=True)
    k, n = len(sizes), len(y)
    means = np.bincount(inv, weights=y) / sizes
    ssb = float((sizes * (means - y.mean()) ** 2).sum())
    ssw = float(((y - means[inv]) ** 2).sum())
    msb, msw = ssb / (k - 1), ssw / (n - k)
    m0 = (n - float((sizes**2).sum()) / n) / (k - 1)
    return max(0.0, (msb - msw) / (msb + (m0 - 1) * msw))


def mde_proportion(
    p: float,
    n_per_arm: float,
    alpha: float = 0.05,
    power: float = 0.8,
    deff: float = 1.0,
    r2: float = 0.0,
) -> float:
    """Minimum detectable absolute effect for a two-sided two-proportion test.

    ``deff`` inflates variance for clustering; ``r2`` is the share of variance a CUPED covariate
    explains (variance x (1 - r2)).
    """
    z = z_quantile(1 - alpha / 2) + z_quantile(power)
    return z * math.sqrt(2 * p * (1 - p) * deff * (1 - r2) / n_per_arm)


def n_per_arm_proportion(
    p: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.8,
    deff: float = 1.0,
    r2: float = 0.0,
) -> int:
    z = z_quantile(1 - alpha / 2) + z_quantile(power)
    return math.ceil(z**2 * 2 * p * (1 - p) * deff * (1 - r2) / mde**2)


# ---------- sample-ratio mismatch ----------


@dataclass(frozen=True)
class SrmResult:
    observed: list[int]
    expected: list[float]
    statistic: float
    p_value: float
    threshold: float
    flagged: bool
    test: str


def srm_chi_square(
    observed: Sequence[int], expected_share: Sequence[float], threshold: float = 0.001
) -> SrmResult:
    """Pearson chi-square goodness of fit of arm counts to the designed split."""
    obs = np.asarray(observed, dtype=np.float64)
    share = np.asarray(expected_share, dtype=np.float64)
    exp = obs.sum() * share / share.sum()
    chi2 = float(((obs - exp) ** 2 / exp).sum())
    p = float(sps.chi2.sf(chi2, len(obs) - 1))
    return SrmResult(
        [int(o) for o in obs], [float(x) for x in exp], chi2, p, threshold, p < threshold, "chi2"
    )


def srm_per_cluster(
    counts_treatment: Array, counts_control: Array, threshold: float = 0.001
) -> SrmResult:
    """Cluster-aware SRM check: Welch t-test on units logged per randomized cluster."""
    t = np.asarray(counts_treatment, dtype=np.float64)
    c = np.asarray(counts_control, dtype=np.float64)
    res = sps.ttest_ind(t, c, equal_var=False)
    p = float(res.pvalue)
    return SrmResult(
        [int(t.sum()), int(c.sum())],
        [float(t.mean()), float(c.mean())],
        float(res.statistic),
        p,
        threshold,
        p < threshold,
        "welch_per_cluster",
    )


# ---------- effects ----------


@dataclass(frozen=True)
class Effect:
    metric: str
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    p_value: float
    mean_control: float
    mean_treatment: float
    n_control: int
    n_treatment: int
    clusters: int | None
    se_method: str

    @property
    def relative(self) -> float:
        return self.estimate / self.mean_control if self.mean_control else math.nan

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "relative": self.relative}


def cluster_robust_cov(x: Floats, resid: Floats, clusters: Array) -> Floats:
    """CR1 sandwich covariance for OLS coefficients, clustered by ``clusters``."""
    n, k = x.shape
    _, inv = np.unique(clusters, return_inverse=True)
    g = int(inv.max()) + 1
    scores = np.zeros((g, k))
    np.add.at(scores, inv, x * resid[:, None])
    bread = np.linalg.inv(x.T @ x)
    meat = scores.T @ scores
    correction = g / (g - 1) * (n - 1) / (n - k)
    return np.asarray(correction * bread @ meat @ bread, dtype=np.float64)


def diff_in_means(
    y: Array,
    treated: Array,
    clusters: Array | None = None,
    alpha: float = 0.05,
    metric: str = "",
) -> Effect:
    """Treatment minus control mean, with Welch (iid) or cluster-robust (CR1, t with G-1 df) SE."""
    y = np.asarray(y, dtype=np.float64)
    t = np.asarray(treated)
    yt, yc = y[t == 1], y[t == 0]
    est = float(yt.mean() - yc.mean())
    if clusters is None:
        se = math.sqrt(yt.var(ddof=1) / len(yt) + yc.var(ddof=1) / len(yc))
        crit = z_quantile(1 - alpha / 2)
        dist_p = 2 * (1 - _N.cdf(abs(est / se))) if se else 1.0
        n_clusters, method = None, "welch"
    else:
        x = np.column_stack([np.ones_like(y), t.astype(np.float64)])
        beta = np.linalg.lstsq(x, y, rcond=None)[0]
        cov = cluster_robust_cov(x, y - x @ beta, clusters)
        se = math.sqrt(cov[1, 1])
        n_clusters = len(np.unique(clusters))
        df = n_clusters - 1
        crit = float(sps.t.ppf(1 - alpha / 2, df))
        dist_p = float(2 * sps.t.sf(abs(est / se), df)) if se else 1.0
        method = "cluster_robust_cr1"
    return Effect(
        metric,
        est,
        se,
        est - crit * se,
        est + crit * se,
        float(dist_p),
        float(yc.mean()),
        float(yt.mean()),
        len(yc),
        len(yt),
        n_clusters,
        method,
    )


def cuped(y: Array, covariate: Array) -> tuple[Floats, float]:
    """CUPED-adjusted outcome y - theta (x - mean x), theta = cov(x, y) / var(x) (pooled)."""
    y = np.asarray(y, dtype=np.float64)
    x = np.asarray(covariate, dtype=np.float64)
    theta = float(np.cov(x, y, ddof=1)[0, 1] / x.var(ddof=1))
    return y - theta * (x - x.mean()), theta


# ---------- multiple testing ----------


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (family-wise error rate)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg step-up adjusted p-values (false discovery rate)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i], reverse=True)
    adjusted = [0.0] * m
    running = 1.0
    for rank, i in enumerate(order):
        running = min(running, p_values[i] * m / (m - rank))
        adjusted[i] = running
    return adjusted


# ---------- sequential testing ----------


def obrien_fleming_bounds(
    information: Sequence[float], alpha: float = 0.05, sims: int = 400_000, seed: int = 0
) -> list[float]:
    """Two-sided O'Brien-Fleming z boundaries c_k = C / sqrt(t_k) for information fractions t_k.

    C is the (1 - alpha) quantile of max_k |Z_k| sqrt(t_k) under H0, computed by seeded
    Monte Carlo of Brownian motion (independent increments), so the overall type I error is alpha.
    """
    t = np.asarray(information, dtype=np.float64)
    if t[-1] != 1.0 or np.any(np.diff(t) <= 0) or t[0] <= 0:
        raise ValueError("information fractions must increase and end at 1.0")
    rng = np.random.default_rng(seed)
    steps = rng.normal(size=(sims, len(t))) * np.sqrt(np.diff(t, prepend=0.0))
    w = np.cumsum(steps, axis=1)
    stat = np.max(np.abs(w), axis=1)  # |Z_k| sqrt(t_k) = |W(t_k)|
    c = float(np.quantile(stat, 1 - alpha))
    return [c / math.sqrt(tk) for tk in t]


@dataclass(frozen=True)
class SequentialResult:
    z: list[float]
    bounds: list[float]
    stopped_at_look: int | None


def sequential_test(z_by_look: Sequence[float], bounds: Sequence[float]) -> SequentialResult:
    stop = next(
        (k + 1 for k, (z, b) in enumerate(zip(z_by_look, bounds, strict=True)) if abs(z) >= b), None
    )
    return SequentialResult([float(z) for z in z_by_look], [float(b) for b in bounds], stop)


@dataclass(frozen=True)
class PeekingResult:
    sims: int
    looks: int
    alpha: float
    fpr_fixed_horizon: float
    fpr_naive_peeking: float
    fpr_obrien_fleming: float
    fpr_naive_by_looks: list[float]
    bounds: list[float]


def aa_peeking(
    sims: int = 2000,
    looks: int = 10,
    n_per_arm_per_look: int = 500,
    p: float = 0.3,
    alpha: float = 0.05,
    seed: int = 0,
) -> PeekingResult:
    """A/A simulation: checking a z-test after every batch inflates false positives; OBF doesn't."""
    rng = np.random.default_rng(seed)
    a = np.cumsum(rng.binomial(n_per_arm_per_look, p, (sims, looks)), axis=1)
    b = np.cumsum(rng.binomial(n_per_arm_per_look, p, (sims, looks)), axis=1)
    n = n_per_arm_per_look * np.arange(1, looks + 1)
    pooled = (a + b) / (2 * n)
    se = np.sqrt(pooled * (1 - pooled) * 2 / n)
    z = np.where(se > 0, (a - b) / n / np.where(se > 0, se, 1.0), 0.0)
    crit = z_quantile(1 - alpha / 2)
    reject = np.abs(z) >= crit
    bounds = obrien_fleming_bounds([k / looks for k in range(1, looks + 1)], alpha, seed=seed + 1)
    obf = np.abs(z) >= np.asarray(bounds)
    by_looks = [float(reject[:, :k].any(axis=1).mean()) for k in range(1, looks + 1)]
    return PeekingResult(
        sims,
        looks,
        alpha,
        float(reject[:, -1].mean()),
        float(reject.any(axis=1).mean()),
        float(obf.any(axis=1).mean()),
        by_looks,
        bounds,
    )
