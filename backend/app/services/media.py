"""Media library for the robot screen (demo sprint M4): validation, storage, selection.

Rules: files stay local in backend/data/media/ (git-ignored); only jpg, png, webp, mp4 and webm
up to 50 MB; every file starts as `pending` and is shown only after a human approved it.
Nothing is ever downloaded from the internet here.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Media

MEDIA_DIR = Path(__file__).resolve().parents[2] / "data" / "media"
MAX_BYTES = 50 * 1024 * 1024
# extension -> (kind, mime)
ALLOWED: dict[str, tuple[str, str]] = {
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "png": ("image", "image/png"),
    "webp": ("image", "image/webp"),
    "mp4": ("video", "video/mp4"),
    "webm": ("video", "video/webm"),
}


def max_bytes() -> int:
    """MEDIA_MAX_MB when set, else MAX_BYTES (50 MB)."""
    from app.core.config import get_settings

    return get_settings().media_max_bytes or MAX_BYTES


def too_large_message() -> str:
    return f"The file is larger than {max_bytes() / (1024 * 1024):g} MB"


class MediaError(ValueError):
    """The upload is not acceptable (wrong type, too large, broken form)."""


def get_media_dir() -> Path:
    """FastAPI dependency (tests point it at a temporary folder)."""
    return MEDIA_DIR


def sniff(data: bytes) -> str | None:
    """File type from the first bytes, so a renamed file cannot pass as an image or video."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def validate(filename: str, data: bytes) -> tuple[str, str, str]:
    """-> (kind, mime, extension). Raises MediaError with a message for the admin."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise MediaError("Only jpg, png, webp, mp4 and webm files are allowed")
    if not data:
        raise MediaError("The file is empty")
    if len(data) > max_bytes():
        raise MediaError(too_large_message())
    kind, mime = ALLOWED[ext]
    if sniff(data) != mime:
        raise MediaError(f"The file content is not {mime} (the extension says .{ext})")
    return kind, mime, "jpg" if ext == "jpeg" else ext


def store(data: bytes, ext: str, media_dir: Path) -> str:
    """Write the bytes under a random name (never the uploaded name) and return that name."""
    media_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (media_dir / name).write_bytes(data)
    return name


def file_path(media: Media, media_dir: Path) -> Path | None:
    """The stored file, or None when the row points outside the folder or the file is gone."""
    path = (media_dir / Path(media.path).name).resolve()
    return path if path.is_file() and path.parent == media_dir.resolve() else None


# ---------------- multipart/form-data (small parser: no extra dependency) ----------------
@dataclass
class FormFile:
    filename: str
    content_type: str | None
    data: bytes


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, FormFile]]:
    """Fields and files of a multipart/form-data body."""
    if "multipart/form-data" not in content_type.lower() or "boundary=" not in content_type:
        raise MediaError("Expected a multipart/form-data upload")
    boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    delimiter = b"--" + boundary.encode()
    fields: dict[str, str] = {}
    files: dict[str, FormFile] = {}
    for part in body.split(delimiter)[1:]:
        if part.startswith(b"--"):
            break  # closing delimiter
        part = part.removeprefix(b"\r\n")
        if part.endswith(b"\r\n"):
            part = part[:-2]
        raw_headers, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            raise MediaError("Broken multipart part")
        headers: dict[str, str] = {}
        for line in raw_headers.decode("utf-8", "replace").split("\r\n"):
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        disposition = _params(headers.get("content-disposition", ""))
        name = disposition.get("name")
        if not name:
            continue
        if "filename" in disposition:
            files[name] = FormFile(disposition["filename"], headers.get("content-type"), content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


def _params(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in header.split(";")[1:]:
        key, _, value = item.strip().partition("=")
        out[key.lower()] = value.strip().strip('"')
    return out


# ---------------- selection for the robot screen ----------------
async def approved_media_for_program(
    session: AsyncSession, program_id: int, lang: str, rotate: int = 0
) -> dict[str, Any] | None:
    """`card.media` for a program: one of its APPROVED files (rotating), or None."""
    rows = (
        await session.scalars(
            select(Media)
            .where(Media.program_id == program_id, Media.review_status == "approved")
            .order_by(Media.id)
        )
    ).all()
    if not rows:
        return None
    m = rows[rotate % len(rows)]
    alt = (m.alt_ar if lang == "ar" else m.alt_en) or m.alt_ar or m.alt_en or m.title or ""
    return {"kind": m.kind, "url": f"/media/{m.id}", "alt": alt}
