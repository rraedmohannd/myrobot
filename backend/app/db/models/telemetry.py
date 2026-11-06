"""Robot telemetry: runs, pose samples, events, and physically measured return trials.

Simulated data must set robot_runs.is_simulated = true and never be reported as a physical test.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column


class RobotRun(Base):
    __tablename__ = "robot_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = created_at_column()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)
    odom_config: Mapped[dict | None] = mapped_column(JSONB)


class PoseSample(Base):
    __tablename__ = "pose_samples"
    __table_args__ = (Index("ix_pose_samples_run_t", "run_id", "t_ms"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("robot_runs.id", ondelete="CASCADE"))
    t_ms: Mapped[int] = mapped_column(BigInteger)
    x_m: Mapped[float] = mapped_column(Float)
    y_m: Mapped[float] = mapped_column(Float)
    theta_rad: Mapped[float] = mapped_column(Float)
    v_mps: Mapped[float | None] = mapped_column(Float)
    omega_rps: Mapped[float | None] = mapped_column(Float)
    enc: Mapped[dict | None] = mapped_column(JSONB)
    imu: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str | None] = mapped_column(String(32))


class Event(Base):
    """kind examples: obstacle, return_start, return_done, return_failed, estop, comm_loss."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_run_t", "run_id", "t_ms"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("robot_runs.id", ondelete="CASCADE"))
    t_ms: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict | None] = mapped_column(JSONB)


class ReturnTrial(Base):
    """Return-to-origin trial; measured_* values are taken with a tape on the floor."""

    __tablename__ = "return_trials"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("robot_runs.id", ondelete="CASCADE"))
    odom_end_x: Mapped[float | None] = mapped_column(Float)
    odom_end_y: Mapped[float | None] = mapped_column(Float)
    measured_err_m: Mapped[float | None] = mapped_column(Float)
    measured_heading_err_deg: Mapped[float | None] = mapped_column(Float)
    success: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
