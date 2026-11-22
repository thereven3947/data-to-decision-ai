"""Causal-forest tests; need the optional group: uv sync --group experiment."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("econml")

from d2d.config import load_categories, load_household
from d2d.experiment.hte import cross_fit_cate, validate_cate
from d2d.experiment.observational import confounding_demo
from d2d.experiment.simulate import (
    Design,
    Simulation,
    load_experiment,
    load_severity,
    simulate,
    templates_from_config,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"
SEED = 7


def _sim(design: Design, spillover: float | None = None) -> Simulation:
    severity, default = load_severity(CONFIG / "notifications.toml")
    templates = templates_from_config(
        load_household(CONFIG / "household.toml"),
        load_categories(CONFIG / "categories.toml"),
        severity,
        default,
    )
    cfg = load_experiment(CONFIG / "experiment.toml")
    return simulate(cfg, templates, design, seed=SEED, spillover=spillover)


def test_causal_forest_recovers_known_effect() -> None:
    # Case-level randomization without spillover isolates estimator accuracy from design noise.
    c = _sim("case", spillover=0.0).cases
    tau, lo, hi = cross_fit_cate(c, folds=3, seed=SEED, n_estimators=200)
    v = validate_cate(c, tau, lo, hi)
    assert v["pearson_r"] > 0.5
    assert abs(v["mean_estimated"] - v["mean_true"]) < 0.01
    # A ~2.5k-case segment's difference in means has SE ~1.8 pp; allow ~2.5 SE.
    for rows in v["per_segment"].values():
        for row in rows:
            assert row["abs_error"] < 0.045, row
    assert 0.5 < v["calibration_slope_bins"] < 1.5


def test_causal_forest_ate_adjusts_for_confounding() -> None:
    c = _sim("observational").cases
    r = confounding_demo(c, seed=SEED, with_forest=True)
    for exposure in ("treated", "history"):
        row = r["exposures"][exposure]
        assert row["causal_forest"]["covers_truth"], row
        assert abs(row["causal_forest"]["bias"]) < abs(row["naive"]["bias"]) / 3
