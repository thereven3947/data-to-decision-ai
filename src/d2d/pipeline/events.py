"""Stage 3: change events (one per changelog row, idempotent id) and the stage 1-3 run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from d2d.pipeline.cdc import cdc_changes, load_cdc
from d2d.pipeline.changelog import Change, build_changelog
from d2d.pipeline.context import duration_changes
from d2d.pipeline.linking import LinkingConfig, TopologyIndex, build_stories
from d2d.pipeline.semantics import Semantics
from d2d.pipeline.snapshots import load_snapshots

_TS = pa.timestamp("us", tz="UTC")
CHANGELOG_SCHEMA = pa.schema(
    [
        ("device_id", pa.string()),
        ("property", pa.string()),
        ("old_value", pa.string()),
        ("new_value", pa.string()),
        ("change_kind", pa.string()),
        ("semantic_tag", pa.string()),
        ("snapshot_ts_before", _TS),
        ("snapshot_ts_after", _TS),
        ("snapshot_run_id", pa.string()),
    ]
)
EVENT_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("device_id", pa.string()),
        ("property", pa.string()),
        ("old_value", pa.string()),
        ("new_value", pa.string()),
        ("change_kind", pa.string()),
        ("semantic_tag", pa.string()),
        ("event_ts", _TS),
        ("snapshot_run_id", pa.string()),
    ]
)
STORY_SCHEMA = pa.schema(
    [
        ("story_id", pa.string()),
        ("home_id", pa.string()),
        ("window_start", _TS),
        ("window_end", _TS),
        ("device_ids", pa.list_(pa.string())),
        ("event_ids", pa.list_(pa.string())),
        ("event_count", pa.int32()),
        ("link_reasons", pa.list_(pa.string())),
        ("linking_config_sha", pa.string()),
    ]
)


def event_id(device_id: str, prop: str, ts_iso: str) -> str:
    return hashlib.sha1(f"{device_id}|{prop}|{ts_iso}".encode()).hexdigest()[:16]


def to_events(changes: list[Change]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for c in changes:
        eid = event_id(c.device_id, c.property, c.snapshot_ts_after.isoformat())
        events[eid] = {
            "event_id": eid,
            "device_id": c.device_id,
            "property": c.property,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "change_kind": c.change_kind,
            "semantic_tag": c.semantic_tag,
            "event_ts": c.snapshot_ts_after,
            "snapshot_run_id": c.snapshot_run_id,
        }
    return list(events.values())


@dataclass(frozen=True)
class RunSummary:
    snapshot_rows: int
    changes: int
    events: int
    by_kind: dict[str, int]
    stories: int = 0


def run_pipeline(
    data_dir: Path, semantics: Semantics, linking: LinkingConfig, home_id: str
) -> RunSummary:
    """Full recompute of stages 2-4 from all snapshots (idempotent)."""
    rows = load_snapshots(data_dir)
    cdc_rows = load_cdc(data_dir)
    kinds = {r["device_id"]: r["kind"] for r in rows}
    until = max((r["snapshot_ts"] for r in rows), default=None)
    changes = (
        build_changelog(rows, semantics)
        + cdc_changes(cdc_rows, semantics)
        + duration_changes(cdc_rows, list(semantics.durations), kinds, until)
    )
    changes.sort(key=lambda c: (c.snapshot_ts_after, c.device_id, c.property))
    events = to_events(changes)
    stories = build_stories(events, TopologyIndex(rows), linking, home_id)
    for name, schema, data in (
        ("changelog", CHANGELOG_SCHEMA, [c.as_row() for c in changes]),
        ("events", EVENT_SCHEMA, events),
        ("stories", STORY_SCHEMA, stories),
    ):
        out = data_dir / name
        out.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(data, schema=schema), out / f"{name}.parquet")
    by_kind: dict[str, int] = {}
    for e in events:
        by_kind[e["change_kind"]] = by_kind.get(e["change_kind"], 0) + 1
    return RunSummary(
        len(rows), len(changes), len(events), dict(sorted(by_kind.items())), len(stories)
    )
