"""Bilingual search hints on chunks; full-text vector now covers text + hint.

The generated tsv column cannot be altered in place (PostgreSQL 16), so it is recreated.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_V1 = (
    "to_tsvector(CASE WHEN lang = 'ar' THEN 'arabic'::regconfig "
    "ELSE 'english'::regconfig END, text)"
)
TSV_V2 = (
    "to_tsvector(CASE WHEN lang = 'ar' THEN 'arabic'::regconfig "
    "ELSE 'english'::regconfig END, text) || "
    "to_tsvector('arabic'::regconfig, coalesce(search_hint, '')) || "
    "to_tsvector('english'::regconfig, coalesce(search_hint, ''))"
)


def _recreate_tsv(expr: str) -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_column("chunks", "tsv")
    op.add_column(
        "chunks",
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed(expr, persisted=True), nullable=False),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")


def upgrade() -> None:
    op.add_column("chunks", sa.Column("search_hint", sa.Text(), nullable=True))
    _recreate_tsv(TSV_V2)


def downgrade() -> None:
    _recreate_tsv(TSV_V1)
    op.drop_column("chunks", "search_hint")
