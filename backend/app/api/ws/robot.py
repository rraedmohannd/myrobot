"""WebSocket /ws/robot — the robot (Raspberry Pi, or the /voice browser page) talks here.

Every message is JSON with `type`; outgoing messages also carry `id` and `ts` (ms).
See docs/PROTOCOL.md. Messages are handled in order; one conversation per connection.
"""

import logging
import time
import uuid
from secrets import compare_digest
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.services.dialogue import DialogueManager
from app.services.providers import LLM, Embedder, get_embedder, get_llm
from app.services.speakers import SpeakerEmbedder, get_speaker_embedder
from app.services.speech import STT, CachedTTS, get_stt, get_tts
from app.telemetry.store import TelemetryRecorder

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/robot")
async def robot_socket(
    ws: WebSocket,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    stt: Annotated[STT, Depends(get_stt)],
    tts: Annotated[CachedTTS, Depends(get_tts)],
    speaker_embedder: Annotated[SpeakerEmbedder, Depends(get_speaker_embedder)],
    token: str | None = None,
) -> None:
    expected = get_settings().robot_token
    if expected is not None and expected.get_secret_value():
        if not token or not compare_digest(token, expected.get_secret_value()):
            await ws.close(code=4401)
            return
    await ws.accept()

    async def send(msg: dict[str, Any]) -> None:
        await ws.send_json({**msg, "id": uuid.uuid4().hex[:12], "ts": int(time.time() * 1000)})

    manager = DialogueManager(send, sessionmaker, embedder, llm, stt, tts, speaker_embedder=speaker_embedder)
    recorder = TelemetryRecorder(send, sessionmaker)  # run.start / telemetry / event / run.end
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                await send({"type": "error", "code": "bad_message", "message": "expected a JSON object"})
                continue
            try:
                if msg.get("type") in TelemetryRecorder.TYPES:
                    await recorder.handle(msg)
                    manager.robot_run_id = recorder.run_id  # conversations during a run link to it
                else:
                    await manager.handle(msg)
            except WebSocketDisconnect:
                raise
            except Exception as e:  # noqa: BLE001 - keep the connection alive, report the problem
                log.exception("error while handling %s", msg.get("type"))
                await send({"type": "error", "code": "internal", "message": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        await manager.disconnected()
        await recorder.disconnected()
