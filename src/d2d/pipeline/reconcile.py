"""M5-06 ``dq.cdc_gap``: snapshots and the CDC feed must agree on mirrored device state.

For each ``[[reconcile]]`` rule, the CDC state of ``cdc_property`` at a snapshot time (last CDC
change at or before it) must map to the snapshot's ``snapshot_field``. A missed CDC event (the
device changed, the feed did not say so) or a phantom one (the feed changed, the device did not)
shows up as a mismatch; the change is emitted on onset only, like the other data-quality rules.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from typing import Any

from d2d.pipeline.changelog import Change
from d2d.pipeline.semantics import Semantics

PROPERTY = "dq.cdc_gap"


def reconcile_changes(
    snapshots: list[dict[str, Any]], cdc_rows: list[dict[str, Any]], semantics: Semantics
) -> list[Change]:
    """``snapshots`` sorted by ``(device_id, snapshot_ts)``; ``cdc_rows`` by ``event_ts``."""
    changes: list[Change] = []
    for rule in semantics.reconciles:
        timeline: dict[str, tuple[list[datetime], list[str]]] = defaultdict(lambda: ([], []))
        for r in cdc_rows:
            if r["property"] == rule.cdc_property:
                stamps, values = timeline[r["device_id"]]
                stamps.append(r["event_ts"])
                values.append(r["new_value"])
        mapped = set(rule.mapping.values())
        prev: dict[str, tuple[datetime, bool]] = {}
        for row in snapshots:
            if row["kind"] not in rule.kinds:
                continue
            dev, ts, actual = row["device_id"], row["snapshot_ts"], row[rule.snapshot_field]
            if actual not in mapped:  # error / offline: not comparable
                prev.pop(dev, None)
                continue
            stamps, values = timeline[dev]
            i = bisect_right(stamps, ts)
            cdc_value = values[i - 1] if i else rule.initial
            mismatch = rule.mapping.get(cdc_value) != actual
            before = prev.get(dev)
            if mismatch and before is not None and not before[1]:
                label = semantics.classify(PROPERTY, "ok", "gap")
                if label is not None:
                    changes.append(
                        Change(
                            device_id=dev,
                            property=PROPERTY,
                            old_value="ok",
                            new_value=f"cdc {rule.cdc_property}={cdc_value}, snapshot={actual}",
                            change_kind=label[0],
                            semantic_tag=label[1],
                            snapshot_ts_before=before[0],
                            snapshot_ts_after=ts,
                            snapshot_run_id=row["snapshot_run_id"],
                        )
                    )
            prev[dev] = (ts, mismatch)
    return changes
