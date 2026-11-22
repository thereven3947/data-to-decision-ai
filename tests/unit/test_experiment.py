from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from d2d.config import load_categories, load_household
from d2d.experiment.analyze import aa_simulator, analyze, decide, power_table
from d2d.experiment.observational import confounding_demo, ols_adjusted
from d2d.experiment.simulate import (
    CaseTemplate,
    ExperimentConfig,
    load_experiment,
    load_severity,
    read_simulation,
    simulate,
    templates_from_config,
    templates_from_stories,
    write_simulation,
)
from d2d.experiment.stats import (
    aa_peeking,
    benjamini_hochberg,
    cuped,
    diff_in_means,
    holm,
    icc_anova,
    mde_proportion,
    n_per_arm_proportion,
    obrien_fleming_bounds,
    sequential_test,
    srm_chi_square,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"
SEED = 7  # tests never use the pre-registered seed in config/experiment.toml


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(CONFIG / "experiment.toml")


@pytest.fixture(scope="module")
def templates() -> list[CaseTemplate]:
    severity, default = load_severity(CONFIG / "notifications.toml")
    return templates_from_config(
        load_household(CONFIG / "household.toml"),
        load_categories(CONFIG / "categories.toml"),
        severity,
        default,
    )


def test_holm_and_bh_match_textbook_values() -> None:
    p = [0.01, 0.04, 0.03, 0.005]
    assert holm(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert benjamini_hochberg(p) == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_sample_size_and_mde_are_inverse() -> None:
    n = n_per_arm_proportion(0.3, 0.02)
    assert n == 8242
    assert mde_proportion(0.3, n) == pytest.approx(0.02, rel=1e-3)
    assert mde_proportion(0.3, n, deff=4.0) == pytest.approx(0.04, rel=1e-3)


def test_obrien_fleming_bounds_match_published_values() -> None:
    # Jennison & Turnbull, Table 2.3 (K = 5, alpha = 0.05 two-sided): 4.56 ... 2.04.
    bounds = obrien_fleming_bounds([0.2, 0.4, 0.6, 0.8, 1.0], 0.05, sims=400_000, seed=1)
    assert bounds[0] == pytest.approx(4.56, abs=0.05)
    assert bounds[-1] == pytest.approx(2.04, abs=0.03)
    assert obrien_fleming_bounds([1.0])[0] == pytest.approx(1.96, abs=0.02)
    assert sequential_test([1.0, 3.0, 3.0, 2.0, 2.1], bounds).stopped_at_look == 3


def test_peeking_inflates_false_positives_and_obrien_fleming_controls_them() -> None:
    r = aa_peeking(sims=4000, looks=10, seed=SEED)
    assert abs(r.fpr_fixed_horizon - 0.05) < 0.015
    assert r.fpr_naive_peeking > 0.15
    assert abs(r.fpr_obrien_fleming - 0.05) < 0.015


def test_simulation_is_deterministic(cfg: ExperimentConfig, templates: list[CaseTemplate]) -> None:
    a = simulate(cfg, templates, "reviewer", seed=SEED, reviewers=20, days=7)
    b = simulate(cfg, templates, "reviewer", seed=SEED, reviewers=20, days=7)
    assert a.truth == b.truth
    for f in dataclasses.fields(a.cases):
        assert np.array_equal(getattr(a.cases, f.name), getattr(b.cases, f.name))


def test_simulation_round_trips(
    cfg: ExperimentConfig, templates: list[CaseTemplate], tmp_path: Path
) -> None:
    sim = simulate(cfg, templates, "case", seed=SEED, reviewers=10, days=3)
    write_simulation(sim, tmp_path)
    back = read_simulation(tmp_path)
    assert back.truth == sim.truth
    assert np.array_equal(back.cases.severity, sim.cases.severity)
    assert np.allclose(back.cases.true_cate, sim.cases.true_cate)


def test_reviewer_randomization_assigns_whole_reviewers(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    c = simulate(cfg, templates, "reviewer", seed=SEED).cases
    per_reviewer = {int(r): set(c.treated[c.reviewer == r].tolist()) for r in np.unique(c.reviewer)}
    assert all(len(v) == 1 for v in per_reviewer.values())
    assert sum(v == {1} for v in per_reviewer.values()) == cfg.reviewers // 2


def test_true_effect_is_heterogeneous_as_configured(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    c = simulate(cfg, templates, "reviewer", seed=SEED).cases
    junior = c.true_cate[c.experience < 3].mean()
    senior = c.true_cate[c.experience > 9].mean()
    assert junior > senior + 0.03
    assert c.true_cate[c.severity == "info"].mean() > c.true_cate[c.severity == "alert"].mean()


def test_srm_fires_on_injected_bug_only(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    clean = analyze(simulate(cfg, templates, "reviewer", seed=SEED), "reviewer")
    buggy = analyze(simulate(cfg, templates, "reviewer", seed=SEED, srm_bug=True), "reviewer")
    assert not clean["srm"]["flagged"]
    assert buggy["srm"]["flagged"]
    assert buggy["srm"]["cases"]["p_value"] < 1e-4
    assert decide(buggy)[0] == "invalid"
    assert srm_chi_square([5000, 5000], [0.5, 0.5]).p_value == pytest.approx(1.0)


def test_cuped_reduces_variance(cfg: ExperimentConfig, templates: list[CaseTemplate]) -> None:
    c = simulate(cfg, templates, "reviewer", seed=SEED).cases
    raw = diff_in_means(c.converted, c.treated, c.reviewer)
    y_adj, theta = cuped(c.converted, c.pre_conversion)
    adj = diff_in_means(y_adj, c.treated, c.reviewer)
    assert theta > 0
    assert adj.se < 0.75 * raw.se
    assert abs(adj.estimate - c.true_effect_today.mean()) < 3 * adj.se


def test_cluster_robust_se_is_wider_than_iid_with_icc(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    c = simulate(cfg, templates, "reviewer", seed=SEED, effect_scale=0.0).cases
    assert icc_anova(c.converted, c.reviewer) > 0.02
    iid = diff_in_means(c.converted, c.treated)
    clustered = diff_in_means(c.converted, c.treated, c.reviewer)
    assert clustered.se > 2 * iid.se


def test_ols_adjusted_se_survives_redundant_dummies() -> None:
    rng = np.random.default_rng(SEED)
    n = 4000
    clusters = rng.integers(0, 80, n)
    t = rng.integers(0, 2, n).astype(np.float64)
    group = rng.integers(0, 4, n)
    onehot = np.eye(4)[group]
    y = 0.05 * t + 0.1 * group + rng.normal(0, 1, n)
    full = ols_adjusted(y, t, onehot, clusters)  # dummy-variable trap with the intercept
    ref = ols_adjusted(y, t, onehot[:, 1:], clusters)
    assert full["estimate"] == pytest.approx(ref["estimate"])
    assert full["se"] == pytest.approx(ref["se"], rel=1e-9)


def test_aa_holds_false_positive_rate_near_alpha(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    small = dataclasses.replace(cfg, reviewers=60, days=14)
    r = aa_simulator(small, templates, sims=300, seed=SEED)
    fpr = r["false_positive_rate"]
    tol = 3 * r["monte_carlo_se"]
    assert abs(fpr["reviewer_cluster"] - 0.05) < tol + 0.01
    assert abs(fpr["reviewer_cluster_cuped"] - 0.05) < tol + 0.01
    assert abs(fpr["case"] - 0.05) < tol
    assert fpr["reviewer_welch"] > 0.15  # ignoring clustering over-rejects


def test_case_design_spillover_attenuates_effect(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    sim = simulate(cfg, templates, "case", seed=SEED)
    r = analyze(sim, "case")
    expected = (1 - sim.truth["spillover"]) * sim.truth["ate_in_window"]
    est = r["conversion_cuped"]
    assert est["ci_low"] <= expected <= est["ci_high"]
    assert est["ci_high"] < sim.truth["ate_in_window"]


def test_analysis_covers_truth_and_reports_decision(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    sim = simulate(cfg, templates, "reviewer", seed=SEED)
    r = analyze(sim, "reviewer")
    est = r["conversion_cuped"]
    assert est["ci_low"] <= sim.truth["ate_in_window"] <= est["ci_high"]
    assert r["cuped_variance_reduction"] > 0.3
    assert len(r["sequential"]["z"]) == 4
    assert r["multiple_testing"]["holm"][0] >= r["multiple_testing"]["p"][0]
    assert decide(r)[0] in {"ship", "iterate", "dont_ship"}


def test_power_table_shows_cluster_penalty_and_cuped_gain(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    pre = simulate(cfg, templates, "reviewer", seed=SEED, effect_scale=0.0).cases
    rows = {(r["design"], r["cuped"]): r for r in power_table(pre, cfg.reviewers, len(pre))["rows"]}
    assert rows[("reviewer", False)]["mde"] > 2 * rows[("case", False)]["mde"]
    assert rows[("reviewer", True)]["mde"] < 0.6 * rows[("reviewer", False)]["mde"]


def test_confounding_naive_is_biased_adjusted_recovers_truth(
    cfg: ExperimentConfig, templates: list[CaseTemplate]
) -> None:
    c = simulate(cfg, templates, "observational", seed=SEED).cases
    r = confounding_demo(c, seed=SEED, with_forest=False)
    for exposure in ("treated", "history"):
        row = r["exposures"][exposure]
        assert not row["naive"]["covers_truth"]
        assert abs(row["naive"]["bias"]) > 0.03
        assert row["aipw"]["covers_truth"]
        assert row["ols_adjusted"]["covers_truth"]
        assert abs(row["aipw"]["bias"]) < 0.015


def test_templates_from_pipeline_stories(run_data: Path) -> None:
    severity, default = load_severity(CONFIG / "notifications.toml")
    t = templates_from_stories(
        run_data, load_household(CONFIG / "household.toml"), severity, default
    )
    assert len(t) > 5
    assert {x.severity for x in t} <= {"info", "notice", "alert"}


def test_cli_simulate_analyze_power(tmp_path: Path) -> None:
    from d2d.cli import app

    runner = CliRunner()
    out = tmp_path / "rev"
    common = ["--config", str(CONFIG), "--data-dir", str(tmp_path / "none")]
    r = runner.invoke(app, ["experiment", "simulate", "--seed", "3", "--out", str(out), *common])
    assert r.exit_code == 0, r.output
    assert "SIMULATED DATA" in r.output
    r = runner.invoke(app, ["experiment", "analyze", "--in", str(out)])
    assert r.exit_code == 0, r.output
    assert "decision:" in r.output
    assert (out / "analysis.json").is_file()
    r = runner.invoke(app, ["experiment", "power", "--seed", "3", *common])
    assert r.exit_code == 0, r.output
    assert "MDE" in r.output
    assert not math.isnan(float(r.output.split("baseline ")[1].split()[0]))
