"""Stage 2: semantic changelog, a typed diff of consecutive snapshots per device."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from d2d.pipeline.semantics import Semantics

SCALAR_PROPERTIES = (
    "room",
    "circuit_id",
    "network_id",
    "firmware_version",
    "mode",
    "setpoint",
    "state",
    "error_code",
    "rssi_dbm",
)
JSON_PROPERTIES = ("schedule", "settings")


@dataclass(frozen=True)
class Change:
    device_id: str
    property: str
    old_value: str | None
    new_value: str | None
    change_kind: str
    semantic_tag: str
    snapshot_ts_before: datetime
    snapshot_ts_after: datetime
    snapshot_run_id: str

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def fmt(value: Any) -> str | None:
    """Canonical string form so values compare and match rules consistently."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def flatten(row: dict[str, Any]) -> dict[str, str | None]:
    props = {p: fmt(row.get(p)) for p in SCALAR_PROPERTIES}
    for name in JSON_PROPERTIES:
        raw = row.get(name)
        for key, value in (json.loads(raw) if raw else {}).items():
            props[f"{name}.{key}"] = fmt(value)
    return props


def diff_properties(rows: list[dict[str, Any]], semantics: Semantics) -> list[Change]:
    """Compare each device row with that device's previous row; rows sorted by device, ts."""
    changes: list[Change] = []
    prev: dict[str, Any] | None = None
    for row in rows:
        if prev is not None and prev["device_id"] == row["device_id"]:
            before, after = flatten(prev), flatten(row)
            for prop in sorted(before.keys() | after.keys()):
                old, new = before.get(prop), after.get(prop)
                if old == new:
                    continue
                label = semantics.classify(prop, old, new)
                if label is not None:
                    changes.append(_change(row, prev, prop, old, new, label))
        prev = row
    return changes


def diff_presence(rows: list[dict[str, Any]], semantics: Semantics) -> list[Change]:
    """Device added/removed between consecutive runs (the first run is the baseline)."""
    runs = sorted({(r["snapshot_ts"], r["snapshot_run_id"]) for r in rows})
    present: dict[str, set[datetime]] = defaultdict(set)
    for r in rows:
        present[r["device_id"]].add(r["snapshot_ts"])
    changes: list[Change] = []
    for (ts_before, _), (ts_after, run_after) in pairwise(runs):
        for device_id in sorted(present):
            was, now = ts_before in present[device_id], ts_after in present[device_id]
            if was == now:
                continue
            old, new = ("present", "absent") if was else ("absent", "present")
            label = semantics.classify("presence", old, new)
            if label is not None:
                changes.append(
                    Change(device_id, "presence", old, new, *label, ts_before, ts_after, run_after)
                )
    return changes


def _change(
    row: dict[str, Any],
    prev: dict[str, Any],
    prop: str,
    old: str | None,
    new: str | None,
    label: tuple[str, str],
) -> Change:
    return Change(
        device_id=row["device_id"],
        property=prop,
        old_value=old,
        new_value=new,
        change_kind=label[0],
        semantic_tag=label[1],
        snapshot_ts_before=prev["snapshot_ts"],
        snapshot_ts_after=row["snapshot_ts"],
        snapshot_run_id=row["snapshot_run_id"],
    )


def build_changelog(rows: list[dict[str, Any]], semantics: Semantics) -> list[Change]:
    changes = diff_properties(rows, semantics) + diff_presence(rows, semantics)
    return sorted(changes, key=lambda c: (c.snapshot_ts_after, c.device_id, c.property))
