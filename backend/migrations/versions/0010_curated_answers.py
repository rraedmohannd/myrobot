"""Curated answers: FAQ question vectors, places, reviewed labs, where an answer came from.

Existing labs (none are expected) become 'pending', so nothing is used before a human review.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = "review_status IN ('pending', 'approved', 'rejected', 'stale')"


def upgrade() -> None:
    op.execute("ALTER TABLE faqs ADD COLUMN embedding_ar vector(1536)")
    op.execute("ALTER TABLE faqs ADD COLUMN embedding_en vector(1536)")
    op.add_column("faqs", sa.Column("embedding_model", sa.String(64), nullable=True))
    op.add_column(
        "faqs",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("labs", sa.Column("keywords", sa.Text(), nullable=True))
    op.add_column(
        "labs",
        sa.Column(
            "review_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False
        ),
    )
    op.create_check_constraint("ck_labs_review_status", "labs", STATUS)
    op.create_table(
        "places",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("building", sa.Text(), nullable=True),
        sa.Column("floor", sa.String(32), nullable=True),
        sa.Column("directions_ar", sa.Text(), nullable=True),
        sa.Column("directions_en", sa.Text(), nullable=True),
        sa.Column("related_office", sa.Text(), nullable=True),
        sa.Column("keywords", sa.Text(), nullable=True),
        sa.Column("contact_chunk_id", sa.Integer(), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column(
            "review_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(STATUS, name="ck_places_review_status"),
        sa.ForeignKeyConstraint(
            ["contact_chunk_id"],
            ["chunks.id"],
            name="fk_places_contact_chunk_id_chunks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"], name="fk_places_media_id_media", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_places"),
    )
    op.add_column("turns", sa.Column("answer_kind", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("turns", "answer_kind")
    op.drop_table("places")
    op.drop_constraint("ck_labs_review_status", "labs", type_="check")
    op.drop_column("labs", "review_status")
    op.drop_column("labs", "keywords")
    op.drop_column("faqs", "created_at")
    op.drop_column("faqs", "embedding_model")
    op.execute("ALTER TABLE faqs DROP COLUMN embedding_en")
    op.execute("ALTER TABLE faqs DROP COLUMN embedding_ar")
