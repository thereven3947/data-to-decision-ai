"""Context rules over the CDC stream (M5-03). First rule type: duration ("left open too long").

Configured in ``semantics.toml``::

    [[duration]]
    property = "door"          # CDC property
    value = "open"             # state that must last
    min_minutes = 30
    kinds = ["garage_door"]    # optional: limit to device kinds
    change_kind = "operational"
    tag = "garage_left_open"

The change is emitted at the moment the threshold is crossed (start + min_minutes), once per
episode, if the state lasts that long before it changes or before ``until``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from d2d.pipeline.cdc import CDC_RUN_ID
from d2d.pipeline.changelog import Change
from d2d.pipeline.semantics import DurationRule


def duration_changes(
    rows: list[dict[str, Any]],
    rules: list[DurationRule],
    device_kinds: dict[str, str],
    until: datetime | None = None,
) -> list[Change]:
    """``rows`` are CDC rows sorted by event_ts."""
    changes: list[Change] = []
    for rule in rules:
        limit = timedelta(minutes=rule.min_minutes)
        started: dict[str, datetime] = {}
        ended: list[tuple[str, datetime, datetime]] = []
        for r in rows:
            dev = r["device_id"]
            if r["property"] != rule.property:
                continue
            if rule.kinds and device_kinds.get(dev) not in rule.kinds:
                continue
            if r["new_value"] == rule.value:
                started[dev] = r["event_ts"]
            elif dev in started:
                ended.append((dev, started.pop(dev), r["event_ts"]))
        if until is not None:
            ended += [(dev, t0, until) for dev, t0 in started.items()]
        for dev, t0, t1 in ended:
            if t1 - t0 < limit:
                continue
            ts = t0 + limit
            changes.append(
                Change(
                    device_id=dev,
                    property=f"{rule.property}.duration",
                    old_value=rule.value,
                    new_value=f"{rule.value}>{rule.min_minutes}m",
                    change_kind=rule.change_kind,
                    semantic_tag=rule.tag,
                    snapshot_ts_before=t0,
                    snapshot_ts_after=ts,
                    snapshot_run_id=CDC_RUN_ID,
                )
            )
    return changes
