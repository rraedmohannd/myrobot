from sqlalchemy import select

from app.db.models import Chunk, Source


async def _seed(db_session) -> int:
    src = Source(url="https://meu.test/admin-api-page/", title="Test page", lang="ar")
    db_session.add(src)
    await db_session.flush()
    db_session.add_all(
        [
            Chunk(source_id=src.id, lang="ar", text="نص أول", position=0),
            Chunk(source_id=src.id, lang="ar", text="نص ثاني", position=1),
            Chunk(source_id=src.id, lang="ar", text="نص قديم", position=2, review_status="stale"),
        ]
    )
    await db_session.commit()
    return src.id


async def test_admin_requires_token(client) -> None:
    assert (await client.get("/api/admin/stats")).status_code == 401
    r = await client.get("/api/admin/stats", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


async def test_admin_page_is_served(client) -> None:
    r = await client.get("/admin")
    assert r.status_code == 200
    assert "Review Queue" in r.text


async def test_list_and_detail(admin_client, db_session) -> None:
    sid = await _seed(db_session)
    first = await admin_client.get("/api/admin/sources", params={"status": "pending", "limit": 1})
    assert first.status_code == 200
    last = first.json()["total"] - 1  # our page has the highest id, so it is listed last
    r = await admin_client.get("/api/admin/sources", params={"status": "pending", "offset": last})
    item = next(i for i in r.json()["items"] if i["id"] == sid)
    assert item["chunk_counts"] == {"pending": 2, "stale": 1}

    r = await admin_client.get(f"/api/admin/sources/{sid}")
    assert [c["position"] for c in r.json()["chunks"]] == [0, 1, 2]


async def test_approve_moves_pending_and_drops_stale(admin_client, db_session) -> None:
    sid = await _seed(db_session)
    r = await admin_client.post(f"/api/admin/sources/{sid}/review", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "approved"
    assert body["chunk_counts"] == {"approved": 2}
    rows = (await db_session.scalars(select(Chunk.review_status).where(Chunk.source_id == sid))).all()
    assert sorted(rows) == ["approved", "approved"]


async def test_reject(admin_client, db_session) -> None:
    sid = await _seed(db_session)
    r = await admin_client.post(f"/api/admin/sources/{sid}/review", json={"decision": "reject"})
    assert r.json()["review_status"] == "rejected"
    assert r.json()["chunk_counts"] == {"rejected": 2, "stale": 1}


async def test_edit_chunk_text_and_status(admin_client, db_session) -> None:
    sid = await _seed(db_session)
    chunk_id = (await admin_client.get(f"/api/admin/sources/{sid}")).json()["chunks"][0]["id"]
    r = await admin_client.patch(
        f"/api/admin/chunks/{chunk_id}", json={"text": "نص معدل", "review_status": "rejected"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "نص معدل"
    assert r.json()["review_status"] == "rejected"

    bad = await admin_client.patch(f"/api/admin/chunks/{chunk_id}", json={"review_status": "published"})
    assert bad.status_code == 422
    assert (await admin_client.patch("/api/admin/chunks/999999999", json={"text": "x"})).status_code == 404
