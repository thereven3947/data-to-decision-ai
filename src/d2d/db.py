"""Postgres: connection URL, ``ontology`` tables and the stage-3 loader (see docs/domain-model.md).

The tables here mirror ``migrations/alembic``; ``tests/integration/test_db.py`` fails if they drift.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, Connection, make_url

from d2d.config import ConfigError, Household

SCHEMA = "ontology"
CHANGE_KINDS = ("config", "operational", "topology", "data_quality")
STORY_STATUSES = ("new", "needs_review", "published", "closed")

metadata = MetaData(
    schema=SCHEMA,
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    },
)

# Topology as configured in household.toml; the snapshot at event time is authoritative for linking.
device = Table(
    "device",
    metadata,
    Column("device_id", Text, primary_key=True),
    Column("home_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("room", Text, nullable=False),
    Column("circuit_id", Text, nullable=False),
    Column("network_id", Text, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("tags", ARRAY(Text), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

change_event = Table(
    "change_event",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("device_id", Text, ForeignKey("ontology.device.device_id"), nullable=False),
    Column("property", Text, nullable=False),
    Column("old_value", Text),
    Column("new_value", Text),
    Column("change_kind", Text, nullable=False),
    Column("semantic_tag", Text, nullable=False),
    Column("event_ts", DateTime(timezone=True), nullable=False),
    Column("snapshot_run_id", Text),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("device_id", "property", "event_ts"),
    CheckConstraint(
        "change_kind IN ('config', 'operational', 'topology', 'data_quality')", name="change_kind"
    ),
    Index(None, "event_ts"),
)

change_story = Table(
    "change_story",
    metadata,
    Column("story_id", Text, primary_key=True),
    Column("home_id", Text, nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("window_end", DateTime(timezone=True), nullable=False),
    Column("device_ids", ARRAY(Text), nullable=False),
    Column("event_count", Integer, nullable=False),
    Column("link_reasons", ARRAY(Text), nullable=False),
    Column("linking_config_sha", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="new"),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('new', 'needs_review', 'published', 'closed')", name="status"),
    Index(None, "window_start"),
)

change_story_event = Table(
    "change_story_event",
    metadata,
    Column(
        "story_id",
        Text,
        ForeignKey("ontology.change_story.story_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("event_id", Text, ForeignKey("ontology.change_event.event_id"), primary_key=True),
    Index(None, "event_id"),
)


def database_url(env: Mapping[str, str] | None = None) -> URL:
    """``D2D_DATABASE_URL``, else the deploy ``.env`` keys (POSTGRES_PASSWORD, D2D_PG_HOST/PORT)."""
    env = os.environ if env is None else env
    if url := env.get("D2D_DATABASE_URL"):
        return make_url(url)
    password = env.get("POSTGRES_PASSWORD")
    if not password:
        raise ConfigError("set D2D_DATABASE_URL or POSTGRES_PASSWORD (see .env.example)")
    return URL.create(
        "postgresql+psycopg",
        username="d2d",
        password=password,
        host=env.get("D2D_PG_HOST") or "127.0.0.1",
        port=int(env.get("D2D_PG_PORT") or 5432),
        database="d2d",
    )


class LoadError(RuntimeError):
    """Raised when pipeline output cannot be loaded into Postgres."""


@dataclass(frozen=True)
class LoadSummary:
    devices: int
    events: int
    stories: int
    stories_removed: int


def _read(data_dir: Path, name: str) -> list[dict[str, Any]]:
    path = data_dir / name / f"{name}.parquet"
    if not path.is_file():
        raise LoadError(f"missing {path}; run `d2d run` first")
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def _upsert(conn: Connection, table: Table, rows: list[dict[str, Any]], key: str) -> None:
    if not rows:
        return
    stmt = insert(table)
    updates: dict[str, Any] = {c: stmt.excluded[c] for c in rows[0] if c != key}
    if "updated_at" in table.c:
        updates["updated_at"] = func.now()
    conn.execute(stmt.on_conflict_do_update(index_elements=[key], set_=updates), rows)


def load_run(engine: Engine, data_dir: Path, household: Household) -> LoadSummary:
    """Upsert devices, change events and change stories from one ``d2d run`` (one transaction).

    Events are append-only (idempotent ``event_id``). Stories are recomputed on every run, so
    stories of this home that are no longer produced are removed while still ``new``; stories a
    human has moved past ``new`` are kept.
    """
    events = _read(data_dir, "events")
    stories = _read(data_dir, "stories")
    unknown = sorted({e["device_id"] for e in events} - household.devices.keys())
    if unknown:
        raise LoadError(f"events reference devices not in household.toml: {', '.join(unknown)}")
    devices = [
        {
            "device_id": d.id,
            "home_id": household.id,
            "kind": d.kind,
            "room": d.room,
            "circuit_id": d.circuit,
            "network_id": d.network,
            "active": d.active,
            "tags": list(d.tags),
        }
        for d in household.devices.values()
    ]
    links = [{"story_id": s["story_id"], "event_id": e} for s in stories for e in s["event_ids"]]
    story_rows = [{k: v for k, v in s.items() if k != "event_ids"} for s in stories]
    story_ids = [s["story_id"] for s in stories]

    with engine.begin() as conn:
        _upsert(conn, device, devices, "device_id")
        _upsert(conn, change_event, events, "event_id")
        removed = conn.execute(
            delete(change_story)
            .where(change_story.c.home_id == household.id)
            .where(change_story.c.status == "new")
            .where(change_story.c.story_id.not_in(story_ids))
        ).rowcount
        _upsert(conn, change_story, story_rows, "story_id")
        if story_ids:
            conn.execute(
                delete(change_story_event).where(change_story_event.c.story_id.in_(story_ids))
            )
        if links:
            conn.execute(insert(change_story_event), links)
    return LoadSummary(len(devices), len(events), len(stories), removed)
