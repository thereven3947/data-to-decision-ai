from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from d2d.pipeline.quality import check_quality
from d2d.pipeline.semantics import load_semantics

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _row(i: int, energy: float, skew_s: int = 0, dups: int = 1) -> dict[str, Any]:
    ts = T0 + timedelta(minutes=15 * i)
    return {
        "device_id": "plug",
        "snapshot_ts": ts,
        "reported_ts": ts + timedelta(seconds=skew_s),
        "snapshot_run_id": f"run_{i}",
        "energy_kwh_total": energy,
        "dup_count": dups,
    }


def test_rules_fire_on_onset_only() -> None:
    semantics = load_semantics(CONFIG_DIR / "semantics.toml")
    rows = [
        _row(0, 5.0, skew_s=900),  # baseline row: never reported
        _row(1, 5.1, skew_s=900),  # skew already present at baseline -> onset here
        _row(2, 5.2, skew_s=900),  # still skewed -> no new event
        _row(3, 0.0, dups=2),  # energy reset + duplicate
        _row(4, 0.1, dups=2),  # duplicate continues -> no new event
        _row(5, 0.2),
        _row(6, 0.1, skew_s=30),  # second reset; skew under threshold
    ]
    got = [(c.property, c.snapshot_run_id) for c in check_quality(rows, semantics)]
    assert got == [
        ("dq.clock_skew", "run_1"),
        ("dq.negative_energy_delta", "run_3"),
        ("dq.duplicate_device", "run_3"),
        ("dq.negative_energy_delta", "run_6"),
    ]
