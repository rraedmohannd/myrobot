"""Chunk embeddings for semantic search (pgvector, 1536 dims = text-embedding-3-small).

No ANN index yet: with a few hundred chunks an exact scan is fast and fully accurate.
Add an HNSW index (vector_cosine_ops) if the knowledge base grows to many thousands of chunks.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import Vector

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column("chunks", sa.Column("embedding_model", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "embedding_model")
    op.drop_column("chunks", "embedding")
