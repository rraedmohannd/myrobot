"""Media library API (demo sprint M4).

Admin (X-Admin-Token): upload, list, edit, approve / reject, preview, delete.
Public: GET /media/{id} serves a file only when it is approved.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, require_admin
from app.db.models import Media, Program
from app.services.media import (
    MAX_BYTES,
    MediaError,
    file_path,
    get_media_dir,
    parse_multipart,
    store,
    validate,
)

router = APIRouter(prefix="/api/admin/media", tags=["admin"], dependencies=[Depends(require_admin)])
public_router = APIRouter(tags=["media"])

MediaDir = Annotated[Path, Depends(get_media_dir)]
FORM_OVERHEAD = 64 * 1024  # room for the text fields and multipart headers


class MediaOut(BaseModel):
    id: int
    kind: str
    title: str | None
    alt_ar: str | None
    alt_en: str | None
    mime: str | None
    size_bytes: int | None
    source_note: str | None
    review_status: str
    program_id: int | None
    program_name: str | None = None
    created_at: datetime
    url: str  # public URL; it answers 404 until the file is approved


class MediaPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    alt_ar: str | None = Field(default=None, max_length=300)
    alt_en: str | None = Field(default=None, max_length=300)
    source_note: str | None = Field(default=None, min_length=3, max_length=500)
    program_id: int | None = None


class MediaReview(BaseModel):
    decision: Literal["approve", "reject"]


async def _out(session, m: Media) -> MediaOut:
    program = await session.get(Program, m.program_id) if m.program_id else None
    return MediaOut(
        id=m.id, kind=m.kind, title=m.title, alt_ar=m.alt_ar, alt_en=m.alt_en, mime=m.mime,
        size_bytes=m.size_bytes, source_note=m.source_note, review_status=m.review_status,
        program_id=m.program_id, program_name=program.name_en if program else None,
        created_at=m.created_at, url=f"/media/{m.id}",
    )  # fmt: skip


async def _get(session, media_id: int) -> Media:
    m = await session.get(Media, media_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    return m


async def _program_id(session, raw: str | int | None) -> int | None:
    if raw in (None, ""):
        return None
    try:
        program_id = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(422, "program_id must be a number") from None
    if await session.get(Program, program_id) is None:
        raise HTTPException(422, "Unknown program_id")
    return program_id


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it grows past the limit."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "The file is larger than 50 MB")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "The file is larger than 50 MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=MediaOut, status_code=status.HTTP_201_CREATED)
async def upload(request: Request, session: SessionDep, media_dir: MediaDir) -> MediaOut:
    """multipart/form-data: `file` (jpg, png, webp, mp4 or webm, at most 50 MB), `source_note`
    (required: where the file came from and the permission), and optional `title`, `alt_ar`,
    `alt_en`, `program_id`. The file is stored as `pending`."""
    body = await _read_body(request, MAX_BYTES + FORM_OVERHEAD)
    try:
        fields, files = parse_multipart(request.headers.get("content-type", ""), body)
        if "file" not in files:
            raise MediaError("The form has no 'file'")
        upload_file = files["file"]
        kind, mime, ext = validate(upload_file.filename, upload_file.data)
    except MediaError as e:
        code = 413 if "larger than" in str(e) else 422
        raise HTTPException(code, str(e)) from e
    source_note = (fields.get("source_note") or "").strip()
    if len(source_note) < 3:
        raise HTTPException(422, "source_note is required: where the file came from and the permission")
    program_id = await _program_id(session, fields.get("program_id"))
    m = Media(
        kind=kind, path=store(upload_file.data, ext, media_dir), mime=mime,
        size_bytes=len(upload_file.data), title=(fields.get("title") or "").strip() or None,
        alt_ar=(fields.get("alt_ar") or "").strip() or None,
        alt_en=(fields.get("alt_en") or "").strip() or None,
        source_note=source_note[:500], program_id=program_id, review_status="pending",
    )  # fmt: skip
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return await _out(session, m)


@router.get("", response_model=list[MediaOut])
async def list_media(session: SessionDep) -> list[MediaOut]:
    rows = (await session.scalars(select(Media).order_by(Media.id.desc()))).all()
    return [await _out(session, m) for m in rows]


@router.patch("/{media_id}", response_model=MediaOut)
async def patch_media(media_id: int, body: MediaPatch, session: SessionDep) -> MediaOut:
    m = await _get(session, media_id)
    changes = body.model_dump(exclude_unset=True)
    if "program_id" in changes:
        changes["program_id"] = await _program_id(session, changes["program_id"])
    if "source_note" in changes and changes["source_note"] is None:
        raise HTTPException(422, "source_note cannot be removed")
    for key, value in changes.items():
        setattr(m, key, value)
    await session.commit()
    await session.refresh(m)
    return await _out(session, m)


@router.post("/{media_id}/review", response_model=MediaOut)
async def review_media(media_id: int, body: MediaReview, session: SessionDep) -> MediaOut:
    m = await _get(session, media_id)
    m.review_status = "approved" if body.decision == "approve" else "rejected"
    await session.commit()
    await session.refresh(m)
    return await _out(session, m)


@router.get("/{media_id}/file")
async def preview(media_id: int, session: SessionDep, media_dir: MediaDir) -> FileResponse:
    """Preview for the admin, whatever the review status."""
    m = await _get(session, media_id)
    path = file_path(m, media_dir)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The file is missing on disk")
    return FileResponse(path, media_type=m.mime or "application/octet-stream")


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(media_id: int, session: SessionDep, media_dir: MediaDir) -> None:
    m = await _get(session, media_id)
    path = file_path(m, media_dir)
    await session.delete(m)
    await session.commit()
    if path is not None:
        path.unlink(missing_ok=True)


@public_router.get("/media/{media_id}")
async def serve(media_id: int, session: SessionDep, media_dir: MediaDir) -> FileResponse:
    """Used by the robot screen. Pending and rejected files do not exist for the public."""
    m = await session.get(Media, media_id)
    path = file_path(m, media_dir) if m is not None and m.review_status == "approved" else None
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")
    return FileResponse(path, media_type=m.mime or "application/octet-stream")
