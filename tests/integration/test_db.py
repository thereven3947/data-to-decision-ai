"""Postgres integration: migrations and `d2d load`. Runs only when D2D_TEST_DATABASE_URL is set
(CI sets it; locally point it at a throwaway database - every test drops the ontology schema)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, func, insert, select, update
from typer.testing import CliRunner

from d2d.cli import app
from d2d.config import Household
from d2d.db import (
    LoadError,
    change_event,
    change_story,
    change_story_event,
    device,
    load_run,
    metadata,
)

ROOT = Path(__file__).resolve().parents[2]
TEST_URL = os.environ.get("D2D_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="D2D_TEST_DATABASE_URL not set")


def _alembic(monkeypatch: pytest.MonkeyPatch) -> Config:
    assert TEST_URL
    monkeypatch.setenv("D2D_DATABASE_URL", TEST_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    cfg = _alembic(monkeypatch)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert TEST_URL
    eng = create_engine(TEST_URL)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("run")
    runner = CliRunner()
    common = ["--data-dir", str(data), "--config", str(ROOT / "config")]
    for cmd in ("simulate", "run"):
        result = runner.invoke(app, [cmd, *common])
        assert result.exit_code == 0, result.output
    return data


def _count(eng: Engine, table: object) -> int:
    with eng.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())  # type: ignore[arg-type]


def test_migrations_round_trip_and_match_metadata(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "include_name": lambda name, type_, _: (
                    type_ != "schema" or name in {"ontology", "hitl", "feedback", "notify"}
                ),
            },
        )
        assert compare_metadata(ctx, metadata) == []
    cfg = _alembic(monkeypatch)
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM information_schema.schemata "
                "WHERE schema_name IN ('ontology', 'hitl', 'feedback', 'notify')"
            ).scalar_one()
            == 0
        )
    command.upgrade(cfg, "head")


def test_load_is_idempotent_and_matches_run(
    engine: Engine, run_dir: Path, household: Household
) -> None:
    events = pq.read_table(run_dir / "events" / "events.parquet").to_pylist()
    stories = pq.read_table(run_dir / "stories" / "stories.parquet").to_pylist()

    first = load_run(engine, run_dir, household)
    second = load_run(engine, run_dir, household)

    assert first == second
    assert (first.events, first.stories, first.stories_removed) == (len(events), len(stories), 0)
    assert _count(engine, device) == len(household.devices)
    assert _count(engine, change_event) == len(events)
    assert _count(engine, change_story) == len(stories)
    assert _count(engine, change_story_event) == sum(s["event_count"] for s in stories)
    with engine.connect() as conn:
        db_ids = set(conn.execute(select(change_event.c.event_id)).scalars())
        statuses = set(conn.execute(select(change_story.c.status)).scalars())
    assert db_ids == {e["event_id"] for e in events}
    assert statuses == {"new"}


def test_reload_keeps_reviewed_stories_and_drops_stale_new_ones(
    engine: Engine, run_dir: Path, household: Household
) -> None:
    load_run(engine, run_dir, household)
    with engine.begin() as conn:
        some_event = conn.execute(select(change_event.c.event_id).limit(1)).scalar_one()
        reviewed = conn.execute(select(change_story.c.story_id).limit(1)).scalar_one()
        conn.execute(
            update(change_story)
            .where(change_story.c.story_id == reviewed)
            .values(status="needs_review")
        )
        base = conn.execute(select(change_story).limit(1)).mappings().one()
        for sid, status in (("stale_new", "new"), ("stale_published", "published")):
            conn.execute(insert(change_story).values({**base, "story_id": sid, "status": status}))
            conn.execute(insert(change_story_event).values(story_id=sid, event_id=some_event))

    summary = load_run(engine, run_dir, household)

    assert summary.stories_removed == 1
    with engine.connect() as conn:
        rows = dict(conn.execute(select(change_story.c.story_id, change_story.c.status)).all())
    assert "stale_new" not in rows
    assert rows["stale_published"] == "published"
    assert rows[reviewed] == "needs_review"


def test_unknown_device_is_rejected_without_writing(
    engine: Engine, run_dir: Path, household: Household
) -> None:
    devices = {k: v for k, v in household.devices.items() if k != "garage_door"}
    with pytest.raises(LoadError, match="garage_door"):
        load_run(engine, run_dir, replace(household, devices=devices))
    assert _count(engine, change_event) == 0


def test_load_cli(engine: Engine, run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_URL
    monkeypatch.setenv("D2D_DATABASE_URL", TEST_URL)
    runner = CliRunner()
    args = ["load", "--data-dir", str(run_dir), "--config", str(ROOT / "config")]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "events" in result.output and "stories" in result.output
    assert "***" in result.output or "@" not in result.output

    missing = runner.invoke(app, [*args[:1], "--data-dir", str(run_dir / "nope"), *args[3:]])
    assert missing.exit_code == 1
