"""Knowledge tables: MEU Faculty of Engineering content and its sources.

Rule: the robot may only answer from rows with review_status = 'approved'.
The embedding column on `chunks` is added in phase B3, once the embedding model
(and therefore the vector dimension) has been chosen.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import REVIEW_STATUSES, Base, created_at_column, in_values

LANGS = ("ar", "en")
MEDIA_KINDS = ("image", "video")


def review_status_column() -> Mapped[str]:
    return mapped_column(String(16), server_default=text("'pending'"), nullable=False)


def review_check() -> CheckConstraint:
    return CheckConstraint(in_values("review_status", REVIEW_STATUSES), name="review_status")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        review_check(),
        CheckConstraint(f"lang IS NULL OR {in_values('lang', LANGS)}", name="lang"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(2))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    review_status: Mapped[str] = review_status_column()
    notes: Mapped[str | None] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)  # as fetched, for audit/re-cleaning
    clean_text: Mapped[str | None] = mapped_column(Text)  # cleaned page text before chunking


class Faculty(Base):
    __tablename__ = "faculties"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name_ar: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    description_ar: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (CheckConstraint(in_values("kind", MEDIA_KINDS), name="kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(Text)
    alt_ar: Mapped[str | None] = mapped_column(Text)
    alt_en: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="SET NULL")
    )


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (review_check(),)

    id: Mapped[int] = mapped_column(primary_key=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id", ondelete="RESTRICT"))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name_ar: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    degree: Mapped[str | None] = mapped_column(String(32))
    duration_years: Mapped[int | None] = mapped_column(SmallInteger)
    credit_hours: Mapped[int | None] = mapped_column(SmallInteger)
    study_plan_url: Mapped[str | None] = mapped_column(Text)
    summary_ar: Mapped[str | None] = mapped_column(Text)
    summary_en: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = review_status_column()
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    academic_year: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = created_at_column()


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="SET NULL")
    )
    name_ar: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    description_ar: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    location_hint: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        review_check(),
        CheckConstraint(in_values("lang", LANGS), name="lang"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_review_status", "review_status"),
        Index("ix_chunks_source_position", "source_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="SET NULL")
    )
    lang: Mapped[str] = mapped_column(String(2))
    category: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))  # order in page
    text: Mapped[str] = mapped_column(Text)
    # Full-text vector generated by Postgres, with the stemmer chosen by the chunk language.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector(CASE WHEN lang = 'ar' THEN 'arabic'::regconfig "
            "ELSE 'english'::regconfig END, text)",
            persisted=True,
        ),
    )
    academic_year: Mapped[str | None] = mapped_column(String(16))
    review_status: Mapped[str] = review_status_column()
    created_at: Mapped[datetime] = created_at_column()


class Faq(Base):
    __tablename__ = "faqs"
    __table_args__ = (review_check(),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="SET NULL")
    )
    question_ar: Mapped[str | None] = mapped_column(Text)
    question_en: Mapped[str | None] = mapped_column(Text)
    answer_ar: Mapped[str | None] = mapped_column(Text)
    answer_en: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    review_status: Mapped[str] = review_status_column()


class Pitch(Base):
    """Promotional Engineering talking points. Flagged as promotional; never invented numbers."""

    __tablename__ = "pitches"
    __table_args__ = (review_check(),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int | None] = mapped_column(
        ForeignKey("programs.id", ondelete="SET NULL")
    )
    topic: Mapped[str] = mapped_column(Text)
    text_ar: Mapped[str | None] = mapped_column(Text)
    text_en: Mapped[str | None] = mapped_column(Text)
    is_promotional: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    review_status: Mapped[str] = review_status_column()
