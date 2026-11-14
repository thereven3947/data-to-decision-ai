"""Pre-registered analysis of one simulated experiment (see docs/experimentation.md)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from d2d.experiment.simulate import (
    Cases,
    CaseTemplate,
    Design,
    ExperimentConfig,
    Simulation,
    simulate,
)
from d2d.experiment.stats import (
    Effect,
    benjamini_hochberg,
    cuped,
    design_effect,
    diff_in_means,
    holm,
    icc_anova,
    obrien_fleming_bounds,
    sequential_test,
    srm_chi_square,
    srm_per_cluster,
    z_quantile,
)


@dataclass(frozen=True)
class Plan:
    """Frozen before results (pre-registration). Change only with a documented deviation."""

    alpha: float = 0.05
    srm_threshold: float = 0.001
    practical_effect: float = 0.02  # smallest conversion lift worth shipping (absolute)
    guardrail_margin: dict[str, float] = field(
        default_factory=lambda: {"wrong_action": 0.01, "false_escalation": 0.01}
    )
    look_days: tuple[int, ...] = (7, 14, 21, 28)
    late_from_day: int = 14  # novelty check: effect after the first two weeks


METRICS: tuple[tuple[str, str], ...] = (
    ("conversion", "primary"),
    ("resolve_minutes", "secondary"),
    ("wrong_action", "guardrail"),
    ("false_escalation", "guardrail"),
)


def _column(cases: Cases, metric: str) -> np.ndarray[Any, Any]:
    return cases.converted if metric == "conversion" else getattr(cases, metric)


def _effect(cases: Cases, metric: str, design: Design, plan: Plan, adjust: bool) -> Effect:
    y = _column(cases, metric).astype(np.float64)
    if adjust:
        y, _ = cuped(y, cases.pre_conversion)
    clusters = cases.reviewer if design == "reviewer" else None
    return diff_in_means(y, cases.treated, clusters, plan.alpha, metric)


def _srm(cases: Cases, design: Design, plan: Plan) -> dict[str, Any]:
    t = int(cases.treated.sum())
    out: dict[str, Any] = {
        "cases": asdict(srm_chi_square([t, len(cases) - t], [0.5, 0.5], plan.srm_threshold))
    }
    if design == "reviewer":
        counts = np.bincount(cases.reviewer)
        arm = np.zeros(len(counts), dtype=np.int64)
        arm[cases.reviewer] = cases.treated
        seen = counts > 0
        out["per_reviewer"] = asdict(
            srm_per_cluster(
                counts[seen & (arm == 1)], counts[seen & (arm == 0)], plan.srm_threshold
            )
        )
    out["flagged"] = any(v["flagged"] for v in out.values() if isinstance(v, dict))
    return out


def analyze(sim: Simulation, design: Design, plan: Plan | None = None) -> dict[str, Any]:
    plan = plan or Plan()
    c = sim.cases
    raw = {m: _effect(c, m, design, plan, adjust=False) for m, _ in METRICS}
    adj = _effect(c, "conversion", design, plan, adjust=True)
    family = [adj, raw["resolve_minutes"], raw["wrong_action"], raw["false_escalation"]]
    p = [e.p_value for e in family]
    holm_p, bh_p = holm(p), benjamini_hochberg(p)

    late = c.subset(c.day >= plan.late_from_day)
    weekly = [
        _effect(
            c.subset((c.day >= d) & (c.day < d + 7)), "conversion", design, plan, True
        ).as_dict()
        for d in range(0, int(c.day.max()) + 1, 7)
    ]
    looks = [d for d in plan.look_days if d <= int(c.day.max()) + 1]
    z, info = [], []
    for d in looks:
        part = c.subset(c.day < d)
        e = _effect(part, "conversion", design, plan, adjust=True)
        z.append(e.estimate / e.se)
        info.append(len(part) / len(c))
    info[-1] = 1.0
    seq = sequential_test(z, obrien_fleming_bounds(info, plan.alpha))

    segments: dict[str, list[dict[str, Any]]] = {}
    for name in ("device_group", "severity"):
        values = getattr(c, name)
        rows = []
        for v in sorted(set(values.tolist())):
            sub = c.subset(values == v)
            e = _effect(sub, "conversion", design, plan, adjust=True)
            rows.append(
                {
                    "segment": v,
                    "cases": len(sub),
                    "estimate": e.estimate,
                    "ci_low": e.ci_low,
                    "ci_high": e.ci_high,
                    "true_long_run": float(sub.true_cate.mean()),
                    "true_in_window": float(sub.true_effect_today.mean()),
                }
            )
        segments[name] = rows

    guardrails = {
        m: {
            "estimate": raw[m].estimate,
            "ci_high": raw[m].ci_high,
            "margin": plan.guardrail_margin[m],
            "pass": raw[m].ci_high < plan.guardrail_margin[m],
        }
        for m in plan.guardrail_margin
    }
    se_raw, se_adj = raw["conversion"].se, adj.se
    return {
        "label": sim.truth["label"],
        "design": design,
        "plan": asdict(plan),
        "cases": len(c),
        "reviewers": len(np.unique(c.reviewer)),
        "srm": _srm(c, design, plan),
        "effects": {m: raw[m].as_dict() for m in raw},
        "conversion_cuped": adj.as_dict(),
        "cuped_variance_reduction": 1 - (se_adj / se_raw) ** 2 if se_raw else math.nan,
        "multiple_testing": {
            "family": ["conversion_cuped", "resolve_minutes", "wrong_action", "false_escalation"],
            "p": p,
            "holm": holm_p,
            "bh": bh_p,
        },
        "guardrails": guardrails,
        "novelty": {
            "weekly": weekly,
            "late": _effect(late, "conversion", design, plan, True).as_dict(),
        },
        "sequential": asdict(seq) | {"look_days": looks, "information": info},
        "segments": segments,
        "truth": sim.truth,
    }


def decide(result: dict[str, Any]) -> tuple[str, list[str]]:
    """Pre-registered decision rule: ship / iterate / don't ship, with the reasons."""
    plan = result["plan"]
    primary = result["conversion_cuped"]
    late = result["novelty"]["late"]
    holm_p = result["multiple_testing"]["holm"][0]
    reasons: list[str] = []
    if result["srm"]["flagged"]:
        return "invalid", ["sample-ratio mismatch: fix logging and rerun before reading effects"]
    significant = holm_p < plan["alpha"] and primary["ci_low"] > 0
    reasons.append(
        f"primary lift {primary['estimate']:+.3f} (95% CI {primary['ci_low']:+.3f} to "
        f"{primary['ci_high']:+.3f}), Holm p={holm_p:.2g}"
    )
    reasons.append(
        f"weeks 3-4 lift {late['estimate']:+.3f} "
        f"(CI {late['ci_low']:+.3f} to {late['ci_high']:+.3f})"
    )
    failed = [m for m, g in result["guardrails"].items() if not g["pass"]]
    for m, g in result["guardrails"].items():
        reasons.append(
            f"guardrail {m}: {g['estimate']:+.4f}, upper CI {g['ci_high']:+.4f} vs margin "
            f"{g['margin']:+.3f} -> {'pass' if g['pass'] else 'FAIL'}"
        )
    if not significant or primary["estimate"] <= 0:
        return "dont_ship", reasons
    if failed or late["estimate"] < plan["practical_effect"]:
        return "iterate", reasons
    return "ship", reasons


def aa_simulator(
    cfg: ExperimentConfig,
    templates: list[CaseTemplate],
    sims: int = 300,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """A/A tests on the full simulator (effect_scale=0, no novelty or spillover).

    Under reviewer randomization, iid (Welch) SEs ignore clustering and over-reject; the
    cluster-robust SE should hold the false-positive rate near alpha.
    """
    rejects = {"reviewer_welch": 0, "reviewer_cluster": 0, "reviewer_cluster_cuped": 0, "case": 0}
    for i in range(sims):
        r = simulate(cfg, templates, "reviewer", seed=seed + i, effect_scale=0.0).cases
        y = r.converted.astype(np.float64)
        y_adj, _ = cuped(y, r.pre_conversion)
        rejects["reviewer_welch"] += diff_in_means(y, r.treated).p_value < alpha
        rejects["reviewer_cluster"] += diff_in_means(y, r.treated, r.reviewer).p_value < alpha
        rejects["reviewer_cluster_cuped"] += (
            diff_in_means(y_adj, r.treated, r.reviewer).p_value < alpha
        )
        c = simulate(cfg, templates, "case", seed=seed + i, effect_scale=0.0).cases
        rejects["case"] += diff_in_means(c.converted, c.treated).p_value < alpha
    fpr = {k: v / sims for k, v in rejects.items()}
    mc_se = math.sqrt(alpha * (1 - alpha) / sims)
    return {"sims": sims, "alpha": alpha, "monte_carlo_se": mc_se, "false_positive_rate": fpr}


@dataclass(frozen=True)
class PowerRow:
    design: str
    cuped: bool
    n_per_arm: int
    clusters_per_arm: int | None
    deff: float
    mde: float
    relative_mde: float


def power_table(
    pre: Cases, reviewers: int, cases_total: int, alpha: float = 0.05, power: float = 0.8
) -> dict[str, Any]:
    """MDE for case vs reviewer randomization, with and without CUPED.

    Variance, ICC and the CUPED R^2 come from pre-experiment (A/A, no treatment) data ``pre``.
    """
    y = pre.converted.astype(np.float64)
    base = float(y.mean())
    y_adj, theta = cuped(y, pre.pre_conversion)
    var_raw, var_adj = float(y.var(ddof=1)), float(y_adj.var(ddof=1))
    icc_raw, icc_adj = icc_anova(y, pre.reviewer), icc_anova(y_adj, pre.reviewer)
    m = cases_total / reviewers
    z = z_quantile(1 - alpha / 2) + z_quantile(power)
    n_arm = cases_total // 2
    rows = []
    for design, deffs in (
        ("case", (1.0, 1.0)),
        ("reviewer", (design_effect(m, icc_raw), design_effect(m, icc_adj))),
    ):
        for use_cuped, var, deff in ((False, var_raw, deffs[0]), (True, var_adj, deffs[1])):
            mde = z * math.sqrt(2 * var * deff / n_arm)
            rows.append(
                PowerRow(
                    design,
                    use_cuped,
                    n_arm,
                    reviewers // 2 if design == "reviewer" else None,
                    deff,
                    mde,
                    mde / base,
                )
            )
    return {
        "baseline_conversion": base,
        "variance": var_raw,
        "cuped_theta": theta,
        "cuped_r2_case_level": 1 - var_adj / var_raw,
        "icc": icc_raw,
        "icc_after_cuped": icc_adj,
        "cases_per_reviewer": m,
        "alpha": alpha,
        "power": power,
        "rows": [asdict(r) for r in rows],
    }
