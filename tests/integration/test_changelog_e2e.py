from __future__ import annotations

from pathlib import Path

from d2d.config import Household, PipelineConfig
from d2d.pipeline.changelog import build_changelog
from d2d.pipeline.semantics import load_semantics
from d2d.pipeline.snapshots import load_snapshots
from d2d.simulator import simulate, write_snapshots

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_changelog_recovers_planted_changes_without_noise(
    tmp_path: Path, household: Household, pipeline: PipelineConfig
) -> None:
    result = simulate(
        household,
        start=pipeline.start,
        days=pipeline.days,
        cadence_minutes=pipeline.cadence_minutes,
        seed=pipeline.seed,
    )
    write_snapshots(result.rows, tmp_path)
    rows = load_snapshots(tmp_path)
    changes = build_changelog(rows, load_semantics(CONFIG_DIR / "semantics.toml"))

    expected = {
        (e["device_id"], e["property"], e["ts"]) for s in result.ground_truth for e in s["events"]
    }
    found = {(c.device_id, c.property, c.snapshot_ts_after.isoformat()) for c in changes}
    assert found - expected == set(), "noise leaked into the changelog"
    assert expected - found == set(), "planted changes were missed"

    tags = {c.semantic_tag for c in changes}
    assert {"schedule_shift_off_peak", "device_added", "device_removed", "network_change"} <= tags
    assert {c.semantic_tag for c in changes if c.change_kind == "data_quality"} == {
        "dq_negative_energy_delta",
        "dq_clock_skew",
        "dq_duplicate_device",
    }


def test_duplicate_rows_are_collapsed(
    tmp_path: Path, household: Household, pipeline: PipelineConfig
) -> None:
    result = simulate(
        household,
        start=pipeline.start,
        days=pipeline.days,
        cadence_minutes=pipeline.cadence_minutes,
        seed=pipeline.seed,
        scenario_ids={"duplicate_device"},
    )
    write_snapshots(result.rows, tmp_path)
    rows = load_snapshots(tmp_path)
    assert len(rows) == len(result.rows) - 2
    assert {r["device_id"] for r in rows if r["dup_count"] > 1} == {"smart_plug_lamp"}
