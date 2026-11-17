"""Privacy guard: visiond has no code that stores or encodes a picture, and the pure modules
do not import OpenCV. A source scan is crude, but it fails loudly if someone adds a
`cv2.imwrite` "just for debugging"."""

from __future__ import annotations

import re

from visiond.config import ROBOT_DIR

PACKAGE = ROBOT_DIR / "visiond"
FORBIDDEN = [
    r"imwrite",
    r"imencode",
    r"VideoWriter",
    r"\.tofile\(",
    r"np\.save",
    r"pickle",
    r"write_bytes",
    r"write_text",
    r"\bopen\(",
    r"send_bytes",
    r"base64",
]
PURE_MODULES = ["config.py", "geometry.py", "logic.py", "servo.py", "messages.py"]


def test_nothing_in_visiond_writes_or_encodes_pictures() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 9
    for file in files:
        text = file.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text), f"{file.name} contains '{pattern}'"


def test_pure_modules_do_not_import_opencv_or_mediapipe() -> None:
    for name in PURE_MODULES:
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(cv2|mediapipe|numpy)", text, re.M), name


def test_only_the_message_builder_output_is_sent() -> None:
    """The WebSocket link sends JSON text built from dicts; there is one send call."""
    text = (PACKAGE / "link.py").read_text(encoding="utf-8")
    assert text.count("send_str(") == 1 and "json.dumps(message" in text
