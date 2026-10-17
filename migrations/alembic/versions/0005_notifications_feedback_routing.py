"""notifications and feedback routing (M5-05, M11-01)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS notify")
    op.create_table(
        "eval_item",
        sa.Column("item_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("set_name", sa.Text(), nullable=False),
        sa.Column("feedback_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "set_name IN ('linking', 'enrichment', 'conversations', 'labels', 'data', 'ux')",
            name=op.f("ck_eval_item_set_name"),
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedback.feedback.feedback_id"],
            name=op.f("fk_eval_item_feedback_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_eval_item")),
        schema="feedback",
    )
    op.create_index(
        op.f("ix_eval_item_set_name"), "eval_item", ["set_name"], unique=False, schema="feedback"
    )
    op.create_table(
        "notification",
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("channel IN ('push', 'digest')", name=op.f("ck_notification_channel")),
        sa.CheckConstraint(
            "severity IN ('info', 'notice', 'alert')", name=op.f("ck_notification_severity")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'deferred', 'recorded', 'failed', 'backfill')",
            name=op.f("ck_notification_status"),
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["ontology.change_story.story_id"],
            name=op.f("fk_notification_story_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("story_id", name=op.f("pk_notification")),
        schema="notify",
    )
    op.add_column("feedback", sa.Column("routed_to", sa.Text(), nullable=True), schema="feedback")
    op.add_column(
        "feedback",
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        schema="feedback",
    )


def downgrade() -> None:
    op.drop_column("feedback", "routed_at", schema="feedback")
    op.drop_column("feedback", "routed_to", schema="feedback")
    op.drop_table("notification", schema="notify")
    op.drop_index(op.f("ix_eval_item_set_name"), table_name="eval_item", schema="feedback")
    op.drop_table("eval_item", schema="feedback")
    op.execute("DROP SCHEMA IF EXISTS notify")
