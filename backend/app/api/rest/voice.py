"""Voice test page (/voice), robot screen page (/robot) and a text-to-speech endpoint used for
the voice listening test."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.services.providers import ProviderError
from app.services.speech import VOICES, CachedTTS, get_tts

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
VOICE_PAGE = STATIC_DIR / "voice.html"
ROBOT_PAGE = STATIC_DIR / "robot.html"
router = APIRouter()


@router.get("/voice", include_in_schema=False)
async def voice_page() -> FileResponse:
    return FileResponse(VOICE_PAGE, media_type="text/html")


@router.get("/robot", include_in_schema=False)
async def robot_page() -> FileResponse:
    """The robot's screen in a browser: stands in for the 7" HDMI display (1024x600)."""
    return FileResponse(ROBOT_PAGE, media_type="text/html")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    voice: str | None = None


@router.get("/api/tts/voices", tags=["voice"])
async def voices(tts: Annotated[CachedTTS, Depends(get_tts)]) -> dict:
    return {"voices": list(VOICES), "default": tts.voice, "model": tts.model}


@router.post("/api/tts", tags=["voice"], response_class=Response)
async def synthesize(body: TTSRequest, tts: Annotated[CachedTTS, Depends(get_tts)]) -> Response:
    if body.voice and body.voice not in VOICES:
        raise HTTPException(422, f"unknown voice; choose one of {', '.join(VOICES)}")
    try:
        mp3 = await tts.synthesize(body.text, voice=body.voice, cache=True)
    except ProviderError as e:
        raise HTTPException(503, str(e)) from e
    return Response(mp3, media_type="audio/mpeg")
