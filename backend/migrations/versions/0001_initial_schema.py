"""Initial schema v1: pgvector extension, knowledge, conversations, telemetry, admin.

The `chunks.embedding` vector column is added in phase B3 after choosing the embedding model.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVIEW = "review_status IN ('pending', 'approved', 'rejected', 'stale')"
NOW = sa.text("now()")
PENDING = sa.text("'pending'")


def _review_col() -> sa.Column:
    return sa.Column("review_status", sa.String(16), server_default=PENDING, nullable=False)


def _fk(table: str, col: str, ref: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [col], [f"{ref}.id"], name=f"fk_{table}_{col}_{ref}", ondelete=ondelete
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------- Knowledge ----------------
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(2), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        _review_col(),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(REVIEW, name="ck_sources_review_status"),
        sa.CheckConstraint("lang IS NULL OR lang IN ('ar', 'en')", name="ck_sources_lang"),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("url", name="uq_sources_url"),
    )
    op.create_table(
        "faculties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("description_ar", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_faculties"),
        sa.UniqueConstraint("slug", name="uq_faculties_slug"),
    )
    op.create_table(
        "programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("faculty_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("degree", sa.String(32), nullable=True),
        sa.Column("duration_years", sa.SmallInteger(), nullable=True),
        sa.Column("credit_hours", sa.SmallInteger(), nullable=True),
        sa.Column("study_plan_url", sa.Text(), nullable=True),
        sa.Column("summary_ar", sa.Text(), nullable=True),
        sa.Column("summary_en", sa.Text(), nullable=True),
        _review_col(),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("academic_year", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(REVIEW, name="ck_programs_review_status"),
        _fk("programs", "faculty_id", "faculties", "RESTRICT"),
        _fk("programs", "source_id", "sources", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_programs"),
        sa.UniqueConstraint("slug", name="uq_programs_slug"),
    )
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("alt_ar", sa.Text(), nullable=True),
        sa.Column("alt_en", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("kind IN ('image', 'video')", name="ck_media_kind"),
        _fk("media", "source_id", "sources", "SET NULL"),
        _fk("media", "program_id", "programs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_media"),
    )
    op.create_table(
        "labs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.Column("name_ar", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("description_ar", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("location_hint", sa.Text(), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        _fk("labs", "program_id", "programs", "SET NULL"),
        _fk("labs", "media_id", "media", "SET NULL"),
        _fk("labs", "source_id", "sources", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_labs"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.Column("lang", sa.String(2), nullable=False),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector(CASE WHEN lang = 'ar' THEN 'arabic'::regconfig "
                "ELSE 'english'::regconfig END, text)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("academic_year", sa.String(16), nullable=True),
        _review_col(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(REVIEW, name="ck_chunks_review_status"),
        sa.CheckConstraint("lang IN ('ar', 'en')", name="ck_chunks_lang"),
        _fk("chunks", "source_id", "sources", "CASCADE"),
        _fk("chunks", "program_id", "programs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_chunks_review_status", "chunks", ["review_status"])
    op.create_table(
        "faqs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.Column("question_ar", sa.Text(), nullable=True),
        sa.Column("question_en", sa.Text(), nullable=True),
        sa.Column("answer_ar", sa.Text(), nullable=True),
        sa.Column("answer_en", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _review_col(),
        sa.CheckConstraint(REVIEW, name="ck_faqs_review_status"),
        _fk("faqs", "program_id", "programs", "SET NULL"),
        _fk("faqs", "source_id", "sources", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_faqs"),
    )
    op.create_table(
        "pitches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("text_ar", sa.Text(), nullable=True),
        sa.Column("text_en", sa.Text(), nullable=True),
        sa.Column(
            "is_promotional", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        _review_col(),
        sa.CheckConstraint(REVIEW, name="ck_pitches_review_status"),
        _fk("pitches", "program_id", "programs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_pitches"),
    )

    # ---------------- Robot telemetry ----------------
    op.create_table(
        "robot_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("odom_config", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_robot_runs"),
    )
    op.create_table(
        "pose_samples",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("t_ms", sa.BigInteger(), nullable=False),
        sa.Column("x_m", sa.Float(), nullable=False),
        sa.Column("y_m", sa.Float(), nullable=False),
        sa.Column("theta_rad", sa.Float(), nullable=False),
        sa.Column("v_mps", sa.Float(), nullable=True),
        sa.Column("omega_rps", sa.Float(), nullable=True),
        sa.Column("enc", postgresql.JSONB(), nullable=True),
        sa.Column("imu", postgresql.JSONB(), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        _fk("pose_samples", "run_id", "robot_runs", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_pose_samples"),
    )
    op.create_index("ix_pose_samples_run_t", "pose_samples", ["run_id", "t_ms"])
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("t_ms", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        _fk("events", "run_id", "robot_runs", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )
    op.create_index("ix_events_run_t", "events", ["run_id", "t_ms"])
    op.create_table(
        "return_trials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("odom_end_x", sa.Float(), nullable=True),
        sa.Column("odom_end_y", sa.Float(), nullable=True),
        sa.Column("measured_err_m", sa.Float(), nullable=True),
        sa.Column("measured_heading_err_deg", sa.Float(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _fk("return_trials", "run_id", "robot_runs", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_return_trials"),
    )

    # ---------------- Conversations ----------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lang_detected", sa.String(2), nullable=True),
        sa.Column("end_reason", sa.String(16), nullable=True),
        sa.Column("robot_run_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('user_done', 'timeout', 'lost_face', 'error')",
            name="ck_sessions_end_reason",
        ),
        _fk("sessions", "robot_run_id", "robot_runs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=True),
        sa.Column("user_lang", sa.String(2), nullable=True),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("used_chunk_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("used_faq_id", sa.Integer(), nullable=True),
        sa.Column("used_pitch_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("stt_ms", sa.Integer(), nullable=True),
        sa.Column("retrieval_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("tts_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        _fk("turns", "session_id", "sessions", "CASCADE"),
        _fk("turns", "used_faq_id", "faqs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.UniqueConstraint("session_id", "idx", name="uq_turns_session_idx"),
    )

    # ---------------- Admin ----------------
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_admin_users"),
        sa.UniqueConstraint("username", name="uq_admin_users_username"),
    )


def downgrade() -> None:
    for table in (
        "admin_users",
        "turns",
        "sessions",
        "return_trials",
        "events",
        "pose_samples",
        "robot_runs",
        "pitches",
        "faqs",
        "chunks",
        "labs",
        "media",
        "programs",
        "faculties",
        "sources",
    ):
        op.drop_table(table)
    # The vector extension is left installed on purpose (harmless, avoids dropping later data).
