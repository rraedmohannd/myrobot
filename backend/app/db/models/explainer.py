"""Program explainers (sprint 2): a pre-approved, pre-generated presentation of one program.

An explainer is spoken in segments, and each segment has an animated scene on the robot screen.
Like pitches, the content is promotional in tone but factual: it is drafted from the program's
approved sources, stored as 'pending', and only a human approval makes the robot use it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, in_values
from app.db.models.knowledge import LANGS

EXPLAINER_STATUSES = ("pending", "approved", "rejected")


class Explainer(Base):
    __tablename__ = "explainers"
    __table_args__ = (
        CheckConstraint(in_values("status", EXPLAINER_STATUSES), name="status"),
        CheckConstraint(in_values("lang", LANGS), name="lang"),
        UniqueConstraint("program_id", "lang"),  # one explainer per program and language
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExplainerSegment(Base):
    __tablename__ = "explainer_segments"
    __table_args__ = (
        Index("ix_explainer_segments_explainer_position", "explainer_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    explainer_id: Mapped[int] = mapped_column(ForeignKey("explainers.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # order of the segment in the presentation
    text: Mapped[str] = mapped_column(Text)  # what the robot says
    scene: Mapped[dict[str, Any]] = mapped_column(JSONB)  # what the screen shows (docs/PROTOCOL.md)
    # Key of the generated speech in the TTS cache; NULL until the audio has been prepared.
    audio_cache_key: Mapped[str | None] = mapped_column(String(64))
