from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from d2d.analytics import SCHEMAS, export_warehouse
from d2d.cli import app
from d2d.config import Household, load_categories
from d2d.security import read_events, score_events, write_alerts

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "analytics" / "bigquery" / "views.sql"
LOOKER = ROOT / "analytics" / "looker"


@pytest.fixture(scope="module")
def warehouse(
    run_data: Path, household: Household, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    write_alerts(score_events(read_events(run_data), household), run_data)
    out = tmp_path_factory.mktemp("warehouse")
    export_warehouse(run_data, out, household, load_categories(ROOT / "config" / "categories.toml"))
    return out


def _duckdb(warehouse: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA d2d_analytics")
    for name in SCHEMAS:
        path = (warehouse / f"{name}.parquet").as_posix()
        con.execute(f"CREATE TABLE d2d_analytics.{name} AS SELECT * FROM read_parquet('{path}')")
    sql = VIEWS.read_text(encoding="utf-8").replace("{dataset}", "d2d_analytics")
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for stmt in body.split(";"):
        if stmt.strip():
            con.execute(stmt)
    return con


def test_export_writes_every_table_with_its_schema(warehouse: Path, run_data: Path) -> None:
    for name, schema in SCHEMAS.items():
        table = pq.read_table(warehouse / f"{name}.parquet")
        assert table.schema.equals(schema), name
    stories = pq.read_table(run_data / "stories" / "stories.parquet")
    events = pq.read_table(run_data / "events" / "events.parquet")
    assert pq.read_table(warehouse / "fact_story.parquet").num_rows == stories.num_rows
    assert pq.read_table(warehouse / "fact_change_event.parquet").num_rows == events.num_rows
    assert pq.read_table(warehouse / "fact_security_alert.parquet").num_rows > 0
    # Without a database there are no labels, agent answers or feedback.
    assert pq.read_table(warehouse / "fact_agent_answer.parquet").num_rows == 0
    assert (warehouse / "manifest.json").exists()


def test_events_link_to_their_story(warehouse: Path) -> None:
    events = pq.read_table(warehouse / "fact_change_event.parquet").to_pylist()
    stories = {r["story_id"] for r in pq.read_table(warehouse / "fact_story.parquet").to_pylist()}
    linked = [e for e in events if e["story_id"] is not None]
    assert linked and all(e["story_id"] in stories for e in linked)
    assert all(0 <= e["event_hour"] <= 23 for e in events)


def test_no_free_text_is_exported() -> None:
    names = {f for s in SCHEMAS.values() for f in s.names}
    assert not names & {"question", "answer", "comment", "username", "note"}


def test_views_run_and_add_up(warehouse: Path) -> None:
    con = _duckdb(warehouse)
    stories = con.execute("SELECT COUNT(*) FROM d2d_analytics.fact_story").fetchone()
    daily = con.execute("SELECT SUM(stories) FROM d2d_analytics.v_daily_stories").fetchone()
    assert stories is not None and daily is not None and daily[0] == stories[0]
    kpis = con.execute(
        "SELECT stories, auto_labeled, review_backlog FROM d2d_analytics.v_label_kpis"
    ).fetchone()
    assert kpis == (stories[0], 0, 0)
    devices = con.execute(
        "SELECT COUNT(*), SUM(change_events) FROM d2d_analytics.v_device_activity"
    ).fetchone()
    events = con.execute("SELECT COUNT(*) FROM d2d_analytics.fact_change_event").fetchone()
    assert devices is not None and events is not None and devices[1] == events[0]
    util = dict(
        con.execute(
            "SELECT circuit_id, utilization FROM d2d_analytics.v_circuit_capacity"
        ).fetchall()
    )
    assert util["circuit_garage"] > 1.0  # 7.2 kW charger on a 50 A circuit at 120 V
    assert util["circuit_bedroom"] < 0.1
    alerts = con.execute("SELECT SUM(alerts) FROM d2d_analytics.v_security_daily").fetchone()
    assert alerts == (0,)
    for view in ("v_agent_quality", "v_feedback_routing"):
        assert con.execute(f"SELECT COUNT(*) FROM d2d_analytics.{view}").fetchone() == (0,)


def test_looker_model_matches_the_warehouse() -> None:
    files = [LOOKER / "d2d.model.lkml", *sorted((LOOKER / "views").glob("*.view.lkml"))]
    views: set[str] = set()
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert text.count("{") == text.count("}"), f.name
        views |= set(re.findall(r"^view: (\w+)", text, re.MULTILINE))
        for table in re.findall(r"sql_table_name: d2d_analytics\.(\w+)", text):
            assert table in SCHEMAS, f"{f.name}: unknown table {table}"
    model = (LOOKER / "d2d.model.lkml").read_text(encoding="utf-8")
    used = set(re.findall(r"(?:explore|join): (\w+)", model))
    assert used <= views, used - views


def test_cli_export_no_db(run_data: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "export",
            "warehouse",
            "--no-db",
            "--data-dir",
            str(run_data),
            "--out",
            str(tmp_path),
            "--config",
            str(ROOT / "config"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "fact_story" in result.output and (tmp_path / "manifest.json").exists()
