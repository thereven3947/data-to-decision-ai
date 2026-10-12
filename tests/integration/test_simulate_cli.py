from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
from typer.testing import CliRunner

from d2d.cli import app

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_simulate_writes_partitions_and_ground_truth(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["simulate", "--data-dir", str(tmp_path), "--config", str(CONFIG_DIR)]
    )
    assert result.exit_code == 0, result.output

    partitions = sorted((tmp_path / "snapshots").glob("run_ts=*/part-0.parquet"))
    assert len(partitions) == 3 * 24 * 4
    table = pq.read_table(partitions[0])
    assert str(table.schema.field("snapshot_ts").type) == "timestamp[us, tz=UTC]"
    assert table.num_rows == 15  # active devices at the first run (2 spares inactive)

    cdc = pq.read_table(tmp_path / "cdc" / "events.parquet")
    assert cdc.num_rows > 0
    assert str(cdc.schema.field("event_ts").type) == "timestamp[us, tz=UTC]"

    lines = (tmp_path / "ground_truth" / "scenarios.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 12
    assert {"scenario_id", "category", "events", "start", "end"} <= json.loads(lines[0]).keys()
