"""Sprint 6: daily count of real AI provider calls (cost guard of the server mode).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paid_call_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("refused", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("day"),
    )


def downgrade() -> None:
    op.drop_table("paid_call_days")
