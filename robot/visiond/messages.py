"""Messages between visiond and robotd on /ws/vision (pure, no OpenCV import).

visiond -> robotd, about 10 Hz:
    {"type":"vision.state","t_ms":int,"camera_ok":bool,
     "faces":[{"id":int,"box":[x,y,w,h],"facing":bool,"yaw_deg":float,"pitch_deg":float,
               "mouth":float,"speaking":bool}],
     "selected_id":int|null,"engaged":bool,"active_speaker_id":int|null,"overlap":bool,
     "servo_target_deg":float}
robotd -> visiond:
    {"type":"vad","speaking":bool}      the page's voice activity
    {"type":"servo","pan_deg":float}    the angle actually commanded

Privacy: the message holds ids, boxes (fractions of the frame) and flags. There is no field
for pixels, landmarks or embeddings, and `build_vision_state` has no access to a frame.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from visiond.logic import LogicState

VISION_STATE = "vision.state"
STATE_KEYS = frozenset(
    {
        "type",
        "t_ms",
        "camera_ok",
        "faces",
        "selected_id",
        "engaged",
        "active_speaker_id",
        "overlap",
        "servo_target_deg",
    }
)
FACE_KEYS = frozenset({"id", "box", "facing", "yaw_deg", "pitch_deg", "mouth", "speaking"})


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _num(value: float, digits: int) -> float:
    return round(value, digits) if math.isfinite(value) else 0.0


def build_vision_state(
    state: LogicState, *, camera_ok: bool, servo_target_deg: float
) -> dict[str, Any]:
    """The `vision.state` message as a JSON-ready dict."""
    faces = [
        {
            "id": face.id,
            "box": [_unit(face.box.x), _unit(face.box.y), _unit(face.box.w), _unit(face.box.h)],
            "facing": bool(face.facing),
            "yaw_deg": _num(face.yaw_deg, 1),
            "pitch_deg": _num(face.pitch_deg, 1),
            "mouth": _num(face.mouth, 3),
            "speaking": bool(face.speaking),
        }
        for face in state.faces
    ]
    return {
        "type": VISION_STATE,
        "t_ms": int(state.t_ms),
        "camera_ok": bool(camera_ok),
        "faces": faces,
        "selected_id": state.selected_id,
        "engaged": bool(state.engaged),
        "active_speaker_id": state.active_speaker_id,
        "overlap": bool(state.overlap),
        "servo_target_deg": _num(servo_target_deg, 1),
    }


@dataclass(frozen=True)
class VadMessage:
    speaking: bool


@dataclass(frozen=True)
class ServoMessage:
    pan_deg: float


def parse_inbound(text: str) -> VadMessage | ServoMessage | None:
    """Parse a robotd -> visiond message; anything unknown or malformed gives None."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    if kind == "vad" and isinstance(data.get("speaking"), bool):
        return VadMessage(speaking=data["speaking"])
    if kind == "servo":
        pan = data.get("pan_deg")
        if isinstance(pan, (int, float)) and not isinstance(pan, bool) and math.isfinite(pan):
            return ServoMessage(pan_deg=float(pan))
    return None


def console_line(message: dict[str, Any], *, vad_label: str, servo_label: str, fps: float) -> str:
    """One human-readable status line for --console (no image data, same content as the WS)."""
    parts = []
    for face in message["faces"]:
        flags = ("F" if face["facing"] else "-") + ("M" if face["speaking"] else "-")
        cx = face["box"][0] + face["box"][2] / 2
        parts.append(
            f"#{face['id']}[{flags} x={cx:.2f} yaw={face['yaw_deg']:+.0f} "
            f"pitch={face['pitch_deg']:+.0f} mouth={face['mouth']:.2f}]"
        )
    faces = " ".join(parts) if parts else "-"
    return (
        f"cam={'ok' if message['camera_ok'] else 'DOWN'} fps={fps:4.1f} "
        f"faces={len(message['faces'])} {faces} | selected={message['selected_id']} "
        f"engaged={'YES' if message['engaged'] else 'no'} | {vad_label} "
        f"speaker={message['active_speaker_id']} overlap={'YES' if message['overlap'] else 'no'}"
        f" | {servo_label} target={message['servo_target_deg']:+.1f}"
    )
