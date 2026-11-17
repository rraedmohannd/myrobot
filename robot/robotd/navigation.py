"""Navigation: reactive roaming and return-to-start (docs/ODOMETRY.md section 4).

Pure logic: every step gets a `Sense` (odometry pose + ultrasonic distances) and returns the
wanted motion (v m/s, w rad/s). No I/O and no clock; time advances by `Sense.dt_s`.

Rules built in here:
- one straight segment is never longer than `max_segment_m` (5 m);
- when blocked, turn toward the freer side (3 front sensors) until a direction is free;
  if a full turn finds none, stay stopped;
- reverse only when the rear sensor is present and clear (the ESP32 enforces this too);
- the way back uses the pose and breadcrumb POSITIONS; recorded motor commands are never replayed.

The ESP32's own obstacle stop (0.25 m) is the safety layer; the distances here are larger so that
it normally never triggers.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any

from scr_odometry import Pose, wrap_angle

from robotd.config import Config

Distance = float | None  # metres; math.inf = no echo (free); None = sensor absent or faulty
Event = tuple[str, dict[str, Any]]


@dataclass
class Sense:
    pose: Pose
    travelled_m: float  # cumulative |distance| from the odometry
    front: tuple[Distance, Distance, Distance]  # front-left, front-centre, front-right
    rear: Distance
    dt_s: float

    def front_min(self) -> float:
        """Nearest front distance; an absent sensor counts as blocked (0 m)."""
        return min(0.0 if d is None else d for d in self.front)

    def front_all_absent(self) -> bool:
        return all(d is None for d in self.front)

    def rear_clear(self, limit: float) -> bool:
        return self.rear is not None and self.rear > limit


def track_speeds(v: float, w: float, track_width_m: float) -> tuple[float, float]:
    """(v, w) -> left, right track speeds for the DRIVE message."""
    return v - w * track_width_m / 2.0, v + w * track_width_m / 2.0


class Breadcrumbs:
    """Waypoints of the outbound path: every ~0.3 m, or where the heading changed."""

    LOOP_SKIP = 3  # the newest crumbs are always near: not a loop

    def __init__(self, spacing_m: float, heading_rad: float, loop_radius_m: float = 0.25) -> None:
        self.spacing_m, self.heading_rad, self.loop_radius_m = spacing_m, heading_rad, loop_radius_m
        self.points: list[tuple[float, float]] = [(0.0, 0.0)]
        self._theta = 0.0

    def reset(self) -> None:
        self.points, self._theta = [(0.0, 0.0)], 0.0

    def update(self, pose: Pose) -> bool:
        x, y = self.points[-1]
        moved = math.hypot(pose.x - x, pose.y - y)
        turned = abs(wrap_angle(pose.theta - self._theta))
        if moved >= self.spacing_m or (turned >= self.heading_rad and moved >= 0.05):
            self._theta = pose.theta
            # Back at a place the path already passed: cut the loop out, so the way home stays
            # short however long the robot roams. (Positions only; no motor commands are kept.)
            for i, (px, py) in enumerate(self.points[: -self.LOOP_SKIP]):
                if math.hypot(pose.x - px, pose.y - py) < self.loop_radius_m:
                    del self.points[i + 1 :]
                    break
            self.points.append((pose.x, pose.y))
            return True
        return False


@dataclass
class Roamer:
    cfg: Config
    rng: random.Random
    phase: str = "forward"  # forward / turn / backoff / blocked
    events: list[Event] = field(default_factory=list)
    segments: list[float] = field(default_factory=list)  # finished straight segments (m)
    _seg_start: float = 0.0
    _seg_limit: float = 0.0
    _turn_dir: float = 1.0
    _turn_min: float = 0.0
    _turned: float = 0.0
    _last_theta: float = 0.0
    _backoff_start: float = 0.0
    _blocked_s: float = 0.0

    RESCAN_S = 15.0  # while blocked: look around again this often

    def reset(self, sense: Sense) -> None:
        """(Re)start roaming here: recheck the path first, never continue an old segment."""
        self._new_segment(sense)
        if sense.front_min() < self.cfg.obstacle_clear_m:
            self._start_turn(sense, self._free_side(sense), 0.0)

    @property
    def segment_m(self) -> float:
        return self._current

    _current: float = 0.0

    def step(self, sense: Sense) -> tuple[float, float]:
        cfg = self.cfg
        if self.phase == "forward":
            self._current = sense.travelled_m - self._seg_start
            nearest = sense.front_min()
            if nearest < cfg.obstacle_stop_m:
                self._end_segment()
                self.events.append(("obstacle", {"range_m": round(nearest, 2)}))
                if nearest < cfg.backoff_below_m and sense.rear_clear(cfg.rear_clear_m):
                    self.phase, self._backoff_start = "backoff", sense.travelled_m
                    return 0.0, 0.0
                self._start_turn(sense, self._free_side(sense), 0.6)
                return 0.0, 0.0
            if self._current >= self._seg_limit:
                self._end_segment()
                self._start_turn(sense, self.rng.choice((-1.0, 1.0)), self.rng.uniform(1.2, 2.6))
                return 0.0, 0.0
            return cfg.roam_speed_mps, 0.0
        if self.phase == "backoff":
            done = sense.travelled_m - self._backoff_start >= cfg.backoff_distance_m
            if done or not sense.rear_clear(cfg.rear_clear_m):  # never reverse blind
                self._start_turn(sense, self._free_side(sense), 0.6)
                return 0.0, 0.0
            return -cfg.roam_speed_mps / 2.0, 0.0
        if self.phase == "turn":
            self._turned += abs(wrap_angle(sense.pose.theta - self._last_theta))
            self._last_theta = sense.pose.theta
            if self._turned >= self._turn_min and sense.front_min() >= cfg.obstacle_clear_m:
                self._new_segment(sense)
                return 0.0, 0.0
            if self._turned >= self._turn_min + 2 * math.pi:  # a full turn: nothing is free
                self.phase, self._blocked_s = "blocked", 0.0
                self.events.append(("roam_blocked", {"reason": "no free direction"}))
                return 0.0, 0.0
            return 0.0, self._turn_dir * cfg.turn_rate_rps
        # blocked: stay stopped; go on when the front is free, look around again now and then
        self._blocked_s += sense.dt_s
        if sense.front_min() >= cfg.obstacle_clear_m:
            self._new_segment(sense)
        elif self._blocked_s >= self.RESCAN_S and not sense.front_all_absent():
            self._start_turn(sense, self._free_side(sense), 0.0)
        return 0.0, 0.0

    def _free_side(self, sense: Sense) -> float:
        """+1 = turn left, -1 = turn right: toward the side sensor that sees further."""
        left = 0.0 if sense.front[0] is None else sense.front[0]
        right = 0.0 if sense.front[2] is None else sense.front[2]
        if left == right:
            return self.rng.choice((-1.0, 1.0))
        return 1.0 if left > right else -1.0

    def _start_turn(self, sense: Sense, direction: float, min_angle: float) -> None:
        self.phase = "turn"
        self._turn_dir, self._turn_min, self._turned = direction, min_angle, 0.0
        self._last_theta = sense.pose.theta

    def _new_segment(self, sense: Sense) -> None:
        self.phase = "forward"
        self._seg_start, self._current = sense.travelled_m, 0.0
        self._seg_limit = self.rng.uniform(self.cfg.min_segment_m, self.cfg.max_segment_m)

    def _end_segment(self) -> None:
        if self._current > 0:
            self.segments.append(self._current)
        self._current = 0.0


@dataclass
class Returner:
    """Return-to-start: direct vector first (closed loop on the pose), breadcrumbs when blocked."""

    cfg: Config
    method: str = "direct"  # direct / breadcrumbs
    result: tuple[str, dict[str, Any]] | None = None  # ("return_done" | "return_failed", data)
    events: list[Event] = field(default_factory=list)
    _crumbs: list[tuple[float, float]] = field(default_factory=list)
    _index: int = 0
    _rotate: bool = True
    _blocked_s: float = 0.0
    _elapsed_s: float = 0.0
    _skips: int = 0

    MAX_SKIPS = 5  # unreachable breadcrumbs in a row before `return_failed{blocked}`

    def start(self, crumbs: list[tuple[float, float]], pose: Pose) -> None:
        self.method, self.result = "direct", None
        self._crumbs = [*reversed(crumbs)]  # newest first; the last one is the origin
        if not self._crumbs or self._crumbs[-1] != (0.0, 0.0):
            self._crumbs.append((0.0, 0.0))
        self._index, self._rotate, self._blocked_s, self._elapsed_s = 0, True, 0.0, 0.0
        self._skips = 0
        distance = math.hypot(pose.x, pose.y)
        self.events.append((
            "return_start",
            {"odom_x": round(pose.x, 3), "odom_y": round(pose.y, 3),
             "distance_m": round(distance, 3),
             "bearing_rad": round(math.atan2(-pose.y, -pose.x), 3),
             "breadcrumbs": len(crumbs)},
        ))  # fmt: skip

    def _finish(self, kind: str, pose: Pose, **data: Any) -> tuple[float, float]:
        summary = {
            "method": self.method,
            "odom_end_x": round(pose.x, 3), "odom_end_y": round(pose.y, 3),
            "odom_distance_m": round(math.hypot(pose.x, pose.y), 3),
            "elapsed_s": round(self._elapsed_s, 1),
        }  # fmt: skip
        self.result = (kind, {**data, **summary})
        return 0.0, 0.0

    def step(self, sense: Sense) -> tuple[float, float]:
        cfg, pose = self.cfg, sense.pose
        if self.result is not None:
            return 0.0, 0.0
        self._elapsed_s += sense.dt_s
        if self._elapsed_s > cfg.return_timeout_s:
            return self._finish("return_failed", pose, reason="timeout")
        if sense.front_all_absent():
            return self._finish("return_failed", pose, reason="sensor_fault")
        direct = self.method == "direct"
        while True:
            tx, ty = (0.0, 0.0) if direct else self._crumbs[self._index]
            final = direct or self._index == len(self._crumbs) - 1
            dx, dy = tx - pose.x, ty - pose.y
            dist = math.hypot(dx, dy)
            if dist >= (cfg.arrival_tolerance_m if final else cfg.crumb_tolerance_m):
                break
            if final:
                return self._finish("return_done", pose)
            self._index += 1
            self._skips = 0
            self._rotate = True
        error = wrap_angle(math.atan2(dy, dx) - pose.theta)
        if self._rotate or abs(error) > 0.6:  # rotate in place first (gyro-controlled)
            self._rotate = abs(error) >= 0.05
            if self._rotate:
                rate = max(0.25, min(cfg.turn_rate_rps, abs(error) * 2.5))
                return 0.0, math.copysign(rate, error)
        nearest = sense.front_min()
        # blocked: something stands before the target, or so near that the ESP32 would refuse
        if nearest < cfg.return_block_m and (dist > nearest or nearest < cfg.hard_stop_m):
            self._blocked_s += sense.dt_s
            if direct and self._blocked_s > 0.3:
                self.method, self._index, self._rotate, self._blocked_s = (
                    "breadcrumbs",
                    0,
                    True,
                    0.0,
                )
                self.events.append((
                    "return_blocked",
                    {"x_m": round(pose.x, 3), "y_m": round(pose.y, 3),
                     "fallback": "breadcrumbs", "remaining": len(self._crumbs)},
                ))  # fmt: skip
            elif (
                not direct
                and not final
                and self._skips < self.MAX_SKIPS
                and (dist < 0.5 or self._blocked_s > 1.0)
            ):
                # this crumb cannot be reached (drift, or something new stands there): try the
                # one before it; give up after a few in a row
                self._index, self._rotate, self._blocked_s = self._index + 1, True, 0.0
                self._skips += 1
            elif not direct and self._blocked_s > cfg.return_block_wait_s:
                return self._finish("return_failed", pose, reason="blocked")
            return 0.0, 0.0
        self._blocked_s = 0.0
        speed = min(cfg.roam_speed_mps, max(0.08, dist * 0.8))  # slow down near the target
        return speed, max(-1.0, min(1.0, 2.0 * error))
