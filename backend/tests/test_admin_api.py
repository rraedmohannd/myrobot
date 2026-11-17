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
    rows = (
        await db_session.scalars(select(Chunk.review_status).where(Chunk.source_id == sid))
    ).all()
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

    bad = await admin_client.patch(
        f"/api/admin/chunks/{chunk_id}", json={"review_status": "published"}
    )
    assert bad.status_code == 422
    assert (
        await admin_client.patch("/api/admin/chunks/999999999", json={"text": "x"})
    ).status_code == 404


# ---------- sprint 4 P0.1 ----------
async def test_reject_switches_off_approved_chunks_too(admin_client, db_session) -> None:
    """A source approved earlier (or with chunks approved one by one) and rejected later:
    no chunk stays approved."""
    sid = await _seed(db_session)
    r = await admin_client.post(f"/api/admin/sources/{sid}/review", json={"decision": "approve"})
    assert r.json()["chunk_counts"] == {"approved": 2}
    db_session.add(Chunk(source_id=sid, lang="ar", text="نص جديد", position=3))  # pending
    await db_session.commit()

    r = await admin_client.post(f"/api/admin/sources/{sid}/review", json={"decision": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "rejected"
    assert r.json()["chunk_counts"] == {"rejected": 3}
    rows = (
        await db_session.scalars(select(Chunk.review_status).where(Chunk.source_id == sid))
    ).all()
    assert set(rows) == {"rejected"}


async def test_reject_leaves_other_sources_alone(admin_client, db_session) -> None:
    sid = await _seed(db_session)
    other = Source(url="https://meu.test/admin-api-other/", title="Other", review_status="approved")
    db_session.add(other)
    await db_session.flush()
    db_session.add(Chunk(source_id=other.id, lang="ar", text="نص آخر", review_status="approved"))
    await db_session.commit()
    await admin_client.post(f"/api/admin/sources/{sid}/review", json={"decision": "reject"})
    rows = (
        await db_session.scalars(select(Chunk.review_status).where(Chunk.source_id == other.id))
    ).all()
    assert rows == ["approved"]


async def test_overview_warns_about_approved_chunks_of_unapproved_sources(
    admin_client, db_session
) -> None:
    key = "approved_chunks_of_unapproved_sources"
    before = (await admin_client.get("/api/admin/analytics/overview")).json()["knowledge_warnings"][
        key
    ]
    assert before["chunks"] == sum(s["approved_chunks"] for s in before["sources"])

    ok = Source(url="https://meu.test/warn-ok/", title="Fine", review_status="approved")
    bad = Source(url="https://meu.test/warn-rejected/", title="Old list", review_status="rejected")
    old = Source(url="https://meu.test/warn-stale/", title="Changed page", review_status="stale")
    db_session.add_all([ok, bad, old])
    await db_session.flush()
    db_session.add_all([
        Chunk(source_id=ok.id, lang="ar", text="سليم", review_status="approved"),
        Chunk(source_id=bad.id, lang="ar", text="قديم 1", review_status="approved"),
        Chunk(source_id=bad.id, lang="ar", text="قديم 2", review_status="approved"),
        Chunk(source_id=bad.id, lang="ar", text="مرفوض", review_status="rejected"),
        Chunk(source_id=old.id, lang="ar", text="تغيّر", review_status="approved"),
    ])  # fmt: skip
    await db_session.commit()

    after = (await admin_client.get("/api/admin/analytics/overview")).json()["knowledge_warnings"][
        key
    ]
    assert after["chunks"] - before["chunks"] == 3
    listed = {s["id"]: s for s in after["sources"]}
    assert ok.id not in listed
    assert listed[bad.id] == {"id": bad.id, "title": "Old list", "url": bad.url,
                              "review_status": "rejected", "approved_chunks": 2}  # fmt: skip
    assert listed[old.id]["approved_chunks"] == 1 and listed[old.id]["review_status"] == "stale"

    # rejecting the source through the API clears its part of the warning
    await admin_client.post(f"/api/admin/sources/{bad.id}/review", json={"decision": "reject"})
    again = (await admin_client.get("/api/admin/analytics/overview")).json()["knowledge_warnings"][
        key
    ]
    assert bad.id not in {s["id"] for s in again["sources"]}


async def test_admin_page_shows_the_knowledge_warning(client) -> None:
    page = (await client.get("/admin")).text
    assert "approved_chunks_of_unapproved_sources" in page and "ovKnowledgeWarning" in page
