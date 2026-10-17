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
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
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
    # Current label (latest row in hitl.label); denormalized for the API and the review queue.
    Column("category", Text),
    Column("confidence", Float),
    Column("label_source", Text),
    Column("model_version", Text),
    CheckConstraint("status IN ('new', 'needs_review', 'published', 'closed')", name="status"),
    CheckConstraint("label_source IN ('human', 'model', 'sim_reviewer')", name="label_source"),
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

HITL = "hitl"
LABEL_SOURCES = ("human", "model", "sim_reviewer")
REVIEW_REASONS = ("abstain", "audit", "feedback")
_STORY_FK = "ontology.change_story.story_id"

label = Table(
    "label",
    metadata,
    Column("label_id", BigInteger, Identity(), primary_key=True),
    Column("story_id", Text, ForeignKey(_STORY_FK, ondelete="CASCADE"), nullable=False),
    Column("category", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("confidence", Float),
    Column("reviewer", Text),
    Column("note", Text),
    Column("model_version", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("source IN ('human', 'model', 'sim_reviewer')", name="source"),
    Index(None, "story_id"),
    schema=HITL,
)

review_item = Table(
    "review_item",
    metadata,
    Column("story_id", Text, ForeignKey(_STORY_FK, ondelete="CASCADE"), primary_key=True),
    Column("reason", Text, nullable=False),
    Column("suggested_category", Text),
    Column("suggested_confidence", Float),
    Column("queued_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("resolved_at", DateTime(timezone=True)),
    Column("resolved_by", Text),
    CheckConstraint("reason IN ('abstain', 'audit', 'feedback')", name="reason"),
    schema=HITL,
)

change_case = Table(
    "change_case",
    metadata,
    Column("case_id", BigInteger, Identity(), primary_key=True),
    Column("title", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="open"),
    Column("note", Text),
    Column("created_by", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("closed_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('open', 'closed')", name="status"),
    schema=HITL,
)

case_story = Table(
    "case_story",
    metadata,
    Column(
        "case_id",
        BigInteger,
        ForeignKey("hitl.change_case.case_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("story_id", Text, ForeignKey(_STORY_FK, ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema=HITL,
)

FEEDBACK = "feedback"
FEEDBACK_REASONS = (
    "wrong_category",
    "wrong_grouping",
    "unclear_description",
    "wrong_data",
    "missing_data",
    "unhelpful_answer",
    "wrong_action",
    "other",
)

chat_message = Table(
    "chat_message",
    metadata,
    Column("message_id", BigInteger, Identity(), primary_key=True),
    Column("session_id", Text, nullable=False),
    Column("user_role", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("skill", Text, nullable=False),
    Column("skill_sha", Text, nullable=False),
    Column("llm_model_tag", Text, nullable=False),
    Column("tool_calls", JSONB, nullable=False),
    Column("cited_ids", ARRAY(Text), nullable=False),
    Column("checker_result", JSONB, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "session_id"),
    schema=FEEDBACK,
)

feedback = Table(
    "feedback",
    metadata,
    Column("feedback_id", BigInteger, Identity(), primary_key=True),
    Column("target_type", Text, nullable=False),
    Column("target_id", Text, nullable=False),
    Column("rating", Text, nullable=False),
    Column("reason", Text),
    Column("comment", Text),
    Column("user_role", Text, nullable=False),
    Column("page", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("target_type IN ('story', 'message', 'page')", name="target_type"),
    CheckConstraint("rating IN ('up', 'down', 'comment')", name="rating"),
    Index(None, "target_type", "target_id"),
    schema=FEEDBACK,
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
