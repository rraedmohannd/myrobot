"""SIMULATED Rover 5 run for development: reactive roaming, then return-to-origin.

*** Everything produced here is simulation. It is stored with is_simulated = true, shown with a
"SIMULATED" banner, and must never be reported as a physical test (CLAUDE.md rule 5). ***

What is simulated
- A room of about 6 x 5 m with a few box obstacles. The world frame is fixed at the start pose.
- Reactive roaming: drive forward, turn away when the (simulated) ultrasonic sensor sees an
  obstacle, and never drive one segment longer than 5 m.
- Sensors with noise: the encoders see the track motion, but the tracks slip (a little when
  driving, a lot when turning); the gyro has a constant bias and random noise.
- The robot estimates its pose with app/telemetry/odometry.py (encoders for distance, gyro for
  heading). The controller only knows this estimate, never the true pose.
- Return-to-origin as in docs/ODOMETRY.md section 4: direct vector first; when the direct path is
  blocked, breadcrumbs (recorded every ~0.3 m or on a heading change) are followed in reverse.

The noise values are plausible guesses, NOT measurements of the real robot. They will be
replaced by calibration results (ODOMETRY.md section 5) when the hardware exists.
"""

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from app.telemetry.odometry import (
    Pose,
    encoder_heading_change,
    update_pose,
    vector_to_origin,
    wrap_angle,
)

Box = tuple[float, float, float, float]  # x_min, y_min, x_max, y_max


@dataclass
class SimConfig:
    seed: int = 7
    dt_s: float = 0.02  # 50 Hz control loop, as in ODOMETRY.md
    sample_every: int = 5  # store 10 Hz
    roam_seconds: float = 45.0
    return_timeout_s: float = 180.0
    # room in the world frame (start pose = origin): 6 m x 5 m
    room: Box = (-1.0, -1.5, 5.0, 3.5)
    obstacles: tuple[Box, ...] = ((1.6, 0.9, 2.2, 1.5), (3.3, -0.9, 3.9, -0.3), (0.6, 2.3, 1.2, 2.9))
    track_width_m: float = 0.19  # placeholder until measured on the Rover 5
    speed_mps: float = 0.25
    turn_rate_rps: float = 1.0
    max_segment_m: float = 5.0
    obstacle_stop_m: float = 0.45
    breadcrumb_spacing_m: float = 0.3
    arrival_tolerance_m: float = 0.15  # proposed in ODOMETRY.md, to be approved by the supervisor
    # noise (guesses, not measurements): what could remain AFTER the calibration steps of
    # ODOMETRY.md section 5 (distance per tick, gyro bias measured while standing still)
    slip_drive: float = 0.01  # mean fraction of track motion lost when driving straight
    slip_turn: float = 0.25  # mean fraction of rotation lost when turning in place
    slip_noise: float = 0.3  # relative spread of both slips
    gyro_bias_rps: float = 0.001  # residual bias, about 0.06 deg/s
    gyro_noise_rps: float = 0.01


@dataclass
class SimResult:
    config: SimConfig
    samples: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = ""  # return_done / return_failed

    def odom_config(self) -> dict[str, Any]:
        """Stored in robot_runs.odom_config, so every simulated run documents its own settings."""
        cfg = asdict(self.config)
        return {
            "simulated": True,
            "simulator": "app.telemetry.simulator",
            "note": "SIMULATED run - noise values are guesses, not measurements of the robot",
            "track_width_m": cfg["track_width_m"],
            "arrival_tolerance_m": cfg["arrival_tolerance_m"],
            "sample_rate_hz": round(1 / (cfg["dt_s"] * cfg["sample_every"]), 1),
            "sim": cfg,
        }


# ---------------- geometry ----------------
def _ray_box(px: float, py: float, dx: float, dy: float, box: Box, inside: bool) -> float:
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


def range_ahead(cfg: SimConfig, pose: Pose) -> float:
    """Simulated ultrasonic reading: nearest wall or obstacle over three rays (-25, 0, +25 deg)."""
    best = math.inf
    for offset in (-0.44, 0.0, 0.44):
        dx, dy = math.cos(pose.theta + offset), math.sin(pose.theta + offset)
        best = min(best, _ray_box(pose.x, pose.y, dx, dy, cfg.room, inside=True))
        for box in cfg.obstacles:
            best = min(best, _ray_box(pose.x, pose.y, dx, dy, box, inside=False))
    return best


# ---------------- the simulated robot ----------------
class _Robot:
    def __init__(self, cfg: SimConfig, rng: random.Random, result: SimResult) -> None:
        self.cfg, self.rng, self.result = cfg, rng, result
        self.true = Pose()  # where the simulated robot really is
        self.odom = Pose()  # what it believes (encoders + gyro)
        self.enc_theta = 0.0  # encoder-only heading, for comparison
        self.t = 0.0
        self.step_no = 0
        self.travelled = 0.0  # odometry distance
        self.ticks_l = self.ticks_r = 0.0  # metres of track motion, accumulated

    def _slip(self, mean: float) -> float:
        return min(0.9, max(0.0, self.rng.gauss(mean, mean * self.cfg.slip_noise)))

    def step(self, v_cmd: float, w_cmd: float, state: str) -> None:
        cfg, dt = self.cfg, self.cfg.dt_s
        # commanded track speeds; the encoders measure this track motion
        d_left = (v_cmd - w_cmd * cfg.track_width_m / 2) * dt
        d_right = (v_cmd + w_cmd * cfg.track_width_m / 2) * dt
        # the true motion is smaller because the tracks slip
        v_true = v_cmd * (1 - self._slip(cfg.slip_drive))
        w_true = w_cmd * (1 - self._slip(cfg.slip_turn))
        mid = self.true.theta + w_true * dt / 2
        self.true = Pose(
            self.true.x + v_true * dt * math.cos(mid),
            self.true.y + v_true * dt * math.sin(mid),
            wrap_angle(self.true.theta + w_true * dt),
        )
        gyro = w_true + cfg.gyro_bias_rps + self.rng.gauss(0, cfg.gyro_noise_rps)
        self.odom = update_pose(self.odom, d_left, d_right, gyro, dt)
        self.enc_theta = wrap_angle(
            self.enc_theta + encoder_heading_change(d_left, d_right, cfg.track_width_m)
        )
        self.travelled += abs((d_left + d_right) / 2)
        self.ticks_l += d_left
        self.ticks_r += d_right
        self.t += dt
        self.step_no += 1
        if self.step_no % cfg.sample_every == 0:
            self.sample(v_cmd, gyro, state)

    def sample(self, v: float, gyro: float, state: str) -> None:
        self.result.samples.append({
            "t_ms": int(round(self.t * 1000)),
            "x_m": round(self.odom.x, 4), "y_m": round(self.odom.y, 4),
            "theta_rad": round(self.odom.theta, 4),
            "v_mps": round(v, 3), "omega_rps": round(gyro, 4),
            "enc": {"left_m": round(self.ticks_l, 4), "right_m": round(self.ticks_r, 4),
                    "enc_theta_rad": round(self.enc_theta, 4)},
            # only a simulation has a "true" pose; a real run never has this key
            "imu": {"gyro_z_rps": round(gyro, 4),
                    "sim_true_pose": [round(self.true.x, 4), round(self.true.y, 4),
                                      round(self.true.theta, 4)]},
            "state": state,
        })  # fmt: skip

    def event(self, kind: str, **data: Any) -> None:
        self.result.events.append({"t_ms": int(round(self.t * 1000)), "kind": kind, "data": data})

    def blocked(self, limit: float | None = None) -> bool:
        return range_ahead(self.cfg, self.true) < (limit or self.cfg.obstacle_stop_m)

    def turn_to(self, target: float, state: str) -> None:
        """Rotate in place until the odometry heading reaches `target` (gyro-controlled)."""
        for _ in range(int(8 / self.cfg.dt_s)):
            error = wrap_angle(target - self.odom.theta)
            if abs(error) < 0.03:
                return
            rate = max(0.25, min(self.cfg.turn_rate_rps, abs(error) * 2.5))
            self.step(0.0, math.copysign(rate, error), state)

    def error_from_start(self) -> float:
        return round(math.hypot(self.true.x, self.true.y), 3)


def simulate_run(cfg: SimConfig | None = None) -> SimResult:
    """One SIMULATED run: roam, then return to the start point. Deterministic for a given seed."""
    cfg = cfg or SimConfig()
    rng = random.Random(cfg.seed)
    result = SimResult(config=cfg)
    bot = _Robot(cfg, rng, result)
    bot.sample(0.0, 0.0, "start")
    bot.event("run_start", simulated=True, seed=cfg.seed)

    # ---- outbound: reactive roaming, breadcrumbs every ~0.3 m and after each turn ----
    crumbs: list[tuple[float, float]] = [(0.0, 0.0)]
    last_crumb_at = 0.0

    def drop_crumb() -> None:
        nonlocal last_crumb_at
        crumbs.append((bot.odom.x, bot.odom.y))
        last_crumb_at = bot.travelled

    segment_start, segment_limit = 0.0, rng.uniform(1.5, cfg.max_segment_m)
    while bot.t < cfg.roam_seconds:
        hit = bot.blocked()
        if hit or bot.travelled - segment_start >= segment_limit:
            if hit:
                bot.event("obstacle", range_m=round(range_ahead(cfg, bot.true), 2),
                          x_m=round(bot.odom.x, 3), y_m=round(bot.odom.y, 3))  # fmt: skip
            drop_crumb()
            for _ in range(12):  # pick a new heading that is free
                turn = rng.choice((-1, 1)) * rng.uniform(1.2, 2.6)
                bot.turn_to(wrap_angle(bot.odom.theta + turn), "turn")
                if not bot.blocked(cfg.obstacle_stop_m + 0.25):
                    break
            segment_start, segment_limit = bot.travelled, rng.uniform(1.5, cfg.max_segment_m)
            continue
        bot.step(cfg.speed_mps, 0.0, "roam")
        if bot.travelled - last_crumb_at >= cfg.breadcrumb_spacing_m:
            drop_crumb()
    drop_crumb()

    # ---- return-to-origin ----
    distance, bearing = vector_to_origin(bot.odom)
    bot.event("return_start", odom_x=round(bot.odom.x, 3), odom_y=round(bot.odom.y, 3),
              distance_m=round(distance, 3), bearing_rad=round(bearing, 3),
              breadcrumbs=len(crumbs))  # fmt: skip
    deadline = bot.t + cfg.return_timeout_s

    def drive_to(x: float, y: float, state: str, tolerance: float) -> str:
        """Closed-loop drive to a point of the odometry frame: reached / blocked / timeout."""
        blocked_steps = 0
        first = True
        while bot.t < deadline:
            dx, dy = x - bot.odom.x, y - bot.odom.y
            dist = math.hypot(dx, dy)
            if dist < tolerance:
                return "reached"
            error = wrap_angle(math.atan2(dy, dx) - bot.odom.theta)
            if first or abs(error) > 0.6:  # rotate in place first (gyro-controlled)
                bot.turn_to(math.atan2(dy, dx), state + "_rotate")
                first = False
                continue
            if bot.blocked(0.40) and dist > 0.45:
                blocked_steps += 1
                if blocked_steps > 5:
                    return "blocked"
                bot.step(0.0, 0.0, state)
                continue
            blocked_steps = 0
            speed = min(cfg.speed_mps, max(0.08, dist * 0.8))  # slow down near the target
            bot.step(speed, max(-1.0, min(1.0, 2.0 * error)), state)
        return "timeout"

    outcome = drive_to(0.0, 0.0, "return_direct", cfg.arrival_tolerance_m)
    method = "direct"
    if outcome == "blocked":
        method = "breadcrumbs"
        bot.event("return_blocked", x_m=round(bot.odom.x, 3), y_m=round(bot.odom.y, 3),
                  fallback="breadcrumbs", remaining=len(crumbs))  # fmt: skip
        outcome = "reached"
        for cx, cy in reversed(crumbs):
            last = (cx, cy) == (0.0, 0.0)
            step = drive_to(cx, cy, "return_breadcrumb", cfg.arrival_tolerance_m if last else 0.12)
            if step != "reached":
                outcome = step
                break

    final_distance, _ = vector_to_origin(bot.odom)
    summary = {
        "method": method,
        "odom_end_x": round(bot.odom.x, 3), "odom_end_y": round(bot.odom.y, 3),
        "odom_distance_m": round(final_distance, 3),
        # a simulation knows its own truth; this is NOT a tape measurement of the real robot
        "simulated_true_error_m": bot.error_from_start(),
        "simulated": True,
    }  # fmt: skip
    if outcome == "reached":
        bot.event("return_done", **summary)
        result.outcome = "return_done"
    else:
        bot.event("return_failed", reason=outcome, **summary)
        result.outcome = "return_failed"
    bot.sample(0.0, 0.0, "stopped")
    return result
