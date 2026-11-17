"""Bring-up (sprint 5 S6.3): calibration records reported by the robot, and the ticks of the
docs/BRINGUP.md checklist.

A calibration record with is_simulated = true was written by tests or against the simulated
ESP32: it is never a measurement and is always shown apart (CLAUDE.md rule 5).
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column


class CalibrationRecord(Base):
    """One value of the robot's calibration file. A new row is stored only when the value of a
    key changes, so the rows of a key are its history."""

    __tablename__ = "calibration_records"
    __table_args__ = (Index("ix_calibration_records_robot_key", "robot_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    robot_id: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[object] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(32))
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[object | None] = mapped_column(JSONB)
    is_simulated: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[datetime] = created_at_column()


class BringupStep(Base):
    """The tick of one checklist step of docs/BRINGUP.md (`<!-- step:<id> -->`)."""

    __tablename__ = "bringup_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    done: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    done_by: Mapped[str | None] = mapped_column(String(64))  # admin user name, or "token"
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
