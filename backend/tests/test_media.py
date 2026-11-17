"""Media library (demo sprint M4): upload validation, review, serving only approved files, and
`card.media` on the robot screen only for approved media of the answer's program."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.db.models import Chunk, Faculty, Media, Program, Source
from app.main import app
from app.services import media as media_service
from app.services.dialogue import DialogueManager
from app.services.media import MediaError, get_media_dir, parse_multipart, sniff, validate
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
NOTE = {"source_note": "recorded by the student, with permission (test)"}
UNIQUE = "zorblaxiq"


@pytest.fixture
async def media_client(admin_client, tmp_path) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_media_dir] = lambda: tmp_path
    try:
        yield admin_client
    finally:
        app.dependency_overrides.pop(get_media_dir, None)


async def _program(db_session, slug: str = "test-program") -> Program:
    faculty = Faculty(slug=f"{slug}-faculty", name_ar="كلية تجريبية", name_en="Test faculty")
    db_session.add(faculty)
    await db_session.flush()
    program = Program(
        faculty_id=faculty.id, slug=slug, name_ar="تخصص تجريبي", name_en="Test program"
    )
    db_session.add(program)
    await db_session.commit()
    return program


async def _upload(client, name: str, data: bytes, **fields):  # noqa: ANN202
    return await client.post(
        "/api/admin/media", files={"file": (name, data)}, data={**NOTE, **fields}
    )


# ---------------- validation ----------------
def test_validate_accepts_only_the_five_types() -> None:
    assert validate("a.PNG", PNG) == ("image", "image/png", "png")
    assert validate("photo.jpeg", JPG) == ("image", "image/jpeg", "jpg")
    assert validate("a.webp", WEBP) == ("image", "image/webp", "webp")
    assert validate("clip.mp4", MP4) == ("video", "video/mp4", "mp4")
    assert validate("clip.webm", WEBM) == ("video", "video/webm", "webm")
    for name, data in (
        ("a.gif", b"GIF89a"),
        ("a.svg", b"<svg/>"),
        ("a.exe", PNG),
        ("noext", PNG),
        ("a.png", JPG),
        ("a.mp4", PNG),
        ("a.png", b""),
        ("a.png", b"<html>"),
    ):
        with pytest.raises(MediaError):
            validate(name, data)
    assert sniff(b"hello") is None


def test_parse_multipart() -> None:
    body = (
        b'--XX\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
        b"\xd8\xb5\xd9\x88\xd8\xb1\xd8\xa9\r\n"
        b'--XX\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
        b"Content-Type: image/png\r\n\r\n" + PNG + b"\r\n--XX--\r\n"
    )
    fields, files = parse_multipart("multipart/form-data; boundary=XX", body)
    assert fields == {"title": "صورة"}
    assert files["file"].filename == "a.png" and files["file"].data == PNG
    with pytest.raises(MediaError):
        parse_multipart("application/json", b"{}")


def test_demo_placeholders_are_valid_png_files() -> None:
    folder = Path(media_service.__file__).resolve().parents[1] / "demo_media"
    files = sorted(folder.glob("placeholder_*.png"))
    assert len(files) == 2
    for f in files:
        assert validate(f.name, f.read_bytes())[1] == "image/png"


# ---------------- admin API ----------------
@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/api/admin/media"), ("get", "/api/admin/media"), ("patch", "/api/admin/media/1"),
     ("post", "/api/admin/media/1/review"), ("get", "/api/admin/media/1/file"),
     ("delete", "/api/admin/media/1")],
)  # fmt: skip
async def test_media_admin_needs_the_token(client, method: str, path: str) -> None:
    assert (await getattr(client, method)(path)).status_code == 401


async def test_upload_review_and_serve(media_client, db_session, tmp_path) -> None:
    program = await _program(db_session)
    r = await _upload(media_client, "lab.png", PNG, title="Lab", alt_ar="مختبر", alt_en="A lab",
                      program_id=str(program.id))  # fmt: skip
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "image" and m["mime"] == "image/png" and m["size_bytes"] == len(PNG)
    assert m["review_status"] == "pending" and m["program_id"] == program.id
    assert m["program_name"] == "Test program" and m["source_note"] == NOTE["source_note"]
    stored = list(tmp_path.iterdir())
    assert len(stored) == 1 and stored[0].suffix == ".png" and "lab" not in stored[0].name

    # pending: not served to the robot screen, but the admin can preview it
    assert (await media_client.get(m["url"])).status_code == 404
    preview = await media_client.get(f"/api/admin/media/{m['id']}/file")
    assert preview.status_code == 200 and preview.content == PNG
    assert preview.headers["content-type"] == "image/png"

    listed = await media_client.get("/api/admin/media")
    assert any(x["id"] == m["id"] for x in listed.json())

    r = await media_client.post(f"/api/admin/media/{m['id']}/review", json={"decision": "approve"})
    assert r.json()["review_status"] == "approved"
    served = await media_client.get(m["url"])
    assert served.status_code == 200 and served.content == PNG

    r = await media_client.post(f"/api/admin/media/{m['id']}/review", json={"decision": "reject"})
    assert r.json()["review_status"] == "rejected"
    assert (await media_client.get(m["url"])).status_code == 404  # rejected: gone again

    r = await media_client.patch(
        f"/api/admin/media/{m['id']}", json={"title": "New", "program_id": None}
    )
    assert r.json()["title"] == "New" and r.json()["program_id"] is None
    assert (
        await media_client.patch(f"/api/admin/media/{m['id']}", json={"program_id": 999999999})
    ).status_code == 422
    bad = await media_client.post(
        f"/api/admin/media/{m['id']}/review", json={"decision": "publish"}
    )
    assert bad.status_code == 422

    assert (await media_client.delete(f"/api/admin/media/{m['id']}")).status_code == 204
    assert list(tmp_path.iterdir()) == []
    assert (await media_client.get(f"/api/admin/media/{m['id']}/file")).status_code == 404


async def test_public_route_does_not_need_a_token_but_only_serves_approved(
    media_client, client, db_session
) -> None:
    m = (await _upload(media_client, "clip.webm", WEBM)).json()
    assert m["kind"] == "video"
    assert (await client.get(m["url"])).status_code == 404
    await media_client.post(f"/api/admin/media/{m['id']}/review", json={"decision": "approve"})
    r = await media_client.get(m["url"], headers={"X-Admin-Token": ""})
    assert r.status_code == 200 and r.headers["content-type"] == "video/webm"
    assert (await client.get("/media/999999999")).status_code == 404


async def test_upload_validation(media_client, db_session, monkeypatch) -> None:
    assert (await _upload(media_client, "a.gif", b"GIF89a")).status_code == 422
    assert (
        await _upload(media_client, "fake.png", b"<html>not an image</html>")
    ).status_code == 422
    assert (await _upload(media_client, "a.png", PNG, program_id="999999999")).status_code == 422
    assert (await _upload(media_client, "a.png", PNG, program_id="abc")).status_code == 422
    no_note = await media_client.post("/api/admin/media", files={"file": ("a.png", PNG)})
    assert no_note.status_code == 422 and "source_note" in no_note.json()["detail"]
    no_file = await media_client.post(
        "/api/admin/media", data=NOTE, files={"other": ("a.png", PNG)}
    )
    assert no_file.status_code == 422
    not_form = await media_client.post("/api/admin/media", json={"file": "x"})
    assert not_form.status_code == 422
    before = (await media_client.get("/api/admin/media")).json()

    # the 50 MB limit, tested with a small limit so the test stays fast
    monkeypatch.setattr("app.services.media.MAX_BYTES", 1000)
    monkeypatch.setattr("app.api.rest.media.MAX_BYTES", 1000)
    monkeypatch.setattr("app.api.rest.media.FORM_OVERHEAD", 100_000)
    assert (await _upload(media_client, "big.png", PNG + b"\x00" * 2000)).status_code == 413
    monkeypatch.setattr("app.api.rest.media.FORM_OVERHEAD", 0)
    assert (await _upload(media_client, "big.png", PNG + b"\x00" * 2000)).status_code == 413
    assert (await media_client.get("/api/admin/media")).json() == before  # nothing was stored


# ---------------- robot screen: card.media ----------------
async def _ask(db_session) -> dict | None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    dm = DialogueManager(
        send, OneSession(db_session), FakeEmbedder(), FakeLLM(), FakeSTT([]), FakeTTS()
    )
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})
    return next((m["card"] for m in sent if m["type"] == "display.update" and m.get("card")), None)


async def test_card_has_media_only_when_approved_and_for_one_program(db_session) -> None:
    program = await _program(db_session)
    src = Source(url="test-doc://media", title="Media test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    chunk = Chunk(source_id=src.id, lang="en", review_status="approved", program_id=program.id,
                  text=f"The {UNIQUE} program requires 162 credit hours.")  # fmt: skip
    image = Media(
        kind="image",
        path="x.png",
        mime="image/png",
        program_id=program.id,
        alt_ar="مختبر",
        alt_en="A lab",
        source_note="test",
        review_status="pending",
    )
    db_session.add_all([chunk, image])
    await db_session.commit()

    card = await _ask(db_session)
    assert card["title"] == "Test program" and "media" not in card  # pending media is never shown

    image.review_status = "rejected"
    await db_session.commit()
    assert "media" not in await _ask(db_session)

    image.review_status = "approved"
    await db_session.commit()
    card = await _ask(db_session)
    assert card["media"] == {"kind": "image", "url": f"/media/{image.id}", "alt": "A lab"}

    chunk.program_id = None  # a general answer (no single program): no media
    await db_session.commit()
    card = await _ask(db_session)
    assert card["title"] == "Faculty of Engineering & Design" and "media" not in card
