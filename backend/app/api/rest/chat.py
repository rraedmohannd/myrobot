"""POST /api/chat — text question in, grounded answer + sources out (used by the robot and
tests)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import PaidAccess, SessionDep
from app.services.chat import answer_question
from app.services.providers import LLM, Embedder, get_embedder, get_llm

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: int | None = None


class SourceOut(BaseModel):
    n: int
    chunk_id: int
    title: str | None
    url: str
    category: str | None
    program_id: int | None = None


class ChatResponse(BaseModel):
    session_id: int | None
    answer: str
    lang: str
    found: bool
    sources: list[SourceOut]
    model: str | None
    timings: dict[str, int]
    invented_numbers: list[str]
    error: str | None
    kind: str = "rag"
    promotional: bool = False


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    session: SessionDep,
    _caller: PaidAccess,  # production: robot token or visitor session, per-IP rate limit
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> ChatResponse:
    result = await answer_question(session, embedder, llm, body.message.strip(), body.session_id)
    if result.limited:  # the daily cap of paid calls (services/cost_guard.py)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Daily limit of AI calls reached. Try tomorrow."
        )
    return ChatResponse(
        session_id=result.session_id,
        answer=result.answer,
        lang=result.lang,
        found=result.found,
        sources=[SourceOut(**vars(s)) for s in result.sources],
        model=result.model,
        timings=result.timings,
        invented_numbers=result.invented_numbers,
        error=result.error,
        kind=result.kind,
        promotional=result.promotional,
    )
