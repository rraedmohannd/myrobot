"""Robot control: commands and acks as events (also outside a run), pending return trials.

Existing return trials were typed in by hand, so they are 'measured'.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("events", "run_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "events",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "return_trials",
        sa.Column("status", sa.String(16), server_default=sa.text("'measured'"), nullable=False),
    )
    op.add_column("return_trials", sa.Column("command_id", sa.String(64), nullable=True))
    op.add_column(
        "return_trials",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_check_constraint(
        "ck_return_trials_status", "return_trials", "status IN ('pending', 'measured')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_return_trials_status", "return_trials", type_="check")
    op.drop_column("return_trials", "created_at")
    op.drop_column("return_trials", "command_id")
    op.drop_column("return_trials", "status")
    op.drop_column("events", "created_at")
    op.execute("DELETE FROM events WHERE run_id IS NULL")
    op.alter_column("events", "run_id", existing_type=sa.Integer(), nullable=False)
