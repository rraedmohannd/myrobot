"""Shape of `vision.state` (ids, boxes, flags: no image data) and the inbound messages."""

from __future__ import annotations

import json
from typing import Any

from visiond_synth import FRAME_MS, LEFT_X, RIGHT_X, face, talking

from visiond.config import Thresholds
from visiond.logic import EMPTY_STATE, VisionLogic
from visiond.messages import (
    FACE_KEYS,
    STATE_KEYS,
    ServoMessage,
    VadMessage,
    build_vision_state,
    console_line,
    parse_inbound,
)


def busy_message() -> dict[str, Any]:
    logic = VisionLogic(Thresholds())
    state = None
    for i in range(60):
        faces = [face(LEFT_X, talking(i)), face(RIGHT_X)]
        state = logic.update(faces, i * FRAME_MS, vad_speaking=i > 30)
    assert state is not None
    return build_vision_state(state, camera_ok=True, servo_target_deg=-12.3456)


def test_exact_keys_and_types() -> None:
    message = busy_message()
    assert set(message) == STATE_KEYS
    assert message["type"] == "vision.state"
    assert type(message["t_ms"]) is int
    assert type(message["camera_ok"]) is bool
    assert type(message["engaged"]) is bool and type(message["overlap"]) is bool
    assert message["selected_id"] == 1 and message["active_speaker_id"] == 1
    assert message["engaged"] is True
    assert message["servo_target_deg"] == -12.3
    assert len(message["faces"]) == 2
    for item in message["faces"]:
        assert set(item) == FACE_KEYS
        assert type(item["id"]) is int
        assert len(item["box"]) == 4 and all(0.0 <= v <= 1.0 for v in item["box"])
        assert type(item["facing"]) is bool and type(item["speaking"]) is bool
        for key in ("yaw_deg", "pitch_deg", "mouth"):
            assert type(item[key]) is float
    assert [f["speaking"] for f in message["faces"]] == [True, False]


def test_no_image_data_can_be_in_the_message() -> None:
    """Only small numbers, booleans, nulls and the type string: nothing that could be pixels."""
    message = busy_message()
    text = json.dumps(message)
    assert len(text) < 600  # two faces; a picture or a landmark list would be far larger

    def check(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                assert not any(
                    word in key.lower()
                    for word in ("image", "frame", "jpeg", "png", "pixel", "landmark", "embedding")
                ), key
                check(inner, f"{path}.{key}")
        elif isinstance(value, list):
            assert path.endswith(".faces") or len(value) == 4, path
            for inner in value:
                check(inner, path)
        elif isinstance(value, str):
            assert value == "vision.state", f"unexpected string at {path}"
        else:
            assert value is None or isinstance(value, (bool, int, float)), path

    check(message, "msg")
    assert not isinstance(json.loads(text).get("faces")[0]["box"][0], str)


def test_empty_state_and_camera_down() -> None:
    message = build_vision_state(EMPTY_STATE, camera_ok=False, servo_target_deg=0.0)
    assert message == {
        "type": "vision.state",
        "t_ms": 0,
        "camera_ok": False,
        "faces": [],
        "selected_id": None,
        "engaged": False,
        "active_speaker_id": None,
        "overlap": False,
        "servo_target_deg": 0.0,
    }
    assert json.loads(json.dumps(message)) == message


def test_boxes_are_clamped_to_the_frame() -> None:
    logic = VisionLogic(Thresholds())
    state = logic.update([face(-0.05, w=0.3), face(0.9, w=0.3)], 0.0)
    message = build_vision_state(state, camera_ok=True, servo_target_deg=0)
    boxes = [f["box"] for f in message["faces"]]
    assert all(0.0 <= v <= 1.0 for box in boxes for v in box)


def test_parse_inbound() -> None:
    assert parse_inbound('{"type":"vad","speaking":true}') == VadMessage(True)
    assert parse_inbound('{"type":"vad","speaking":false}') == VadMessage(False)
    assert parse_inbound('{"type":"servo","pan_deg":-12.5}') == ServoMessage(-12.5)
    assert parse_inbound('{"type":"servo","pan_deg":3}') == ServoMessage(3.0)
    for bad in (
        "",
        "not json",
        "[1,2]",
        '{"type":"vad"}',
        '{"type":"vad","speaking":"yes"}',
        '{"type":"servo","pan_deg":"left"}',
        '{"type":"servo","pan_deg":true}',
        '{"type":"servo","pan_deg":NaN}',
        '{"type":"other"}',
    ):
        assert parse_inbound(bad) is None, bad


def test_console_line_shows_the_labels() -> None:
    line = console_line(
        busy_message(),
        vad_label="vad=ASSUMED(mouth motion)=on",
        servo_label="servo SIMULATED at=-10.0",
        fps=14.9,
    )
    assert "faces=2" in line and "selected=1" in line and "engaged=YES" in line
    assert "ASSUMED" in line and "SIMULATED" in line and "target=-12.3" in line
    assert "#1[FM" in line and "#2[F-" in line
