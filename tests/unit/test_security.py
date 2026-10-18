from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from d2d.cli import app
from d2d.config import Household
from d2d.security import (
    ATTACKS,
    attack_event,
    evaluate_security,
    read_events,
    score_events,
    write_alerts,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"
NOON_PT = datetime(2026, 10, 2, 19, 0, tzinfo=UTC)  # 12:00 America/Los_Angeles
NIGHT_PT = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)  # 03:00 America/Los_Angeles


def _event(
    device: str,
    prop: str,
    old: str | None,
    new: str | None,
    ts: datetime = NOON_PT,
    kind: str = "config",
    eid: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": eid or f"{device}-{prop}-{ts:%H%M}",
        "device_id": device,
        "property": prop,
        "old_value": old,
        "new_value": new,
        "change_kind": kind,
        "semantic_tag": "x",
        "event_ts": ts,
        "snapshot_run_id": "t",
    }


def _reasons(events: list[dict[str, Any]], household: Household, eid: str) -> list[str]:
    return next(s.reasons for s in score_events(events, household) if s.event_id == eid)


def test_clean_simulated_stream_raises_no_alerts(run_data: Path, household: Household) -> None:
    scored = score_events(read_events(run_data), household)
    assert scored, "config/topology changes are scored"
    assert [s for s in scored if s.alert] == []
    assert all(s.event_id for s in scored)


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (_event("thermostat", "firmware_version", "1.3.0", "0.9.1", eid="a"), "firmware_downgrade"),
        (
            _event("dryer", "network_id", "router_mesh_1", "ap_beef", kind="topology", eid="a"),
            "unknown_network",
        ),
        (
            _event("dryer", "circuit_id", "circuit_laundry", "circuit_x", kind="topology", eid="a"),
            "unknown_circuit",
        ),
        (
            _event(
                "dryer", "circuit_id", "circuit_laundry", "circuit_living", kind="topology", eid="a"
            ),
            "undersized_circuit",
        ),
        (_event("fridge", "setpoint", "4", "30", eid="a"), "out_of_range"),
        (_event("ev_charger", "settings.max_current_a", "32", "56", eid="a"), "out_of_range"),
    ],
)
def test_baseline_rules(event: dict[str, Any], reason: str, household: Household) -> None:
    assert reason in _reasons([event], household, "a")


def test_in_range_and_upgrades_are_not_flagged(household: Household) -> None:
    events = [
        _event("thermostat", "firmware_version", "1.2.0", "1.3.0", eid="up"),
        _event("water_heater", "settings.max_temp_c", "60", "70", eid="limit"),
        _event(
            "ev_charger",
            "circuit_id",
            "circuit_garage",
            "circuit_utility",
            kind="topology",
            eid="same",
        ),
    ]
    for eid in ("up", "limit", "same"):
        reasons = _reasons(events, household, eid)
        assert not {"firmware_downgrade", "out_of_range", "undersized_circuit"} & set(reasons)


def test_campaign_at_night_is_a_planned_change(household: Household) -> None:
    rollout = [
        _event(d, "firmware_version", "1.2.0", "1.3.0", NIGHT_PT + timedelta(hours=i), eid=d)
        for i, d in enumerate(("smart_plug_lamp", "smart_plug_tv", "thermostat"))
    ]
    lone = _event(
        "dryer", "firmware_version", "1.0.0", "1.1.0", NIGHT_PT - timedelta(hours=2), eid="lone"
    )
    scored = {s.event_id: s for s in score_events([*rollout, lone], household)}
    assert all("campaign" in scored[d].reasons for d in ("smart_plug_lamp", "smart_plug_tv"))
    assert "off_hours" not in scored["smart_plug_lamp"].reasons
    assert {"off_hours", "lone_change"} <= set(scored["lone"].reasons)
    assert scored["lone"].score > scored["smart_plug_tv"].score


def test_attack_events_are_well_formed_and_deterministic(
    run_data: Path, household: Household
) -> None:
    events = read_events(run_data)
    a = [attack_event(k, events, household, random.Random(1)) for k in ATTACKS]
    b = [attack_event(k, events, household, random.Random(1)) for k in ATTACKS]
    assert a == b
    first, last = min(e["event_ts"] for e in events), max(e["event_ts"] for e in events)
    for e in a:
        assert e["old_value"] != e["new_value"]
        assert first <= e["event_ts"] <= last
        assert e["device_id"] in household.devices


def test_red_team_eval_meets_gates(run_data: Path, household: Household) -> None:
    r = evaluate_security(read_events(run_data), household, trials=10)
    assert r.injected == 10 * len(ATTACKS)
    assert r.precision_at_k >= 0.8 and r.recall >= 0.8
    assert r.false_alerts_per_trial <= 1.0 and r.clean_false_alerts == 0
    for kind in ("firmware_downgrade", "rogue_network", "setpoint_manipulation"):
        assert r.recall_by_attack[kind] >= 0.9


def test_write_alerts_round_trip(run_data: Path, household: Household, tmp_path: Path) -> None:
    scored = score_events(read_events(run_data), household)
    table = pq.read_table(write_alerts(scored, tmp_path)).to_pylist()
    assert len(table) == len(scored)
    assert table[0]["event_id"] == scored[0].event_id
    assert table[0]["reasons"] == scored[0].reasons


def test_cli_scan_and_eval(run_data: Path) -> None:
    runner = CliRunner()
    scan = runner.invoke(
        app, ["security", "scan", "--data-dir", str(run_data), "--config", str(CONFIG)]
    )
    assert scan.exit_code == 0, scan.output
    assert "0 alert(s)" in scan.output
    assert (run_data / "security" / "alerts.parquet").exists()
    ev = runner.invoke(
        app,
        ["eval", "security", "--data-dir", str(run_data), "--config", str(CONFIG), "--trials", "5"],
    )
    assert ev.exit_code == 0, ev.output
    assert "gate: PASS" in ev.output
