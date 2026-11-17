"""Sprint 5: power in pose samples, calibration records, bring-up checklist ticks.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pose_samples", sa.Column("power", postgresql.JSONB(), nullable=True))
    op.create_table(
        "calibration_records",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("robot_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator", sa.String(64), nullable=True),
        sa.Column("method", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calibration_records"),
    )
    op.create_index(
        "ix_calibration_records_robot_key", "calibration_records", ["robot_id", "key"]
    )
    op.create_table(
        "bringup_steps",
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("done_by", sa.String(64), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("step_id", name="pk_bringup_steps"),
    )


def downgrade() -> None:
    op.drop_table("bringup_steps")
    op.drop_index("ix_calibration_records_robot_key", table_name="calibration_records")
    op.drop_table("calibration_records")
    op.drop_column("pose_samples", "power")
