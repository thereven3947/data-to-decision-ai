"""Write snapshots as hive-style Parquet partitions and ground truth as JSONL."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SNAPSHOT_SCHEMA = pa.schema(
    [
        ("snapshot_run_id", pa.string()),
        ("snapshot_ts", pa.timestamp("us", tz="UTC")),
        ("reported_ts", pa.timestamp("us", tz="UTC")),
        ("device_id", pa.string()),
        ("kind", pa.string()),
        ("room", pa.string()),
        ("circuit_id", pa.string()),
        ("upstream_ids", pa.list_(pa.string())),
        ("firmware_version", pa.string()),
        ("schedule", pa.string()),
        ("mode", pa.string()),
        ("setpoint", pa.float64()),
        ("settings", pa.string()),
        ("power_w", pa.float64()),
        ("energy_kwh_total", pa.float64()),
        ("state", pa.string()),
        ("error_code", pa.string()),
        ("rssi_dbm", pa.int32()),
        ("network_id", pa.string()),
        ("source", pa.string()),
    ]
)
CDC_SCHEMA = pa.schema(
    [
        ("device_id", pa.string()),
        ("property", pa.string()),
        ("old_value", pa.string()),
        ("new_value", pa.string()),
        ("event_ts", pa.timestamp("us", tz="UTC")),
        ("source", pa.string()),
    ]
)


def write_snapshots(rows: list[dict[str, Any]], data_dir: Path) -> int:
    """Replace ``data_dir/snapshots`` with one partition per run; returns partitions written."""
    out = data_dir / "snapshots"
    if out.exists():
        shutil.rmtree(out)
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_run[row["snapshot_run_id"]].append(row)
    for run_id, run_rows in by_run.items():
        part = out / f"run_ts={run_id.removeprefix('run_')}"
        part.mkdir(parents=True)
        table = pa.Table.from_pylist(run_rows, schema=SNAPSHOT_SCHEMA)
        pq.write_table(table, part / "part-0.parquet")
    return len(by_run)


def write_cdc(events: list[dict[str, Any]], data_dir: Path) -> Path:
    out = data_dir / "cdc"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "events.parquet"
    pq.write_table(pa.Table.from_pylist(events, schema=CDC_SCHEMA), path)
    return path


def write_ground_truth(ground_truth: list[dict[str, Any]], data_dir: Path) -> Path:
    out = data_dir / "ground_truth"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "scenarios.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for entry in ground_truth:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return path
