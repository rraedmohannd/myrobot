"""Checks that the migrated database matches the models and behaves as designed.

Requires `alembic upgrade head` to have been run on the database in DATABASE_URL.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.types import alembic_compare_type

EXPECTED_TABLES = {
    "sources", "faculties", "programs", "media", "labs", "chunks", "faqs", "pitches",
    "robot_runs", "pose_samples", "events", "return_trials",
    "sessions", "turns", "admin_users", "explainers", "explainer_segments",
}  # fmt: skip


async def test_all_tables_exist(engine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {r[0] for r in rows}
    assert EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables


async def test_models_match_migrations(engine) -> None:
    """If this fails, a model was changed without a new Alembic migration."""

    def diff(sync_conn):
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": alembic_compare_type})
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        changes = await conn.run_sync(diff)
    assert changes == [], changes


async def test_review_status_is_constrained(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text("INSERT INTO sources (url) VALUES ('https://example.test/a')"))
            status = await conn.scalar(
                text("SELECT review_status FROM sources WHERE url = 'https://example.test/a'")
            )
            assert status == "pending"  # new content always starts unreviewed
            nested = await conn.begin_nested()
            with pytest.raises(IntegrityError, match="ck_sources_review_status"):
                await conn.execute(
                    text(
                        "INSERT INTO sources (url, review_status) "
                        "VALUES ('https://example.test/b', 'published')"
                    )
                )
            await nested.rollback()
        finally:
            await trans.rollback()  # leave the database unchanged


async def test_arabic_full_text_search(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            source_id = await conn.scalar(
                text("INSERT INTO sources (url) VALUES ('https://example.test/fts') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO chunks (source_id, lang, text) VALUES "
                    "(:s, 'ar', 'برنامج هندسة الأنظمة الذكية'), "
                    "(:s, 'en', 'Intelligent Systems Engineering programme')"
                ),
                {"s": source_id},
            )
            ar = await conn.scalar(
                text(
                    "SELECT count(*) FROM chunks WHERE source_id = :s "
                    "AND tsv @@ plainto_tsquery('arabic', 'الهندسة')"
                ),
                {"s": source_id},  # only this test's rows: the real DB holds crawled pages too
            )
            en = await conn.scalar(
                text(
                    "SELECT count(*) FROM chunks WHERE source_id = :s "
                    "AND tsv @@ plainto_tsquery('english', 'engineering')"
                ),
                {"s": source_id},
            )
            assert ar == 1
            assert en == 1
        finally:
            await trans.rollback()
