from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, in_values

END_REASONS = ("user_done", "timeout", "lost_face", "error")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            f"end_reason IS NULL OR {in_values('end_reason', END_REASONS)}", name="end_reason"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = created_at_column()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lang_detected: Mapped[str | None] = mapped_column(String(2))
    end_reason: Mapped[str | None] = mapped_column(String(16))
    robot_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("robot_runs.id", ondelete="SET NULL")
    )


class Turn(Base):
    """One question/answer exchange, with per-stage latency for the report."""

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("session_id", "idx", name="uq_turns_session_idx"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    user_text: Mapped[str | None] = mapped_column(Text)
    user_lang: Mapped[str | None] = mapped_column(String(2))
    intent: Mapped[str | None] = mapped_column(String(32))
    answer_text: Mapped[str | None] = mapped_column(Text)
    used_chunk_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    used_faq_id: Mapped[int | None] = mapped_column(ForeignKey("faqs.id", ondelete="SET NULL"))
    used_pitch_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    stt_ms: Mapped[int | None] = mapped_column(Integer)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    tts_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
