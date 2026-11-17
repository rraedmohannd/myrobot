"""Review API for curated answers (sprint 3 S3 and S4): FAQs, pitches, places and labs.

Everything is created or drafted as `pending`; the robot only uses rows a human approved here.
Editing an approved row sends it back to `pending`. Approval is refused (409) while an FAQ or a
pitch contains a number that is not in the approved sources, a promise, or (pitches) a claim
about other faculties or universities.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.api.deps import SessionDep, require_admin
from app.db.models import Chunk, Faq, Lab, Media, Pitch, Place, Program, Source
from app.knowledge.store import usable_chunk
from app.services import curated as cur
from app.services.chat import speech_pieces
from app.services.providers import ProviderError
from app.services.speech import CachedTTS, get_tts

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
TTSDep = Annotated[CachedTTS, Depends(get_tts)]


class Review(BaseModel):
    decision: Literal["approve", "reject"]


def _text(value: str | None) -> str | None:
    return " ".join((value or "").split()) or None


async def _program(session, program_id: int | None) -> dict[str, Any] | None:
    program = await session.get(Program, program_id) if program_id else None
    return {"id": program.id, "slug": program.slug, "name_en": program.name_en,
            "name_ar": program.name_ar} if program else None  # fmt: skip


async def _check_program(session, program_id: int | None) -> None:
    if program_id is not None and await session.get(Program, program_id) is None:
        raise HTTPException(422, "Unknown program_id")


async def prepare_speech(tts: CachedTTS, *texts: str | None) -> str | None:
    """Generate the speech of approved texts ahead of time (same pieces as when spoken).
    Returns an error text when text-to-speech is unreachable; the approval stands anyway."""
    try:
        for body in texts:
            for piece in speech_pieces(body or ""):
                await tts.synthesize(piece, cache=True)
    except ProviderError as e:
        return str(e)
    return None


def _refuse(issues: list[str]) -> None:
    if issues:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"message": "Fix these before approving", "issues": issues}
        )


# ====================================================================================
# FAQs
# ====================================================================================
class FaqIn(BaseModel):
    program_id: int | None = None
    question_ar: str | None = Field(default=None, max_length=400)
    question_en: str | None = Field(default=None, max_length=400)
    answer_ar: str | None = Field(default=None, max_length=800)
    answer_en: str | None = Field(default=None, max_length=800)
    priority: int = 0


class FaqPatch(BaseModel):
    program_id: int | None = None
    question_ar: str | None = Field(default=None, max_length=400)
    question_en: str | None = Field(default=None, max_length=400)
    answer_ar: str | None = Field(default=None, max_length=800)
    answer_en: str | None = Field(default=None, max_length=800)
    priority: int | None = None


async def _faq(session, faq: Faq, **extra: Any) -> dict[str, Any]:
    source = await session.get(Source, faq.source_id) if faq.source_id else None
    return {
        "id": faq.id, "program": await _program(session, faq.program_id),
        "question_ar": faq.question_ar, "question_en": faq.question_en,
        "answer_ar": faq.answer_ar, "answer_en": faq.answer_en, "priority": faq.priority,
        "status": faq.review_status, "source_title": source.title if source else None,
        "issues": await cur.faq_issues(session, faq), **extra,
    }  # fmt: skip


async def _get_faq(session, faq_id: int) -> Faq:
    faq = await session.get(Faq, faq_id)
    if faq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found")
    return faq


@router.get("/faqs")
async def list_faqs(session: SessionDep) -> dict[str, Any]:
    rows = (await session.scalars(select(Faq).order_by(Faq.program_id.nulls_first(), Faq.id))).all()
    programs = (await session.scalars(select(Program).order_by(Program.id))).all()
    return {
        "items": [await _faq(session, f) for f in rows],
        "programs": [
            {"id": p.id, "slug": p.slug, "name_en": p.name_en, "name_ar": p.name_ar}
            for p in programs
        ],
        "match_threshold": cur.get_settings().faq_match_threshold,
    }


@router.post("/faqs", status_code=status.HTTP_201_CREATED)
async def add_faq(body: FaqIn, session: SessionDep) -> dict[str, Any]:
    await _check_program(session, body.program_id)
    faq = Faq(
        program_id=body.program_id,
        question_ar=_text(body.question_ar),
        question_en=_text(body.question_en),
        answer_ar=_text(body.answer_ar),
        answer_en=_text(body.answer_en),
        priority=body.priority,
        review_status="pending",
    )
    session.add(faq)
    await session.commit()
    return await _faq(session, faq)


@router.patch("/faqs/{faq_id}")
async def patch_faq(faq_id: int, body: FaqPatch, session: SessionDep) -> dict[str, Any]:
    faq = await _get_faq(session, faq_id)
    changes = body.model_dump(exclude_unset=True)
    if "program_id" in changes:
        await _check_program(session, changes["program_id"])
    for key, value in changes.items():
        setattr(
            faq,
            key,
            _text(value)
            if isinstance(value, str) or (value is None and key != "program_id")
            else value,
        )
    if faq.priority is None:
        faq.priority = 0
    if faq.review_status == "approved":
        faq.review_status = "pending"  # edited content is reviewed again
    await session.commit()
    if {"question_ar", "question_en"} & set(
        changes
    ):  # the old vectors no longer match the questions
        await session.execute(
            text(
                "UPDATE faqs SET embedding_ar = NULL, embedding_en = NULL, embedding_model = NULL "
                "WHERE id = :id"
            ),
            {"id": faq.id},
        )
        await session.commit()
    return await _faq(session, faq)


@router.post("/faqs/{faq_id}/review")
async def review_faq(faq_id: int, body: Review, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    faq = await _get_faq(session, faq_id)
    if body.decision == "reject":
        faq.review_status = "rejected"
        await session.commit()
        return await _faq(session, faq)
    _refuse(await cur.faq_issues(session, faq))
    faq.review_status = "approved"
    await session.commit()
    return await _faq(
        session, faq, audio_error=await prepare_speech(tts, faq.answer_ar, faq.answer_en)
    )


@router.delete("/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(faq_id: int, session: SessionDep) -> None:
    await session.delete(await _get_faq(session, faq_id))
    await session.commit()


# ====================================================================================
# Pitches (why / compare)
# ====================================================================================
class PitchIn(BaseModel):
    program_id: int
    kind: Literal["why", "compare"]
    text_ar: str | None = Field(default=None, max_length=800)
    text_en: str | None = Field(default=None, max_length=800)


class PitchPatch(BaseModel):
    text_ar: str | None = Field(default=None, max_length=800)
    text_en: str | None = Field(default=None, max_length=800)


async def _pitch(session, pitch: Pitch, **extra: Any) -> dict[str, Any]:
    return {
        "id": pitch.id,
        "kind": pitch.topic,
        "program": await _program(session, pitch.program_id),
        "text_ar": pitch.text_ar,
        "text_en": pitch.text_en,
        "status": pitch.review_status,
        "is_promotional": pitch.is_promotional,
        "issues": await cur.pitch_issues(session, pitch),
        **extra,
    }


async def _get_pitch(session, pitch_id: int) -> Pitch:
    pitch = await session.get(Pitch, pitch_id)
    if pitch is None or pitch.topic not in cur.PITCH_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pitch not found")
    return pitch


@router.get("/pitches")
async def list_pitches(session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(Pitch)
            .where(Pitch.topic.in_(cur.PITCH_KINDS))
            .order_by(Pitch.program_id, Pitch.topic, Pitch.id)
        )
    ).all()
    return {"items": [await _pitch(session, p) for p in rows]}


@router.post("/pitches", status_code=status.HTTP_201_CREATED)
async def add_pitch(body: PitchIn, session: SessionDep) -> dict[str, Any]:
    await _check_program(session, body.program_id)
    pitch = Pitch(
        program_id=body.program_id,
        topic=body.kind,
        text_ar=_text(body.text_ar),
        text_en=_text(body.text_en),
        is_promotional=True,
        review_status="pending",
    )
    session.add(pitch)
    await session.commit()
    return await _pitch(session, pitch)


@router.patch("/pitches/{pitch_id}")
async def patch_pitch(pitch_id: int, body: PitchPatch, session: SessionDep) -> dict[str, Any]:
    pitch = await _get_pitch(session, pitch_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(pitch, key, _text(value))
    if pitch.review_status == "approved":
        pitch.review_status = "pending"
    await session.commit()
    return await _pitch(session, pitch)


@router.post("/pitches/{pitch_id}/review")
async def review_pitch(
    pitch_id: int, body: Review, session: SessionDep, tts: TTSDep
) -> dict[str, Any]:
    pitch = await _get_pitch(session, pitch_id)
    if body.decision == "reject":
        pitch.review_status = "rejected"
        await session.commit()
        return await _pitch(session, pitch)
    _refuse(await cur.pitch_issues(session, pitch))
    pitch.review_status = "approved"
    await session.commit()
    speech = (
        (pitch.text_ar, pitch.text_en) if pitch.topic == "compare" else ()
    )  # `why` goes through the model
    return await _pitch(session, pitch, audio_error=await prepare_speech(tts, *speech))


@router.delete("/pitches/{pitch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pitch(pitch_id: int, session: SessionDep) -> None:
    await session.delete(await _get_pitch(session, pitch_id))
    await session.commit()


# ====================================================================================
# Places and labs (typed in by the student; never invented)
# ====================================================================================
class PlaceIn(BaseModel):
    name_ar: str = Field(min_length=2, max_length=200)
    name_en: str = Field(min_length=2, max_length=200)
    building: str | None = Field(default=None, max_length=200)
    floor: str | None = Field(default=None, max_length=32)
    directions_ar: str | None = Field(default=None, max_length=600)
    directions_en: str | None = Field(default=None, max_length=600)
    related_office: str | None = Field(default=None, max_length=200)
    keywords: str | None = Field(default=None, max_length=400)
    contact_chunk_id: int | None = None
    media_id: int | None = None


class PlacePatch(PlaceIn):
    name_ar: str | None = Field(default=None, min_length=2, max_length=200)  # type: ignore[assignment]
    name_en: str | None = Field(default=None, min_length=2, max_length=200)  # type: ignore[assignment]


class LabIn(BaseModel):
    name_ar: str = Field(min_length=2, max_length=200)
    name_en: str = Field(min_length=2, max_length=200)
    program_id: int | None = None
    description_ar: str | None = Field(default=None, max_length=800)
    description_en: str | None = Field(default=None, max_length=800)
    location_hint: str | None = Field(default=None, max_length=400)
    keywords: str | None = Field(default=None, max_length=400)
    media_id: int | None = None


class LabPatch(LabIn):
    name_ar: str | None = Field(default=None, min_length=2, max_length=200)  # type: ignore[assignment]
    name_en: str | None = Field(default=None, min_length=2, max_length=200)  # type: ignore[assignment]


async def _check_links(session, data: dict[str, Any]) -> None:
    if data.get("media_id") is not None and await session.get(Media, data["media_id"]) is None:
        raise HTTPException(422, "Unknown media_id")
    if data.get("contact_chunk_id") is not None:
        chunk = await session.scalar(
            select(Chunk).where(Chunk.id == data["contact_chunk_id"], usable_chunk())
        )
        if chunk is None:
            raise HTTPException(422, "contact_chunk_id must be an approved passage")
    await _check_program(session, data.get("program_id"))


def _place(p: Place) -> dict[str, Any]:
    issues = (
        []
        if (p.directions_ar or p.directions_en or p.building or p.floor)
        else ["no directions, building or floor yet"]
    )
    return {
        "id": p.id,
        "name_ar": p.name_ar,
        "name_en": p.name_en,
        "building": p.building,
        "floor": p.floor,
        "directions_ar": p.directions_ar,
        "directions_en": p.directions_en,
        "related_office": p.related_office,
        "keywords": p.keywords,
        "contact_chunk_id": p.contact_chunk_id,
        "media_id": p.media_id,
        "status": p.review_status,
        "issues": issues,
    }


async def _lab(session, lab: Lab) -> dict[str, Any]:
    return {
        "id": lab.id,
        "name_ar": lab.name_ar,
        "name_en": lab.name_en,
        "program": await _program(session, lab.program_id),
        "description_ar": lab.description_ar,
        "description_en": lab.description_en,
        "location_hint": lab.location_hint,
        "keywords": lab.keywords,
        "media_id": lab.media_id,
        "status": lab.review_status,
        "issues": []
        if lab.location_hint
        else ["no location yet: the robot cannot answer 'where' for this lab"],
    }


@router.get("/places")
async def list_places(session: SessionDep) -> dict[str, Any]:
    """Places, labs and what the forms need: programs, approved media, and the approved
    passages that hold contact numbers (to link, not to retype)."""
    places = (await session.scalars(select(Place).order_by(Place.id))).all()
    labs = (await session.scalars(select(Lab).order_by(Lab.id))).all()
    programs = (await session.scalars(select(Program).order_by(Program.id))).all()
    media = (
        await session.scalars(
            select(Media).where(Media.review_status == "approved").order_by(Media.id)
        )
    ).all()
    contacts = (
        await session.execute(
            select(Chunk.id, Chunk.text, Source.title)
            .join(Source, Source.id == Chunk.source_id)
            .where(usable_chunk(), Chunk.category == "contacts")
            .order_by(Chunk.id)
        )
    ).all()
    return {
        "places": [_place(p) for p in places],
        "labs": [await _lab(session, lab) for lab in labs],
        "programs": [{"id": p.id, "name_en": p.name_en, "name_ar": p.name_ar} for p in programs],
        "media": [{"id": m.id, "title": m.title, "kind": m.kind} for m in media],
        "contact_passages": [
            {"id": c.id, "source": c.title, "text": c.text[:400]} for c in contacts
        ],
    }


@router.post("/places", status_code=status.HTTP_201_CREATED)
async def add_place(body: PlaceIn, session: SessionDep) -> dict[str, Any]:
    data = {k: (_text(v) if isinstance(v, str) else v) for k, v in body.model_dump().items()}
    await _check_links(session, data)
    place = Place(**data, review_status="pending")
    session.add(place)
    await session.commit()
    return _place(place)


async def _get(session, model: type, row_id: int) -> Any:
    row = await session.get(model, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return row


@router.patch("/places/{place_id}")
async def patch_place(place_id: int, body: PlacePatch, session: SessionDep) -> dict[str, Any]:
    place = await _get(session, Place, place_id)
    data = {
        k: (_text(v) if isinstance(v, str) else v)
        for k, v in body.model_dump(exclude_unset=True).items()
    }
    await _check_links(session, data)
    for key, value in data.items():
        if key in ("name_ar", "name_en") and not value:
            raise HTTPException(422, f"{key} cannot be empty")
        setattr(place, key, value)
    place.updated_at = datetime.now(UTC)
    if place.review_status == "approved":
        place.review_status = "pending"
    await session.commit()
    return _place(place)


@router.post("/places/{place_id}/review")
async def review_place(place_id: int, body: Review, session: SessionDep) -> dict[str, Any]:
    place = await _get(session, Place, place_id)
    if body.decision == "approve":
        _refuse(_place(place)["issues"])
    place.review_status = "approved" if body.decision == "approve" else "rejected"
    await session.commit()
    return _place(place)


@router.delete("/places/{place_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_place(place_id: int, session: SessionDep) -> None:
    await session.delete(await _get(session, Place, place_id))
    await session.commit()


@router.post("/labs", status_code=status.HTTP_201_CREATED)
async def add_lab(body: LabIn, session: SessionDep) -> dict[str, Any]:
    data = {k: (_text(v) if isinstance(v, str) else v) for k, v in body.model_dump().items()}
    await _check_links(session, data)
    lab = Lab(**data, review_status="pending")
    session.add(lab)
    await session.commit()
    return await _lab(session, lab)


@router.patch("/labs/{lab_id}")
async def patch_lab(lab_id: int, body: LabPatch, session: SessionDep) -> dict[str, Any]:
    lab = await _get(session, Lab, lab_id)
    data = {
        k: (_text(v) if isinstance(v, str) else v)
        for k, v in body.model_dump(exclude_unset=True).items()
    }
    await _check_links(session, data)
    for key, value in data.items():
        if key in ("name_ar", "name_en") and not value:
            raise HTTPException(422, f"{key} cannot be empty")
        setattr(lab, key, value)
    if lab.review_status == "approved":
        lab.review_status = "pending"
    await session.commit()
    return await _lab(session, lab)


@router.post("/labs/{lab_id}/review")
async def review_lab(lab_id: int, body: Review, session: SessionDep) -> dict[str, Any]:
    lab = await _get(session, Lab, lab_id)
    lab.review_status = "approved" if body.decision == "approve" else "rejected"
    await session.commit()
    return await _lab(session, lab)


@router.delete("/labs/{lab_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lab(lab_id: int, session: SessionDep) -> None:
    await session.delete(await _get(session, Lab, lab_id))
    await session.commit()
