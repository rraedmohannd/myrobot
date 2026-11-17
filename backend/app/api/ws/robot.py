"""WebSocket /ws/robot — the robot (Raspberry Pi, or the browser pages that stand in for it).

Every message is JSON with `type`; outgoing messages also carry `id` and `ts` (ms). The contract
is the `scr_protocol` package in `shared/` (docs/PROTOCOL.md is generated from it).

Two lanes per connection:
- conversation, telemetry and `calibration.report` messages are handled one at a time, in order,
  by a worker task;
- `heartbeat`, `robot.status` and `robot.command.ack` are answered at once by the reader, so a
  robot stays visible (and its acks arrive) while the backend is busy preparing an answer.
"""

import asyncio
import contextlib
import ipaddress
import logging
import time
import uuid
from secrets import compare_digest
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from scr_protocol import ProtocolError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.ws.contract import checked
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.services.bringup import handle_calibration_report
from app.services.dialogue import DialogueManager
from app.services.providers import LLM, Embedder, get_embedder, get_llm
from app.services.robot_hub import RobotHub, get_robot_hub
from app.services.speakers import SpeakerEmbedder, get_speaker_embedder
from app.services.speech import STT, CachedTTS, get_stt, get_tts
from app.telemetry.store import TelemetryRecorder

router = APIRouter()
log = logging.getLogger(__name__)


def is_local_peer(host: str | None) -> bool:
    """True when the connection comes from this machine or from a network listed in
    ROBOT_LOCAL_NETS. Only the peer address counts: headers such as Host can be forged."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for net in get_settings().robot_local_net_list:
        if address in net:
            return True
    return False


def token_ok(ws: WebSocket, token: str | None) -> bool:
    """ROBOT_TOKEN, when set, is required for every connection that is not local."""
    expected = get_settings().robot_token
    if expected is None or not expected.get_secret_value():
        return True
    if token and compare_digest(token, expected.get_secret_value()):
        return True
    return is_local_peer(ws.client.host if ws.client else None)


@router.websocket("/ws/robot")
async def robot_socket(
    ws: WebSocket,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
    stt: Annotated[STT, Depends(get_stt)],
    tts: Annotated[CachedTTS, Depends(get_tts)],
    speaker_embedder: Annotated[SpeakerEmbedder, Depends(get_speaker_embedder)],
    hub: Annotated[RobotHub, Depends(get_robot_hub)],
    token: str | None = None,
) -> None:
    if not token_ok(ws, token):
        await ws.close(code=4401)
        return
    await ws.accept()
    send_lock = asyncio.Lock()  # the reader and the worker both send

    async def raw_send(msg: dict[str, Any]) -> None:
        out = {**msg, "ts": int(time.time() * 1000)}
        out.setdefault("id", uuid.uuid4().hex[:12])  # robot.command keeps its command id
        async with send_lock:
            await ws.send_json(out)

    send = checked(raw_send)
    manager = DialogueManager(
        send, sessionmaker, embedder, llm, stt, tts, speaker_embedder=speaker_embedder
    )
    recorder = TelemetryRecorder(send, sessionmaker)  # run.start / telemetry / event / run.end
    link = hub.register(send)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def worker() -> None:
        while True:
            msg = await queue.get()
            try:
                if msg.get("type") in TelemetryRecorder.TYPES:
                    await recorder.handle(msg)
                    link.run_id = recorder.run_id
                elif msg.get("type") == "calibration.report":
                    try:
                        await handle_calibration_report(sessionmaker, msg, link.robot_id)
                    except ProtocolError as e:
                        await send({"type": "error", "code": "bad_message", "message": str(e)})
                else:
                    # Conversations during a run link to it: this connection's own run, or
                    # else the run open on the live robot link (the screen page and `robotd`
                    # are two connections). Read when the session row is created.
                    own = recorder.run_id
                    manager.robot_run_id = own if own is not None else hub.open_run_id()
                    await manager.handle(msg)
            except (WebSocketDisconnect, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001 - keep the connection alive, report the problem
                log.exception("error while handling %s", msg.get("type"))
                with contextlib.suppress(Exception):
                    await send(
                        {"type": "error", "code": "internal", "message": f"{type(e).__name__}: {e}"}
                    )

    work = asyncio.create_task(worker())
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                await send(
                    {"type": "error", "code": "bad_message", "message": "expected a JSON object"}
                )
                continue
            kind = msg.get("type")
            if kind == "heartbeat":
                await send({"type": "heartbeat"})
            elif kind in ("robot.status", "robot.command.ack"):
                try:
                    (hub.on_status if kind == "robot.status" else hub.on_ack)(link, msg)
                except ProtocolError as e:
                    await send({"type": "error", "code": "bad_message", "message": str(e)})
            else:
                if kind == "hello":
                    link.hello(msg)
                queue.put_nowait(msg)
    except WebSocketDisconnect:
        pass
    finally:
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await work
        hub.unregister(link)
        await manager.disconnected()
        await recorder.disconnected()
