"""Stage 2 data-quality rules. Each rule emits a change only on onset (ok -> violation)."""

from __future__ import annotations

from typing import Any

from d2d.pipeline.changelog import Change
from d2d.pipeline.semantics import Semantics


def _violations(prev: dict[str, Any], row: dict[str, Any], skew_s: int) -> dict[str, str | None]:
    """Rule name -> detail string if violated at ``row`` (given the device's previous row)."""
    delta = row["energy_kwh_total"] - prev["energy_kwh_total"]
    skew = abs((row["reported_ts"] - row["snapshot_ts"]).total_seconds())
    return {
        "negative_energy_delta": f"delta={delta:.3f} kWh" if delta < 0 else None,
        "clock_skew": f"skew={skew:.0f}s" if skew > skew_s else None,
        "duplicate_device": f"rows={row['dup_count']}" if row["dup_count"] > 1 else None,
    }


def check_quality(rows: list[dict[str, Any]], semantics: Semantics) -> list[Change]:
    """Rows must be sorted by ``(device_id, snapshot_ts)``; a device's first row is the baseline."""
    changes: list[Change] = []
    skew_s = semantics.clock_skew_threshold_s
    prev: dict[str, Any] | None = None
    active: dict[str, bool] = {}
    for row in rows:
        if prev is None or prev["device_id"] != row["device_id"]:
            prev, active = row, {}
            continue
        for rule, detail in _violations(prev, row, skew_s).items():
            was_active = active.get(rule, False)
            active[rule] = detail is not None
            if detail is None or was_active:
                continue
            prop = f"dq.{rule}"
            label = semantics.classify(prop, "ok", detail)
            if label is not None:
                changes.append(
                    Change(
                        device_id=row["device_id"],
                        property=prop,
                        old_value="ok",
                        new_value=detail,
                        change_kind=label[0],
                        semantic_tag=label[1],
                        snapshot_ts_before=prev["snapshot_ts"],
                        snapshot_ts_after=row["snapshot_ts"],
                        snapshot_run_id=row["snapshot_run_id"],
                    )
                )
        prev = row
    return changes
