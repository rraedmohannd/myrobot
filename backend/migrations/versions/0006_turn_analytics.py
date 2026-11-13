"""Analytics columns on turns: found flag, filler time and time to the first LLM piece.

All nullable, so turns stored before this migration stay valid (their values are unknown).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("found", sa.Boolean(), nullable=True))
    op.add_column("turns", sa.Column("ack_ms", sa.Integer(), nullable=True))
    op.add_column("turns", sa.Column("llm_first_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("turns", "llm_first_ms")
    op.drop_column("turns", "ack_ms")
    op.drop_column("turns", "found")
