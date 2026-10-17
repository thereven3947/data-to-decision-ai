from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from d2d.pipeline.context import duration_changes
from d2d.pipeline.semantics import DurationRule

T0 = datetime(2026, 10, 1, 8, tzinfo=UTC)
RULE = DurationRule("door", "open", 30, "operational", "garage_left_open", ("garage_door",))
KINDS = {"garage_door": "garage_door", "shed": "shed_door"}


def _row(dev: str, minutes: int, old: str, new: str) -> dict[str, Any]:
    ts = T0 + timedelta(minutes=minutes)
    return {
        "device_id": dev,
        "property": "door",
        "old_value": old,
        "new_value": new,
        "event_ts": ts,
    }


def test_duration_fires_once_when_threshold_crossed() -> None:
    rows = [
        _row("garage_door", 0, "closed", "open"),
        _row("garage_door", 3, "open", "closed"),  # commute: quiet
        _row("shed", 10, "closed", "open"),  # other kind: ignored
        _row("garage_door", 60, "closed", "open"),
        _row("garage_door", 105, "open", "closed"),  # 45 min -> fires at +30
        _row("garage_door", 200, "closed", "open"),  # still open at `until`
    ]
    until = T0 + timedelta(minutes=260)
    changes = duration_changes(rows, [RULE], KINDS, until)
    assert [(c.snapshot_ts_after - T0, c.semantic_tag) for c in changes] == [
        (timedelta(minutes=90), "garage_left_open"),
        (timedelta(minutes=230), "garage_left_open"),
    ]
    assert duration_changes(rows, [RULE], KINDS, until=None)[0].property == "door.duration"


def _cdc(dev: str, prop: str, minutes: float, old: str, new: str) -> dict[str, Any]:
    return {
        "device_id": dev,
        "property": prop,
        "old_value": old,
        "new_value": new,
        "event_ts": T0 + timedelta(minutes=minutes),
    }


def test_zone_rules_fire_once_per_episode_and_duration_counts_only_zone_time() -> None:
    from pathlib import Path

    from d2d.pipeline.context import context_changes
    from d2d.pipeline.semantics import load_semantics

    sem = load_semantics(Path(__file__).resolve().parents[2] / "config" / "semantics.toml")
    kinds = {"cam": "ring_camera", "lamp": "light_switch", "home": "home"}
    rows = [
        _cdc("cam", "motion", 10, "clear", "detected"),  # before away: routine
        _cdc("lamp", "power", 50, "off", "on"),  # left on just before leaving
        _cdc("home", "away_mode", 60, "off", "on"),
        _cdc("cam", "motion", 90, "clear", "detected"),  # fires
        _cdc("cam", "motion", 95, "detected", "clear"),
        _cdc("cam", "motion", 120, "clear", "detected"),  # same episode: quiet
        _cdc("home", "away_mode", 400, "on", "off"),
        _cdc("lamp", "power", 420, "on", "off"),
    ]
    out = context_changes(rows, sem, kinds, until=T0 + timedelta(hours=8), tz="UTC")
    tags = [(c.semantic_tag, c.snapshot_ts_after - T0) for c in out]
    assert ("motion_while_away", timedelta(minutes=90)) in tags
    assert sum(t == "motion_while_away" for t, _ in tags) == 1
    # light counted only from away start (60) -> fires at 60 + 240
    assert ("lights_left_on_away", timedelta(minutes=300)) in tags


def test_missed_visitor_needs_person_and_no_answer() -> None:
    from d2d.pipeline.context import sequence_changes
    from d2d.pipeline.semantics import SequenceRule

    rule = SequenceRule(
        "missed_visitor",
        "operational",
        "visit",
        "missed",
        "button",
        "pressed",
        ("ring_doorbell",),
        "person",
        "detected",
        ("ring_camera",),
        60,
        "call",
        "answered",
        120,
    )
    kinds = {"bell": "ring_doorbell", "cam": "ring_camera"}
    missed = [
        _cdc("bell", "button", 0, "idle", "pressed"),
        _cdc("cam", "person", 0.2, "clear", "detected"),
    ]
    answered = [*missed, _cdc("bell", "call", 1, "idle", "answered")]
    nobody = [_cdc("bell", "button", 0, "idle", "pressed")]
    assert [c.property for c in sequence_changes(missed, [rule], kinds)] == ["visit"]
    assert sequence_changes(answered, [rule], kinds) == []
    assert sequence_changes(nobody, [rule], kinds) == []


def test_reconcile_flags_missed_cdc_event_on_onset() -> None:
    from pathlib import Path

    from d2d.pipeline.reconcile import reconcile_changes
    from d2d.pipeline.semantics import load_semantics

    sem = load_semantics(Path(__file__).resolve().parents[2] / "config" / "semantics.toml")

    def snap(minutes: int, state: str) -> dict[str, Any]:
        return {
            "device_id": "lamp",
            "kind": "light_switch",
            "snapshot_ts": T0 + timedelta(minutes=minutes),
            "state": state,
            "snapshot_run_id": f"run_{minutes}",
        }

    snaps = [
        snap(0, "idle"),
        snap(15, "running"),
        snap(30, "idle"),
        snap(45, "idle"),
        snap(60, "idle"),
    ]
    cdc = [_cdc("lamp", "power", 10, "off", "on")]  # the "off" around minute 25 never arrived
    out = reconcile_changes(snaps, cdc, sem)
    assert [(c.property, c.snapshot_ts_after - T0) for c in out] == [
        ("dq.cdc_gap", timedelta(minutes=30))
    ]
    assert reconcile_changes(snaps, [*cdc, _cdc("lamp", "power", 25, "on", "off")], sem) == []
