"""Room geometry for the SIMULATED robot: axis-aligned boxes and ray distances.

Copied from the sprint-1 simulator (backend/app/telemetry/simulator.py) so that the backend
simulator and the simulated ESP32 of robotd use the same code. Simulation only (CLAUDE.md rule 5).
"""

import math
from collections.abc import Iterable

Box = tuple[float, float, float, float]  # x_min, y_min, x_max, y_max


def ray_box(px: float, py: float, dx: float, dy: float, box: Box, inside: bool) -> float:
    """Distance along the ray to the box (from outside), or to its walls (from inside)."""
    x0, y0, x1, y1 = box
    t_near, t_far = -math.inf, math.inf
    for p, d, lo, hi in ((px, dx, x0, x1), (py, dy, y0, y1)):
        if abs(d) < 1e-12:
            if p < lo or p > hi:
                return math.inf
            continue
        a, b = (lo - p) / d, (hi - p) / d
        t_near, t_far = max(t_near, min(a, b)), min(t_far, max(a, b))
    if t_near > t_far or t_far < 0:
        return math.inf
    if inside:
        return t_far
    return t_near if t_near >= 0 else 0.0  # already inside an obstacle: distance 0


def range_along(
    x: float, y: float, heading: float, room: Box, obstacles: Iterable[Box]
) -> float:  # fmt: skip
    """Distance from (x, y) along `heading` to the nearest room wall or obstacle."""
    dx, dy = math.cos(heading), math.sin(heading)
    best = ray_box(x, y, dx, dy, room, inside=True)
    for box in obstacles:
        best = min(best, ray_box(x, y, dx, dy, box, inside=False))
    return best


def range_min(
    x: float, y: float, theta: float, offsets: Iterable[float], room: Box, obstacles: Iterable[Box]
) -> float:  # fmt: skip
    """Nearest distance over several rays (`theta + offset`), e.g. three front ultrasonics."""
    boxes = tuple(obstacles)
    return min(range_along(x, y, theta + offset, room, boxes) for offset in offsets)
