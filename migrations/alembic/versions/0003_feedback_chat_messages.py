"""feedback schema: chat_message, feedback (M10/M11)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS feedback")
    op.create_table(
        "chat_message",
        sa.Column("message_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_role", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("skill", sa.Text(), nullable=False),
        sa.Column("skill_sha", sa.Text(), nullable=False),
        sa.Column("llm_model_tag", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cited_ids", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("checker_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_chat_message")),
        schema="feedback",
    )
    op.create_index(
        op.f("ix_chat_message_session_id"),
        "chat_message",
        ["session_id"],
        unique=False,
        schema="feedback",
    )
    op.create_table(
        "feedback",
        sa.Column("feedback_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("user_role", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating IN ('up', 'down', 'comment')", name=op.f("ck_feedback_rating")),
        sa.CheckConstraint(
            "target_type IN ('story', 'message', 'page')", name=op.f("ck_feedback_target_type")
        ),
        sa.PrimaryKeyConstraint("feedback_id", name=op.f("pk_feedback")),
        schema="feedback",
    )
    op.create_index(
        op.f("ix_feedback_target_type"),
        "feedback",
        ["target_type", "target_id"],
        unique=False,
        schema="feedback",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_target_type"), table_name="feedback", schema="feedback")
    op.drop_table("feedback", schema="feedback")
    op.drop_index(op.f("ix_chat_message_session_id"), table_name="chat_message", schema="feedback")
    op.drop_table("chat_message", schema="feedback")
    op.execute("DROP SCHEMA IF EXISTS feedback")
