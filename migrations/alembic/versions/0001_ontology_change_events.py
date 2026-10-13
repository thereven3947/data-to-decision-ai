"""ontology schema: device, change_event, change_story, change_story_event (M4-02)

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ontology"
TS = sa.DateTime(timezone=True)
TEXTS = postgresql.ARRAY(sa.Text())


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "device",
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("home_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("room", sa.Text(), nullable=False),
        sa.Column("circuit_id", sa.Text(), nullable=False),
        sa.Column("network_id", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("tags", TEXTS, nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("device_id", name="pk_device"),
        schema=SCHEMA,
    )
    op.create_table(
        "change_event",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("property", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("semantic_tag", sa.Text(), nullable=False),
        sa.Column("event_ts", TS, nullable=False),
        sa.Column("snapshot_run_id", sa.Text(), nullable=True),
        sa.Column("loaded_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "change_kind IN ('config', 'operational', 'topology', 'data_quality')",
            name="ck_change_event_change_kind",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], [f"{SCHEMA}.device.device_id"], name="fk_change_event_device_id"
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_change_event"),
        sa.UniqueConstraint("device_id", "property", "event_ts", name="uq_change_event_device_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_change_event_event_ts", "change_event", ["event_ts"], schema=SCHEMA)
    op.create_table(
        "change_story",
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column("home_id", sa.Text(), nullable=False),
        sa.Column("window_start", TS, nullable=False),
        sa.Column("window_end", TS, nullable=False),
        sa.Column("device_ids", TEXTS, nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("link_reasons", TEXTS, nullable=False),
        sa.Column("linking_config_sha", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="new", nullable=False),
        sa.Column("loaded_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'needs_review', 'published', 'closed')",
            name="ck_change_story_status",
        ),
        sa.PrimaryKeyConstraint("story_id", name="pk_change_story"),
        schema=SCHEMA,
    )
    op.create_index("ix_change_story_window_start", "change_story", ["window_start"], schema=SCHEMA)
    op.create_table(
        "change_story_event",
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            [f"{SCHEMA}.change_event.event_id"],
            name="fk_change_story_event_event_id",
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            [f"{SCHEMA}.change_story.story_id"],
            name="fk_change_story_event_story_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("story_id", "event_id", name="pk_change_story_event"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_change_story_event_event_id", "change_story_event", ["event_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("change_story_event", schema=SCHEMA)
    op.drop_table("change_story", schema=SCHEMA)
    op.drop_table("change_event", schema=SCHEMA)
    op.drop_table("device", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
