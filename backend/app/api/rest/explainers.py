"""Program explainers and interest matching: review API for the admin, and the public endpoint
the idle robot screen reads (sprint 2).

Admin (X-Admin-Token): list / read / edit segments / approve or reject explainers, and the
interest -> program mapping. Drafts are created by the `draft-explainers` command (one LLM call
each); nothing here approves by itself, and an explainer with a number or course name that is
not in the program's approved sources cannot be approved.
Public: GET /api/robot/screen returns approved content only.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_admin
from app.db.models import Explainer, ExplainerSegment, Pitch, Program
from app.services import interests
from app.services.explainers import (
    MAX_SEGMENTS,
    Issue,
    clean_scene,
    explainer_issues,
    load_segments,
    prepare_audio,
    resolve_scene,
    screen_content,
)
from app.services.providers import ProviderError
from app.services.speech import CachedTTS, get_tts

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
public_router = APIRouter(tags=["robot"])

TTSDep = Annotated[CachedTTS, Depends(get_tts)]


class SegmentPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=1200)
    scene: dict[str, Any] | None = None


class SegmentCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    scene: dict[str, Any] = Field(default_factory=lambda: {"type": "keypoints"})


class Review(BaseModel):
    decision: Literal["approve", "reject"]


class MappingCreate(BaseModel):
    interest: str
    program_id: int
    text_ar: str | None = Field(default=None, max_length=300)
    text_en: str | None = Field(default=None, max_length=300)


class MappingPatch(BaseModel):
    text_ar: str | None = Field(default=None, max_length=300)
    text_en: str | None = Field(default=None, max_length=300)


# ---------------- explainers ----------------
def _issue(i: Issue) -> dict[str, Any]:
    return {"segment": i.segment, "kind": i.kind, "detail": i.detail}


async def _get(session, explainer_id: int) -> Explainer:
    explainer = await session.get(Explainer, explainer_id)
    if explainer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Explainer not found")
    return explainer


def _audio_ready(tts: CachedTTS, seg: ExplainerSegment) -> bool:
    """The cached speech exists for the CURRENT voice, pace and text."""
    if not hasattr(tts, "cache_key"):
        return seg.audio_cache_key is not None
    return seg.audio_cache_key == tts.cache_key(seg.text) and tts.is_cached(seg.text)


async def _detail(session, tts: CachedTTS, explainer: Explainer, **extra: Any) -> dict[str, Any]:
    program = await session.get(Program, explainer.program_id)
    segments = await load_segments(session, explainer.id)
    issues = await explainer_issues(session, explainer)
    return {
        "id": explainer.id, "lang": explainer.lang, "status": explainer.status,
        "program": {"id": program.id, "slug": program.slug, "name_en": program.name_en,
                    "name_ar": program.name_ar},
        "created_at": explainer.created_at, "updated_at": explainer.updated_at,
        "issues": [_issue(i) for i in issues],
        "can_approve": not issues,
        "segments": [
            {
                "id": s.id, "position": i, "text": s.text, "scene": s.scene,
                # what the screen would receive (program name, QR address and media resolved)
                "preview_scene": await resolve_scene(session, program, explainer.lang, s.scene, i),
                "audio_ready": _audio_ready(tts, s),
                "words": len(s.text.split()),
            }
            for i, s in enumerate(segments, start=1)
        ],
        **extra,
    }  # fmt: skip


@router.get("/explainers")
async def list_explainers(session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Explainer, Program)
            .join(Program, Program.id == Explainer.program_id)
            .order_by(Program.id, Explainer.lang)
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(ExplainerSegment.explainer_id, func.count()).group_by(ExplainerSegment.explainer_id)
            )
        ).all()
    )
    items = []
    for explainer, program in rows:
        issues = await explainer_issues(session, explainer)
        items.append({
            "id": explainer.id, "lang": explainer.lang, "status": explainer.status,
            "program": {"id": program.id, "slug": program.slug, "name_en": program.name_en,
                        "name_ar": program.name_ar},
            "segments": counts.get(explainer.id, 0), "issues": len(issues),
            "updated_at": explainer.updated_at,
        })  # fmt: skip
    programs = (await session.scalars(select(Program).order_by(Program.id))).all()
    return {
        "items": items,
        "programs": [{"id": p.id, "slug": p.slug, "name_en": p.name_en, "name_ar": p.name_ar} for p in programs],
    }


@router.get("/explainers/{explainer_id}")
async def get_explainer(explainer_id: int, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    return await _detail(session, tts, await _get(session, explainer_id))


async def _touched(session, explainer: Explainer) -> None:
    """Edited content is not approved content: it goes back to the review queue."""
    explainer.updated_at = datetime.now(UTC)
    if explainer.status == "approved":
        explainer.status = "pending"
    await session.commit()


@router.patch("/explainers/segments/{segment_id}")
async def patch_segment(segment_id: int, body: SegmentPatch, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    seg = await session.get(ExplainerSegment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if body.text is not None and body.text.strip() != seg.text:
        seg.text = " ".join(body.text.split())
        seg.audio_cache_key = None  # the old speech no longer matches the text
    if body.scene is not None:
        seg.scene = clean_scene(body.scene)
    explainer = await _get(session, seg.explainer_id)
    await _touched(session, explainer)
    return await _detail(session, tts, explainer)


@router.post("/explainers/{explainer_id}/segments", status_code=status.HTTP_201_CREATED)
async def add_segment(explainer_id: int, body: SegmentCreate, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    explainer = await _get(session, explainer_id)
    segments = await load_segments(session, explainer.id)
    if len(segments) >= MAX_SEGMENTS:
        raise HTTPException(422, f"An explainer has at most {MAX_SEGMENTS} segments")
    session.add(ExplainerSegment(
        explainer_id=explainer.id, position=(segments[-1].position + 1) if segments else 1,
        text=" ".join(body.text.split()), scene=clean_scene(body.scene),
    ))  # fmt: skip
    await _touched(session, explainer)
    return await _detail(session, tts, explainer)


@router.delete("/explainers/segments/{segment_id}")
async def delete_segment(segment_id: int, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    seg = await session.get(ExplainerSegment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    explainer = await _get(session, seg.explainer_id)
    await session.delete(seg)
    await _touched(session, explainer)
    return await _detail(session, tts, explainer)


@router.post("/explainers/{explainer_id}/review")
async def review_explainer(explainer_id: int, body: Review, session: SessionDep, tts: TTSDep) -> dict[str, Any]:
    """approve: refused (409) while a number or course name is not in the program's approved
    sources; otherwise the explainer becomes playable and its speech is generated and cached.
    reject: the robot never plays it."""
    explainer = await _get(session, explainer_id)
    if body.decision == "reject":
        explainer.status = "rejected"
        explainer.updated_at = datetime.now(UTC)
        await session.commit()
        return await _detail(session, tts, explainer)
    issues = await explainer_issues(session, explainer)
    if issues:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Fix these before approving", "issues": [_issue(i) for i in issues]},
        )
    explainer.status = "approved"
    explainer.updated_at = datetime.now(UTC)
    await session.commit()
    audio_error = None
    try:
        await prepare_audio(session, tts, explainer)
    except ProviderError as e:  # approved anyway; the speech is generated at the first playback
        audio_error = str(e)
    return await _detail(session, tts, explainer, audio_error=audio_error)


# ---------------- interest -> program mapping ----------------
async def _mapping(session, pitch: Pitch) -> dict[str, Any]:
    program = await session.get(Program, pitch.program_id) if pitch.program_id else None
    key = pitch.topic.removeprefix(interests.TOPIC_PREFIX)
    info = interests.INTERESTS.get(key, {})
    return {
        "id": pitch.id, "interest": key, "interest_ar": info.get("ar"), "interest_en": info.get("en"),
        "program": {"id": program.id, "slug": program.slug, "name_en": program.name_en,
                    "name_ar": program.name_ar} if program else None,
        "text_ar": pitch.text_ar, "text_en": pitch.text_en, "status": pitch.review_status,
        "is_promotional": pitch.is_promotional,
        "issues": await interests.mapping_issues(session, pitch),
    }  # fmt: skip


async def _get_mapping(session, pitch_id: int) -> Pitch:
    pitch = await session.get(Pitch, pitch_id)
    if pitch is None or not pitch.topic.startswith(interests.TOPIC_PREFIX):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interest mapping not found")
    return pitch


@router.get("/interests")
async def list_interests(session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(Pitch).where(Pitch.topic.like(f"{interests.TOPIC_PREFIX}%")).order_by(Pitch.topic, Pitch.id)
        )
    ).all()
    return {
        "interests": [{"key": k, "ar": v["ar"], "en": v["en"]} for k, v in interests.INTERESTS.items()],
        "items": [await _mapping(session, p) for p in rows],
    }


@router.post("/interests/seed")
async def seed_interests(session: SessionDep) -> dict[str, Any]:
    """Pending rows from the default mapping, for programs whose explainer is approved."""
    return await interests.seed_from_explainers(session)


@router.post("/interests", status_code=status.HTTP_201_CREATED)
async def add_interest(body: MappingCreate, session: SessionDep) -> dict[str, Any]:
    if body.interest not in interests.INTERESTS:
        raise HTTPException(422, f"Unknown interest; choose one of {', '.join(interests.INTERESTS)}")
    if await session.get(Program, body.program_id) is None:
        raise HTTPException(422, "Unknown program_id")
    pitch = Pitch(
        program_id=body.program_id, topic=f"{interests.TOPIC_PREFIX}{body.interest}",
        text_ar=(body.text_ar or "").strip() or None, text_en=(body.text_en or "").strip() or None,
        is_promotional=True, review_status="pending",
    )  # fmt: skip
    session.add(pitch)
    await session.commit()
    return await _mapping(session, pitch)


@router.patch("/interests/{pitch_id}")
async def patch_interest(pitch_id: int, body: MappingPatch, session: SessionDep) -> dict[str, Any]:
    pitch = await _get_mapping(session, pitch_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(pitch, key, (value or "").strip() or None)
    if pitch.review_status == "approved":
        pitch.review_status = "pending"  # edited text is reviewed again
    await session.commit()
    return await _mapping(session, pitch)


@router.post("/interests/{pitch_id}/review")
async def review_interest(pitch_id: int, body: Review, session: SessionDep) -> dict[str, Any]:
    pitch = await _get_mapping(session, pitch_id)
    if body.decision == "approve":
        issues = await interests.mapping_issues(session, pitch)
        if issues:
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Fix these before approving", "issues": issues})
        pitch.review_status = "approved"
    else:
        pitch.review_status = "rejected"
    await session.commit()
    return await _mapping(session, pitch)


@router.delete("/interests/{pitch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interest(pitch_id: int, session: SessionDep) -> None:
    await session.delete(await _get_mapping(session, pitch_id))
    await session.commit()


# ---------------- public: what the idle screen may show ----------------
@public_router.get("/api/robot/screen")
async def robot_screen(session: SessionDep) -> dict[str, Any]:
    """Approved content only: programs with an approved explainer (touch buttons), their
    `title` and `stat` scenes (attract mode), and whether "help me choose" can be offered."""
    content = await screen_content(session)
    content["interests"] = await interests.has_approved_mapping(session)
    return content
