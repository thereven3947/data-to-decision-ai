"""Stage 3 for event-native devices: CDC rows -> change events through ``semantics.toml``.

CDC rows already have the change-event shape, so they only need classification. Routine changes
(automation light on at dusk, garage on a commute) match no rule and are dropped, exactly like
idle <-> running in the snapshot diff. Context rules (M5-03) will add the meaningful ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from d2d.pipeline.changelog import Change
from d2d.pipeline.semantics import Semantics

CDC_RUN_ID = "cdc"


def load_cdc(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "cdc" / "events.parquet"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def cdc_changes(rows: list[dict[str, Any]], semantics: Semantics) -> list[Change]:
    changes: list[Change] = []
    for r in rows:
        label = semantics.classify(r["property"], r["old_value"], r["new_value"])
        if label is None:
            continue
        changes.append(
            Change(
                device_id=r["device_id"],
                property=r["property"],
                old_value=r["old_value"],
                new_value=r["new_value"],
                change_kind=label[0],
                semantic_tag=label[1],
                snapshot_ts_before=r["event_ts"],
                snapshot_ts_after=r["event_ts"],
                snapshot_run_id=CDC_RUN_ID,
            )
        )
    return changes
