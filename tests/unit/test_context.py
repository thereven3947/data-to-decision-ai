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
