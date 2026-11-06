from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]


def _alembic_head() -> str:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def test_health_ok(client) -> None:
    r = await client.get("/health")
    body = r.json()
    assert r.status_code == 200, body
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["pgvector"] is True
    # the database must be at the newest migration (no hard-coded number to update each time)
    assert body["migration"] == _alembic_head()


async def test_openapi_lists_health(client) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert "/health" in r.json()["paths"]
