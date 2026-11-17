"""Sprint 2 S4: the idle robot screen (attract mode and touch buttons) only ever gets approved
content, and the page stops the attract mode as soon as a conversation starts."""

from app.services.explainers import screen_content
from tests.test_explainers import SLUG, _explainer, _program


async def test_idle_screen_content_is_approved_only(admin_client, db_session) -> None:
    program = await _program(db_session)
    explainer = await _explainer(db_session, program, status="pending")
    content = await screen_content(db_session)
    assert SLUG not in [p["slug"] for p in content["programs"]]
    assert not any(s["program"]["slug"] == SLUG for s in content["attract"])

    explainer.status = "approved"
    await db_session.commit()
    r = await admin_client.get("/api/robot/screen")  # public endpoint (the token is not needed)
    assert r.status_code == 200
    body = r.json()
    entry = next(p for p in body["programs"] if p["slug"] == SLUG)
    assert entry["name_ar"] == "هندسة الزوربولوجي" and entry["langs"] == ["ar"]
    mine = [s for s in body["attract"] if s["program"]["slug"] == SLUG]
    assert [s["type"] for s in mine] == ["title", "stat"] and mine[1]["value"] == "162"
    assert isinstance(body["interests"], bool)

    explainer.status = "rejected"
    await db_session.commit()
    assert SLUG not in [p["slug"] for p in (await screen_content(db_session))["programs"]]


async def test_robot_page_has_explainer_scenes_and_local_qr(client) -> None:
    robot = (await client.get("/robot")).text
    for needle in (
        "/static/explainer-scenes.js",
        "/static/vendor/qrcode/qrcode.js",
        "ساعدني أختار",
        "قرّب واحكي معي",
    ):
        assert needle in robot, needle
    assert "https://" not in robot and "http://" not in robot.replace("http://www.w3.org", "")
    scenes = (await client.get("/static/explainer-scenes.js")).text
    for kind in ("title", "keypoints", "stat", "timeline", "courses", "diagram", "media", "qr"):
        assert f"    {kind}(scene" in scenes, kind
    for slug in (
        "intelligent-systems-engineering",
        "renewable-energy-engineering",
        "architectural-engineering",
        "interior-design",
        "graphic-design",
        "biomedical-engineering",
    ):
        assert slug in scenes, slug
    assert "https://" not in scenes and "http://" not in scenes.replace("http://www.w3.org", "")
    assert (await client.get("/static/vendor/qrcode/qrcode.js")).status_code == 200
    assert (await client.get("/static/vendor/qrcode/LICENSE")).status_code == 200
    admin = (await client.get("/admin")).text
    assert 'data-view="explainers"' in admin and "/static/explainer-scenes.js" in admin


async def test_attract_mode_stops_when_a_conversation_starts(client) -> None:
    robot = (await client.get("/robot")).text
    # the session.started handler and both ways of starting (start button, touch buttons) stop it
    assert (
        'case "session.started": inSession = true; setCard(null); $("heard").textContent = ""; '
        "stopAttract(); break;" in robot
    )
    assert robot.count("starting = true; stopAttract();") == 2
    assert "/api/robot/screen" in robot and "setInterval(nextAttract, 7000)" in robot
    assert (
        "if (showAttract) startAttract(); else stopAttract();" in robot
    )  # only on the idle screen
    assert 'id="attractName"' in robot  # a number on the idle screen always names its program
