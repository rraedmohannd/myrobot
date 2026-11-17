"""SIMULATED vision client — sends scripted `vision.state` messages per the contract; not a camera.

    cd robot
    .venv\\Scripts\\python scripts\\fake_vision.py [--url ws://127.0.0.1:8765/ws/vision] [--hold 20]

A labelled stand-in for `visiond` when no face (phone camera, webcam) is available. It plays one
scripted visitor on robotd's `/ws/vision`, at 10 Hz, with the message shape of
`visiond/messages.py`:

    appear   1.0 s   one face in view, not facing the robot            engaged = false
    facing   1.5 s   the face looks at the robot                       engaged = false
    engaged  --hold  facing long enough: engaged = true; the face then moves sideways, so
                     `servo_target_deg` changes (0 -> +18 -> -12 degrees)
    leave    2.0 s   no face                                           engaged = false

Nothing here detects anything: no camera is opened and no image exists. It proves only that
robotd reacts to the messages (stop, ENGAGED, SERVO commands). It says NOTHING about visiond's
detection on real faces. Every line it prints starts with "SIMULATED vision".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import time
from collections.abc import Callable
from typing import Any

import aiohttp

LABEL = "SIMULATED vision"
PERIOD_S = 0.1
APPEAR_S = 1.0
FACING_S = 1.5  # the engagement time of shared/vision_thresholds.json (browser prototype value)
LEAVE_S = 2.0
FACE_ID = 1


def scripted_state(t_s: float, hold_s: float) -> tuple[str, dict[str, Any]]:
    """(phase, vision.state) at `t_s` seconds after the start of the script. Pure."""
    engaged_from = APPEAR_S + FACING_S
    leave_from = engaged_from + hold_s
    if t_s >= leave_from:
        phase = "leave" if t_s < leave_from + LEAVE_S else "done"
        faces: list[dict[str, Any]] = []
        engaged, target, selected = False, 0.0, None
    else:
        if t_s < APPEAR_S:
            phase, facing, engaged, cx = "appear", False, False, 0.5
        elif t_s < engaged_from:
            phase, facing, engaged, cx = "facing", True, False, 0.5
        else:
            phase, facing, engaged = "engaged", True, True
            # sideways: to the right during the first 3 s, then slowly to the left
            u = t_s - engaged_from
            cx = 0.5 + 0.3 * min(1.0, u / 3.0) if u < 6.0 else 0.8 - 0.5 * min(1.0, (u - 6.0) / 4.0)
        target = round((cx - 0.5) * 60.0, 1)  # placeholder 60 degree field of view
        faces = [
            {
                "id": FACE_ID,
                "box": [round(cx - 0.1, 4), 0.3, 0.2, 0.3],
                "facing": facing,
                "yaw_deg": 0.0 if facing else 35.0,
                "pitch_deg": 0.0,
                "mouth": round(0.05 + 0.03 * math.sin(t_s * 9.0), 3),
                "speaking": False,
            }
        ]
        selected = FACE_ID
    return phase, {
        "type": "vision.state",
        "t_ms": int(t_s * 1000),
        "camera_ok": True,
        "faces": faces,
        "selected_id": selected,
        "engaged": engaged,
        "active_speaker_id": None,
        "overlap": False,
        "servo_target_deg": target,
    }


async def play(
    url: str,
    hold_s: float,
    on_phase: Callable[[str], None] | None = None,
    stop: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Play the script once. Returns what robotd sent back: servo angles and vad messages.

    `stop` (optional): when set during the `engaged` phase, the visitor leaves at once.
    """
    servo: list[float] = []
    vad: list[bool] = []
    sent = 0
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=3)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.ws_connect(url) as ws:

        async def reader() -> None:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    msg = json.loads(raw.data)
                    if msg.get("type") == "servo":
                        servo.append(float(msg["pan_deg"]))
                    elif msg.get("type") == "vad":
                        vad.append(bool(msg["speaking"]))

        task = asyncio.create_task(reader())
        t0 = time.monotonic()
        last = ""
        try:
            while True:
                t = time.monotonic() - t0
                if stop is not None and stop.is_set() and t < APPEAR_S + FACING_S + hold_s:
                    hold_s = max(0.0, t - APPEAR_S - FACING_S)  # leave now
                phase, state = scripted_state(t, hold_s)
                if phase != last:
                    last = phase
                    if on_phase is not None:
                        on_phase(phase)
                if phase == "done":
                    break
                await ws.send_str(json.dumps(state, separators=(",", ":")))
                sent += 1
                await asyncio.sleep(PERIOD_S)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    return {"sent": sent, "servo": servo, "vad": vad}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SIMULATED vision client: scripted vision.state messages, not a camera"
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws/vision")
    parser.add_argument(
        "--hold", type=float, default=20.0, help="seconds the visitor stays engaged"
    )
    args = parser.parse_args()
    print(f"{LABEL}: scripted visitor -> {args.url} (no camera, no image, not visiond)", flush=True)
    t0 = time.monotonic()
    try:
        result = asyncio.run(
            play(
                args.url,
                args.hold,
                lambda p: print(f"{LABEL}: t={time.monotonic() - t0:5.1f}s phase {p}", flush=True),
            )
        )
    except (aiohttp.ClientError, OSError) as exc:
        print(f"{LABEL}: cannot reach robotd at {args.url}: {exc}")
        return 2
    angles = sorted(set(result["servo"]))
    print(
        f"{LABEL}: sent {result['sent']} vision.state; robotd answered {len(result['servo'])} "
        f"servo messages (min {angles[0] if angles else '-'}, max {angles[-1] if angles else '-'}) "
        f"and {len(result['vad'])} vad messages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
