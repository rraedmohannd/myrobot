"""Ingestion columns: raw/clean page text on sources, chunk order within a page.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("raw_html", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("clean_text", sa.Text(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_chunks_source_position", "chunks", ["source_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_chunks_source_position", table_name="chunks")
    op.drop_column("chunks", "position")
    op.drop_column("sources", "clean_text")
    op.drop_column("sources", "raw_html")
