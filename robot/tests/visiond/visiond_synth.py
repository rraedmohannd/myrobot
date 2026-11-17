"""Synthetic face sequences for the visiond tests (numbers only: no camera, no pictures)."""

from __future__ import annotations

import math

from visiond.logic import Box, FaceObservation

FRAME_MS = 1000.0 / 15.0  # the analysis runs at about 15 frames per second
SILENT = 0.02


def talking(i: int) -> float:
    """A mouth that opens and closes (same curve as backend/tests/js/speaker_logic_test.mjs)."""
    return 0.25 + 0.2 * math.sin(i * 1.3)


def face(
    x: float, mouth: float = SILENT, *, facing: bool = True, y: float = 0.3, w: float = 0.2
) -> FaceObservation:
    return FaceObservation(box=Box(x, y, w, 0.3), mouth=mouth, facing=facing)


LEFT_X = 0.1
RIGHT_X = 0.6
