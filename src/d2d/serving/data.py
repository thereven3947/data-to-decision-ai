"""Simulated story datasets for serving: training runs, traffic runs and injected shifts.

Every run is a seeded `d2d simulate` + `d2d run` with ground-truth labels (all data is simulated).
Splits are by run, and the golden run (pipeline seed and start) is never used here.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from d2d.config import load_household, load_pipeline
from d2d.hitl.training_set import Example, build_training_set
from d2d.pipeline.events import run_pipeline
from d2d.pipeline.linking import load_linking
from d2d.pipeline.semantics import load_semantics
from d2d.simulator import simulate, write_cdc, write_ground_truth, write_snapshots
from d2d.simulator.cdc import mirror_cdc_state, plan_cdc

TRAIN_START = datetime(2026, 2, 2, tzinfo=UTC)
TRAFFIC_START = datetime(2026, 6, 1, tzinfo=UTC)
SHIFTS = ("none", "linking_bug", "category_mix")


def simulate_run(cfg_dir: Path, out: Path, seed: int, start: datetime, days: int) -> Path:
    """Simulate one household timeline into ``out`` (skipped if it already exists)."""
    if (out / "stories" / "stories.parquet").exists():
        return out
    pipeline = load_pipeline(cfg_dir / "pipeline.toml")
    if (seed, start) == (pipeline.seed, pipeline.start):
        raise ValueError("refusing to simulate the golden run for training or traffic")
    household = load_household(cfg_dir / "household.toml")
    result = simulate(
        household,
        start=start,
        days=days,
        cadence_minutes=pipeline.cadence_minutes,
        seed=seed,
        scenario_ids=None,
    )
    cdc = plan_cdc(
        household, start=start, days=days, seed=seed, cadence_minutes=pipeline.cadence_minutes
    )
    mirror_cdc_state(result.rows, cdc.true_rows, household)
    write_snapshots(result.rows, out)
    write_cdc(cdc.rows, out)
    write_ground_truth(result.ground_truth + cdc.truth, out)
    run_pipeline(
        out,
        load_semantics(cfg_dir / "semantics.toml"),
        load_linking(cfg_dir / "linking.toml"),
        household.id,
        household.timezone,
    )
    return out


def labeled_runs(
    cfg_dir: Path, data_dir: Path, seeds: tuple[int, ...], start: datetime, days: int, prefix: str
) -> list[Example]:
    """Labeled stories of several runs; story ids are prefixed with the run so they stay unique."""
    household = load_household(cfg_dir / "household.toml")
    rows: list[Example] = []
    for i, seed in enumerate(seeds):
        name = f"{prefix}-{seed}"
        out = simulate_run(cfg_dir, data_dir / name, seed, start + timedelta(days=7 * i), days)
        for r in build_training_set(out, household, None, True):
            rows.append({**r, "story_id": f"{name}/{r['story_id']}"})
    return rows


def shift(rows: list[Example], kind: str, seed: int = 0) -> list[Example]:
    """Inject a known shift into labeled traffic.

    - ``linking_bug``: an upstream release stops writing link reasons (covariate shift).
    - ``category_mix``: one category becomes most of the traffic (prior shift).
    """
    if kind == "none":
        return list(rows)
    if kind == "linking_bug":
        return [{**r, "link_reasons": []} for r in rows]
    if kind == "category_mix":
        rng = random.Random(seed)
        rarest = Counter(r["category"] for r in rows).most_common()[-1][0]
        rare = [r for r in rows if r["category"] == rarest]
        return rows + [rng.choice(rare) for _ in range(2 * len(rows))]
    raise ValueError(f"unknown shift {kind!r}; expected one of {SHIFTS}")
