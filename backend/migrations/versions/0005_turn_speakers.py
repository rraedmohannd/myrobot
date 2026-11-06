"""Anonymous speaker number per turn (multi-speaker conversations, docs/SPEAKERS.md).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("speaker_label", sa.SmallInteger(), nullable=True))
    op.add_column("turns", sa.Column("speaker_confidence", sa.Float(), nullable=True))
    op.add_column("turns", sa.Column("speaker_source", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("turns", "speaker_source")
    op.drop_column("turns", "speaker_confidence")
    op.drop_column("turns", "speaker_label")
