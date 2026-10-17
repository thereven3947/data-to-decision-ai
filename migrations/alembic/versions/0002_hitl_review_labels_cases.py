"""hitl schema: label, review_item, change_case, case_story; story label columns (M6-01)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS hitl")
    op.create_table(
        "change_case",
        sa.Column("case_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_change_case_status")),
        sa.PrimaryKeyConstraint("case_id", name=op.f("pk_change_case")),
        schema="hitl",
    )
    op.create_table(
        "case_story",
        sa.Column("case_id", sa.BigInteger(), nullable=False),
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["hitl.change_case.case_id"],
            name=op.f("fk_case_story_case_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["ontology.change_story.story_id"],
            name=op.f("fk_case_story_story_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_id", "story_id", name=op.f("pk_case_story")),
        schema="hitl",
    )
    op.create_table(
        "label",
        sa.Column("label_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('human', 'model', 'sim_reviewer')", name=op.f("ck_label_source")
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["ontology.change_story.story_id"],
            name=op.f("fk_label_story_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("label_id", name=op.f("pk_label")),
        schema="hitl",
    )
    op.create_index(op.f("ix_label_story_id"), "label", ["story_id"], unique=False, schema="hitl")
    op.create_table(
        "review_item",
        sa.Column("story_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("suggested_category", sa.Text(), nullable=True),
        sa.Column("suggested_confidence", sa.Float(), nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "reason IN ('abstain', 'audit', 'feedback')", name=op.f("ck_review_item_reason")
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["ontology.change_story.story_id"],
            name=op.f("fk_review_item_story_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("story_id", name=op.f("pk_review_item")),
        schema="hitl",
    )
    op.add_column(
        "change_story", sa.Column("category", sa.Text(), nullable=True), schema="ontology"
    )
    op.add_column(
        "change_story", sa.Column("confidence", sa.Float(), nullable=True), schema="ontology"
    )
    op.add_column(
        "change_story", sa.Column("label_source", sa.Text(), nullable=True), schema="ontology"
    )
    op.add_column(
        "change_story", sa.Column("model_version", sa.Text(), nullable=True), schema="ontology"
    )
    op.create_check_constraint(
        "label_source",
        "change_story",
        "label_source IN ('human', 'model', 'sim_reviewer')",
        schema="ontology",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_change_story_label_source"), "change_story", schema="ontology")
    op.drop_column("change_story", "model_version", schema="ontology")
    op.drop_column("change_story", "label_source", schema="ontology")
    op.drop_column("change_story", "confidence", schema="ontology")
    op.drop_column("change_story", "category", schema="ontology")
    op.drop_table("review_item", schema="hitl")
    op.drop_index(op.f("ix_label_story_id"), table_name="label", schema="hitl")
    op.drop_table("label", schema="hitl")
    op.drop_table("case_story", schema="hitl")
    op.drop_table("change_case", schema="hitl")
    op.execute("DROP SCHEMA IF EXISTS hitl")
