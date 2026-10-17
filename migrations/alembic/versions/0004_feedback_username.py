"""username on chat messages and feedback (login)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_message", sa.Column("username", sa.Text(), nullable=True), schema="feedback"
    )
    op.add_column("feedback", sa.Column("username", sa.Text(), nullable=True), schema="feedback")


def downgrade() -> None:
    op.drop_column("feedback", "username", schema="feedback")
    op.drop_column("chat_message", "username", schema="feedback")
