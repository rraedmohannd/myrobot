"""Voice test page (/voice), robot screen page (/robot), the visitor access page (/access) and a
text-to-speech endpoint used for the voice listening test.

In production (APP_ENV=production) the pages and the TTS endpoints need the robot token or a
visitor session (services/access.py); in development they are open, as before.
"""

import html
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import PaidAccess, PaidCaller
from app.core.config import get_settings
from app.services import access
from app.services.cost_guard import PaidLimitError, SlidingWindowLimiter
from app.services.providers import ProviderError
from app.services.speech import VOICES, CachedTTS, audio_media_type, get_tts

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
VOICE_PAGE = STATIC_DIR / "voice.html"
ROBOT_PAGE = STATIC_DIR / "robot.html"
ACCESS_PAGE = STATIC_DIR / "access.html"
router = APIRouter()

# Wrong codes typed on /access: at most this many POSTs per IP in the window.
ACCESS_ATTEMPTS, ACCESS_WINDOW_S = 10, 600
_access_attempts = SlidingWindowLimiter()


def reset_access_attempts() -> None:
    _access_attempts.clear()


def _page(request: Request, path: Path) -> Response:
    """A paid page: open in development; in production the robot token or a visitor session,
    otherwise a redirect to /access (or 404 with PUBLIC_PAGES=false)."""
    settings = get_settings()
    if settings.is_production and access.paid_caller(request) is None:
        if not settings.public_pages:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/access?next={quote(target, safe='')}", status.HTTP_303_SEE_OTHER)
    return FileResponse(path, media_type="text/html")


@router.get("/voice", include_in_schema=False)
async def voice_page(request: Request) -> Response:
    return _page(request, VOICE_PAGE)


@router.get("/robot", include_in_schema=False)
async def robot_page(request: Request) -> Response:
    """The robot's screen in a browser: stands in for the 7" HDMI display (1024x600)."""
    return _page(request, ROBOT_PAGE)


# ---------------- /access: the visitor types SITE_ACCESS_CODE once ----------------
def _access_html(next_path: str, error: str | None = None, code: int = 200) -> HTMLResponse:
    page = ACCESS_PAGE.read_text(encoding="utf-8")
    page = page.replace("<!--NEXT-->", html.escape(next_path, quote=True))
    if error:
        page = page.replace("<!--ERROR-->", f'<p class="error">{html.escape(error)}</p>')
    response = HTMLResponse(page, status_code=code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _access_enabled() -> None:
    settings = get_settings()
    if not settings.is_production or not settings.public_pages:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


@router.get("/access", include_in_schema=False)
async def access_page(request: Request) -> Response:
    _access_enabled()
    next_path = access.safe_next(request.query_params.get("next"))
    if access.paid_caller(request) is not None:  # already signed in
        return RedirectResponse(next_path, status.HTTP_303_SEE_OTHER)
    return _access_html(next_path)


@router.post("/access", include_in_schema=False)
async def access_submit(request: Request) -> Response:
    _access_enabled()
    if not _access_attempts.allow(access.client_ip(request), ACCESS_ATTEMPTS, ACCESS_WINDOW_S):
        return _access_html(
            "/robot", "محاولات كثيرة. استنى شوي وجرب مرة ثانية. · Too many attempts, wait.", 429
        )
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 4096:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Too large")
    form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    next_path = access.safe_next((form.get("next") or [""])[0])
    if not access.access_code_ok((form.get("code") or [""])[0]):
        return _access_html(next_path, "الرمز غلط. · Wrong code.", 401)
    response = RedirectResponse(next_path, status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        access.VISITOR_COOKIE, access.make_visitor_cookie(),
        max_age=get_settings().visitor_session_hours * 3600, httponly=True, secure=True,
        samesite="strict", path="/",
    )  # fmt: skip
    return response


# ---------------- text-to-speech for the voice test ----------------
class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    voice: str | None = None


@router.get("/api/tts/voices", tags=["voice"])
async def voices(_caller: PaidCaller, tts: Annotated[CachedTTS, Depends(get_tts)]) -> dict:
    return {"voices": list(VOICES), "default": tts.voice, "model": tts.model}


@router.post("/api/tts", tags=["voice"], response_class=Response)
async def synthesize(
    body: TTSRequest, _caller: PaidAccess, tts: Annotated[CachedTTS, Depends(get_tts)]
) -> Response:
    if body.voice and body.voice not in VOICES:
        raise HTTPException(422, f"unknown voice; choose one of {', '.join(VOICES)}")
    try:
        mp3 = await tts.synthesize(body.text, voice=body.voice, cache=True)
    except PaidLimitError as e:
        raise HTTPException(429, "Daily limit of AI calls reached. Try tomorrow.") from e
    except ProviderError as e:
        raise HTTPException(503, str(e)) from e
    return Response(mp3, media_type=audio_media_type(mp3))  # WAV with openai_realtime
