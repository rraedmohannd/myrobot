"""Program explainers: spoken segments with a screen scene each, reviewed like pitches.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "explainers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("lang", sa.String(2), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="ck_explainers_status"
        ),
        sa.CheckConstraint("lang IN ('ar', 'en')", name="ck_explainers_lang"),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["programs.id"],
            name="fk_explainers_program_id_programs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_explainers"),
        sa.UniqueConstraint("program_id", "lang", name="uq_explainers_program_id"),
    )
    op.create_table(
        "explainer_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("explainer_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("scene", postgresql.JSONB(), nullable=False),
        sa.Column("audio_cache_key", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["explainer_id"],
            ["explainers.id"],
            name="fk_explainer_segments_explainer_id_explainers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_explainer_segments"),
    )
    op.create_index(
        "ix_explainer_segments_explainer_position",
        "explainer_segments",
        ["explainer_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_explainer_segments_explainer_position", table_name="explainer_segments")
    op.drop_table("explainer_segments")
    op.drop_table("explainers")
