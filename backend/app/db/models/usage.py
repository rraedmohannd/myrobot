from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaidCallDay(Base):
    """Real AI provider calls (embedding, STT, LLM, TTS) per day in LOCAL_TIMEZONE: the global
    daily cap DAILY_PAID_CALL_LIMIT (services/cost_guard.py). Kept in the database so the cap
    survives a restart. No personal data."""

    __tablename__ = "paid_call_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refused: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
