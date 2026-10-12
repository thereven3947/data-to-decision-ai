from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
from typer.testing import CliRunner

from d2d.cli import app

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_simulate_run_eval_passes_gate(tmp_path: Path) -> None:
    runner = CliRunner()
    common = ["--data-dir", str(tmp_path)]
    cfg = ["--config", str(CONFIG_DIR)]

    assert runner.invoke(app, ["simulate", *common, *cfg]).exit_code == 0
    first = runner.invoke(app, ["run", *common, *cfg])
    assert first.exit_code == 0, first.output
    ids_1 = pq.read_table(tmp_path / "events" / "events.parquet").column("event_id").to_pylist()

    second = runner.invoke(app, ["run", *common, *cfg])
    ids_2 = pq.read_table(tmp_path / "events" / "events.parquet").column("event_id").to_pylist()
    assert second.output == first.output
    assert ids_1 == ids_2 and len(set(ids_1)) == len(ids_1)

    result = runner.invoke(app, ["eval", "changelog", *common])
    assert result.exit_code == 0, result.output
    assert "recall    1.000" in result.output
    assert "gate: PASS" in result.output

    failing = runner.invoke(app, ["eval", "changelog", *common, "--min-precision", "1.01"])
    assert failing.exit_code == 1
    assert "gate: FAIL" in failing.output

    linking = runner.invoke(app, ["eval", "linking", *common])
    assert linking.exit_code == 0, linking.output
    assert "pair_f1" in linking.output and "ari" in linking.output


def test_ari_known_values() -> None:
    from d2d.evaluation import _ari

    assert _ari(["a", "a", "b", "b"], ["x", "x", "y", "y"]) == 1.0
    assert _ari(["a", "a", "b", "b"], ["x", "y", "x", "y"]) < 0
