"""Media library: title, type, size, where the file came from, and a review status.

Existing rows (none are expected) become 'pending', so nothing is shown before a human review.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("media", sa.Column("mime", sa.String(64), nullable=True))
    op.add_column("media", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("media", sa.Column("source_note", sa.Text(), nullable=True))
    op.add_column(
        "media",
        sa.Column("review_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
    )
    op.add_column(
        "media",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_check_constraint(
        "ck_media_review_status", "media", "review_status IN ('pending', 'approved', 'rejected')"
    )
    op.create_index("ix_media_program_status", "media", ["program_id", "review_status"])


def downgrade() -> None:
    op.drop_index("ix_media_program_status", table_name="media")
    op.drop_constraint("ck_media_review_status", "media", type_="check")
    op.drop_column("media", "created_at")
    op.drop_column("media", "review_status")
    op.drop_column("media", "source_note")
    op.drop_column("media", "size_bytes")
    op.drop_column("media", "mime")
    op.drop_column("media", "title")
