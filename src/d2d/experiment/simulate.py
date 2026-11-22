"""Seeded review-flow experiment simulator with a known ground-truth treatment effect.

Extends the simulated reviewer (``d2d.hitl.sim_reviewer``): each case is a change story with its
ground-truth category, device kind and notification severity; a simulated reviewer either sees
ranked suggested actions from the category playbook (treatment) or none (control). Every effect
is set in ``config/experiment.toml``, so estimators can be scored against the truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from d2d.config import ConfigError, Household, Taxonomy, load_toml

Design = Literal["reviewer", "case", "observational"]
DESIGNS: tuple[Design, ...] = ("reviewer", "case", "observational")
SEVERITIES = ("info", "notice", "alert")
DATA_LABEL = "SIMULATED DATA: seeded simulator, no real users or production traffic"

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]
Strs = npt.NDArray[np.str_]


@dataclass(frozen=True)
class ExperimentConfig:
    seed: int
    reviewers: int
    days: int
    cases_per_reviewer_day: float
    pre_period_days: int
    device_group: dict[str, str]
    baseline: dict[str, Any]
    effect: dict[str, Any]
    resolve: dict[str, Any]
    guardrail: dict[str, Any]
    srm_bug: dict[str, Any]
    observational: dict[str, Any]


def load_experiment(path: Path) -> ExperimentConfig:
    raw = load_toml(path)
    try:
        return ExperimentConfig(**{f.name: raw[f.name] for f in fields(ExperimentConfig)})
    except KeyError as exc:
        raise ConfigError(f"{path}: missing key {exc}") from exc


@dataclass(frozen=True)
class CaseTemplate:
    category: str
    device_kind: str
    severity: str


def load_severity(path: Path) -> tuple[dict[str, str], str]:
    raw = load_toml(path)
    return dict(raw.get("severity", {})), str(raw.get("default", "info"))


def templates_from_config(
    household: Household, taxonomy: Taxonomy, severity: dict[str, str], default: str
) -> list[CaseTemplate]:
    """Every category x active device kind (used when no pipeline run is available)."""
    kinds = sorted({d.kind for d in household.devices.values() if d.active})
    return [
        CaseTemplate(c, k, severity.get(c, default))
        for c in sorted(taxonomy.categories)
        for k in kinds
    ]


def templates_from_stories(
    data_dir: Path, household: Household, severity: dict[str, str], default: str
) -> list[CaseTemplate]:
    """One template per story labeled by the simulated reviewer (output of `d2d run`)."""
    from d2d.hitl.sim_reviewer import label_stories

    labels = label_stories(data_dir)
    stories = pq.read_table(data_dir / "stories" / "stories.parquet").to_pylist()
    out: list[CaseTemplate] = []
    for s in sorted(stories, key=lambda s: s["story_id"]):
        if s["story_id"] in labels and s["device_ids"]:
            kind = household.devices[s["device_ids"][0]].kind
            cat = labels[s["story_id"]]
            out.append(CaseTemplate(cat, kind, severity.get(cat, default)))
    return out


@dataclass
class Cases:
    """Columnar case log. ``true_*`` columns are simulator ground truth, unseen by estimators."""

    reviewer: Ints
    day: Ints
    category: Strs
    device_kind: Strs
    device_group: Strs
    severity: Strs
    experience: Floats
    pre_conversion: Floats
    treated: Ints
    history: Ints
    converted: Ints
    resolve_minutes: Floats
    wrong_action: Ints
    false_escalation: Ints
    true_cate: Floats
    true_effect_today: Floats

    def __len__(self) -> int:
        return len(self.reviewer)

    def subset(self, mask: npt.NDArray[np.bool_]) -> Cases:
        return Cases(**{f.name: getattr(self, f.name)[mask] for f in fields(Cases)})

    def to_table(self) -> pa.Table:
        return pa.table({f.name: getattr(self, f.name) for f in fields(Cases)})

    @classmethod
    def from_table(cls, table: pa.Table) -> Cases:
        cols = {f.name: table.column(f.name).to_numpy(zero_copy_only=False) for f in fields(cls)}
        for name in ("category", "device_kind", "device_group", "severity"):
            cols[name] = cols[name].astype(np.str_)
        return cls(**cols)


@dataclass(frozen=True)
class Simulation:
    cases: Cases
    truth: dict[str, Any]


def _sigmoid(x: Floats) -> Floats:
    return np.asarray(1.0 / (1.0 + np.exp(-x)), dtype=np.float64)


def _lookup(table: dict[str, float], keys: Strs) -> Floats:
    return np.array([float(table.get(str(k), 0.0)) for k in keys], dtype=np.float64)


def simulate(
    cfg: ExperimentConfig,
    templates: list[CaseTemplate],
    design: Design = "reviewer",
    *,
    seed: int | None = None,
    reviewers: int | None = None,
    days: int | None = None,
    effect_scale: float = 1.0,
    novelty: float | None = None,
    spillover: float | None = None,
    srm_bug: bool = False,
) -> Simulation:
    """Generate one experiment. ``effect_scale=0`` gives an A/A test with the same structure."""
    if not templates:
        raise ConfigError("no case templates")
    if design not in DESIGNS:
        raise ConfigError(f"unknown design {design!r}")
    seed = cfg.seed if seed is None else seed
    n_rev = cfg.reviewers if reviewers is None else reviewers
    n_days = cfg.days if days is None else days
    b, e, r, g = cfg.baseline, cfg.effect, cfg.resolve, cfg.guardrail
    novelty_boost = float(e["novelty_boost"] if novelty is None else novelty)
    spill = float(e["spillover"] if spillover is None else spillover)
    if design == "observational":
        novelty_boost, spill = 0.0, 0.0  # steady state, no concurrent control arm
    rng = np.random.default_rng(seed)

    max_exp = float(b["max_experience_years"])
    rev_exp = rng.uniform(0.0, max_exp, n_rev)
    rev_u = rng.normal(0.0, float(b["reviewer_sd"]), n_rev)
    rev_time = rng.normal(0.0, float(r["reviewer_sd"]), n_rev)

    per_day = rng.poisson(cfg.cases_per_reviewer_day, (n_rev, n_days))
    reviewer = np.repeat(np.arange(n_rev, dtype=np.int64), per_day.sum(axis=1))
    day = np.concatenate([np.repeat(np.arange(n_days, dtype=np.int64), row) for row in per_day])
    n = len(reviewer)
    pick = rng.integers(0, len(templates), n)
    category = np.array([templates[i].category for i in pick], dtype=np.str_)
    device_kind = np.array([templates[i].device_kind for i in pick], dtype=np.str_)
    device_group = np.array(
        [cfg.device_group.get(str(k), "other") for k in device_kind], dtype=np.str_
    )
    severity = np.array([templates[i].severity for i in pick], dtype=np.str_)

    exp_c = rev_exp - max_exp / 2
    rev_logit = float(b["intercept"]) + rev_u + float(b["experience_per_year"]) * exp_c
    tmpl_logit = np.array(
        [
            float(b["severity"].get(t.severity, 0.0))
            + float(b["device_group"].get(cfg.device_group.get(t.device_kind, "other"), 0.0))
            for t in templates
        ]
    )
    # Pre-period (no suggestions anywhere): the CUPED covariate, observed for every reviewer.
    rev_p_pre = _sigmoid(rev_logit[:, None] + tmpl_logit[None, :]).mean(axis=1)
    n_pre = np.maximum(rng.poisson(cfg.cases_per_reviewer_day * cfg.pre_period_days, n_rev), 1)
    rev_pre = rng.binomial(n_pre, rev_p_pre) / n_pre

    p0 = _sigmoid(rev_logit[reviewer] + tmpl_logit[pick])
    tau = effect_scale * (
        float(e["base"])
        + float(e["experience_per_year"]) * exp_c[reviewer]
        + _lookup(e["severity"], severity)
        + _lookup(e["device_group"], device_group)
    )
    true_cate = np.clip(p0 + tau, 0.01, 0.99) - p0
    novelty_factor = 1.0 + novelty_boost * np.exp(-day / float(e["novelty_decay_days"]))
    effect_today = np.clip(p0 + true_cate * novelty_factor, 0.0, 1.0) - p0

    pre_conversion = rev_pre[reviewer]
    history = np.zeros(n, dtype=np.int64)
    if design == "reviewer":
        arm = np.zeros(n_rev, dtype=np.int64)
        arm[rng.permutation(n_rev)[: n_rev // 2]] = 1
        treated = arm[reviewer]
    elif design == "case":
        treated = rng.integers(0, 2, n).astype(np.int64)
    else:
        o = cfg.observational
        pre_c = pre_conversion - rev_pre.mean()
        sel = (
            float(o["intercept"])
            + float(o["experience_per_year"]) * exp_c[reviewer]
            + float(o["pre_conversion"]) * pre_c
            + _lookup(o["severity"], severity)
        )
        treated = (rng.random(n) < _sigmoid(sel)).astype(np.int64)
        hist = float(o["history_intercept"]) + float(o["history_pre_conversion"]) * pre_c
        history = (rng.random(n) < _sigmoid(hist)).astype(np.int64)

    leak = spill if design == "case" else 0.0  # every reviewer sees both arms under case design
    p = p0 + np.where(treated == 1, effect_today, leak * effect_today)
    converted = (rng.random(n) < np.clip(p, 0.0, 1.0)).astype(np.int64)

    log_t = (
        float(r["log_mean"])
        + rev_time[reviewer]
        + _lookup(r["severity"], severity)
        + effect_scale * float(r["treatment"]) * treated
        + rng.normal(0.0, float(r["log_sd"]), n)
    )
    resolve_minutes = np.exp(log_t)

    wrong_t = float(g["wrong_given_convert_treatment"]) + float(g["suggestion_wrong_rate"]) * float(
        g["follow_wrong_suggestion"]
    )
    wrong_c = float(g["wrong_given_convert_control"])
    p_wrong = wrong_c + effect_scale * (wrong_t - wrong_c) * treated
    wrong_action = ((converted == 1) & (rng.random(n) < p_wrong)).astype(np.int64)
    esc_c, esc_t = float(g["escalate_info_control"]), float(g["escalate_info_treatment"])
    p_esc = esc_c + effect_scale * (esc_t - esc_c) * treated
    false_escalation = ((severity == "info") & (rng.random(n) < p_esc)).astype(np.int64)

    cases = Cases(
        reviewer=reviewer,
        day=day,
        category=category,
        device_kind=device_kind,
        device_group=device_group,
        severity=severity,
        experience=rev_exp[reviewer],
        pre_conversion=pre_conversion,
        treated=treated,
        history=history,
        converted=converted,
        resolve_minutes=resolve_minutes,
        wrong_action=wrong_action,
        false_escalation=false_escalation,
        true_cate=true_cate,
        true_effect_today=effect_today,
    )
    dropped = 0
    if srm_bug:
        s = cfg.srm_bug
        drop = (
            (treated == 1)
            & (resolve_minutes > float(s["drop_treatment_slower_than_minutes"]))
            & (rng.random(n) < float(s["drop_rate"]))
        )
        dropped = int(drop.sum())
        cases = cases.subset(~drop)

    truth = {
        "label": DATA_LABEL,
        "design": design,
        "seed": seed,
        "reviewers": n_rev,
        "days": n_days,
        "cases": len(cases),
        "effect_scale": effect_scale,
        "novelty_boost": novelty_boost,
        "spillover": leak,
        "srm_bug": srm_bug,
        "srm_dropped_cases": dropped,
        "ate_long_run": float(cases.true_cate.mean()),
        "ate_in_window": float(cases.true_effect_today.mean()),
        "resolve_log_effect": effect_scale * float(r["treatment"]),
        "wrong_action_given_convert": {"control": wrong_c, "treatment": wrong_t},
        "templates": len(templates),
    }
    return Simulation(cases, truth)


def write_simulation(sim: Simulation, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cases.parquet"
    pq.write_table(sim.cases.to_table(), path)
    (out_dir / "truth.json").write_text(json.dumps(sim.truth, indent=2) + "\n", "utf-8")
    return path


def read_simulation(out_dir: Path) -> Simulation:
    cases = Cases.from_table(pq.read_table(out_dir / "cases.parquet"))
    truth = json.loads((out_dir / "truth.json").read_text("utf-8"))
    return Simulation(cases, truth)
