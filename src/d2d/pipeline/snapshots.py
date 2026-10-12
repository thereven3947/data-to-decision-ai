"""Load stage-1 snapshots with DuckDB (one row per device per run, duplicates collapsed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

_SQL = """
SELECT *, count(*) OVER (PARTITION BY snapshot_ts, device_id) AS dup_count
FROM read_parquet(?, union_by_name = true)
QUALIFY row_number() OVER (PARTITION BY snapshot_ts, device_id ORDER BY snapshot_run_id) = 1
ORDER BY device_id, snapshot_ts
"""


def load_snapshots(data_dir: Path) -> list[dict[str, Any]]:
    """Rows sorted by ``(device_id, snapshot_ts)``; ``dup_count`` > 1 marks duplicate IDs."""
    pattern = (data_dir / "snapshots" / "*" / "*.parquet").as_posix()
    if not any((data_dir / "snapshots").glob("*/*.parquet")):
        raise FileNotFoundError(f"no snapshots under {data_dir / 'snapshots'}; run d2d simulate")
    with duckdb.connect() as con:
        con.execute("SET TimeZone = 'UTC'")
        table = con.execute(_SQL, [pattern]).to_arrow_table()
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows
