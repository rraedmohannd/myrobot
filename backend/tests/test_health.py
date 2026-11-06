async def test_health_ok(client) -> None:
    r = await client.get("/health")
    body = r.json()
    assert r.status_code == 200, body
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["pgvector"] is True
    assert body["migration"] == "0004"


async def test_openapi_lists_health(client) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert "/health" in r.json()["paths"]
