"""Navigation against the SIMULATED ESP32, in simulated time (deterministic, no sleeps).

*** Simulation only: the distances below say that the logic works in the simulator, nothing
about the real Rover 5 (CLAUDE.md rule 5). ***
"""

import math
import random

from scr_odometry import Pose, range_along
from scr_serial import Drive

from robotd.config import Config
from robotd.navigation import Breadcrumbs, Returner, Roamer, Sense
from robotd.sim_esp32 import SimEsp32Config
from robotd.sim_harness import SimHarness

OPEN_ROOM = (-20.0, -20.0, 20.0, 20.0)


def started(cfg: Config | None = None, sim_cfg: SimEsp32Config | None = None) -> SimHarness:
    h = SimHarness(cfg or Config(sim=True), sim_cfg)
    h.run(0.5)
    assert h.brain.sup.state == "IDLE"
    assert h.command("start_roaming")["accepted"]
    return h


def event_kinds(h: SimHarness) -> list[str]:
    return [kind for kind, _ in h.brain.events]


def last_event(h: SimHarness, kind: str) -> dict:
    return [data for k, data in h.brain.events if k == kind][-1]


# ---------------- roaming ----------------
def test_roam_turns_away_from_an_obstacle() -> None:
    h = started()  # default room: the wall is 5 m ahead of the start pose
    assert h.run_until(lambda: "obstacle" in event_kinds(h), 40)
    at = h.sim.true
    seen = last_event(h, "obstacle")["range_m"]
    assert 0.25 < seen <= h.cfg.obstacle_stop_m  # noticed well before the ESP32's 0.25 m stop
    sim = h.sim.cfg
    ahead = [
        range_along(at.x, at.y, at.theta + a, sim.room, sim.obstacles) for a in (0.44, 0, -0.44)
    ]
    assert min(ahead) < h.cfg.obstacle_stop_m + 0.05  # there really is something in front
    assert h.brain.roamer.phase == "turn"
    assert h.run_until(lambda: h.brain.roamer.phase == "forward", 20)
    turned = abs(
        math.atan2(math.sin(h.sim.true.theta - at.theta), math.cos(h.sim.true.theta - at.theta))
    )
    assert turned > 0.4  # it turned away ...
    now = h.sim.true
    ahead = [
        range_along(now.x, now.y, now.theta + a, sim.room, sim.obstacles) for a in (0.44, 0, -0.44)
    ]
    assert min(ahead) >= h.cfg.obstacle_clear_m - 0.05  # ... toward a free direction
    h.run(60)
    assert h.brain.sup.state == "ROAMING"
    assert h.sim.collisions == 0
    assert h.brain.rejected == []  # the ESP32 never had to refuse a command
    assert "obstacle_stop" not in event_kinds(h)  # ... or to stop the robot itself
    assert event_kinds(h).count("obstacle") >= 2
    assert math.hypot(h.sim.true.x - at.x, h.sim.true.y - at.y) > 0.3  # and it keeps going


def test_one_straight_segment_is_at_most_5_m() -> None:
    cfg = Config(sim=True, min_segment_m=4.0)
    h = started(cfg, SimEsp32Config(room=OPEN_ROOM, obstacles=()))
    assert h.run_until(lambda: len(h.brain.roamer.segments) >= 3, 150)
    assert "obstacle" not in event_kinds(h)  # nothing in the way: only the limit ends a segment
    assert all(4.0 <= s <= 5.0 + 0.02 for s in h.brain.roamer.segments)
    assert h.brain.roamer.segment_m <= 5.0 + 0.02


def test_roam_uses_the_low_configured_speed() -> None:
    h = started(Config(sim=True, roam_speed_mps=0.12))
    h.run(5)
    drives = [m for m in h.sim.received if isinstance(m, Drive)]
    assert drives and max(max(m.left, m.right) for m in drives) <= 0.12 + 1e-9
    assert len(drives) == 20 * 5 or abs(len(drives) - 100) <= 12  # DRIVE at 20 Hz while moving


def sense(
    front: tuple, rear: float | None, pose: Pose | None = None, travelled: float = 0.0
) -> Sense:
    return Sense(pose or Pose(), travelled, front, rear, 0.02)


def test_stays_stopped_when_no_direction_is_free() -> None:
    cfg = Config()
    roamer = Roamer(cfg, random.Random(1))
    boxed = (0.3, 0.3, 0.3)
    roamer.reset(sense(boxed, 0.3))
    theta, commands = 0.0, []
    for _ in range(1000):  # 20 s: more than a full turn at 1 rad/s
        v, w = roamer.step(sense(boxed, 0.3, Pose(0.0, 0.0, theta)))
        theta = math.atan2(math.sin(theta + w * 0.02), math.cos(theta + w * 0.02))
        commands.append((v, w))
    assert all(v == 0.0 for v, _ in commands)  # it never drives into anything
    assert roamer.phase == "blocked" and commands[-1] == (0.0, 0.0)
    assert ("roam_blocked", {"reason": "no free direction"}) in roamer.events
    # the way becomes free: it goes on
    roamer.step(sense((2.0, 2.0, 2.0), 0.3))
    assert roamer.phase == "forward"


def test_turns_toward_the_free_side() -> None:
    roamer = Roamer(Config(), random.Random(1))
    roamer.reset(sense((3.0, 3.0, 3.0), 1.0))
    assert roamer.step(sense((3.0, 3.0, 3.0), 1.0))[0] > 0
    roamer.step(sense((2.0, 0.4, 0.3), 1.0))  # left is freer
    assert roamer.step(sense((2.0, 0.4, 0.3), 1.0))[1] > 0
    roamer = Roamer(Config(), random.Random(1))
    roamer.reset(sense((3.0, 3.0, 3.0), 1.0))
    roamer.step(sense((0.3, 0.4, 2.0), 1.0))  # right is freer
    assert roamer.step(sense((0.3, 0.4, 2.0), 1.0))[1] < 0


def test_reverse_only_with_rear_sensing() -> None:
    cfg = Config()
    close = (0.15, 0.15, 0.15)
    for rear in (None, 0.2):  # no rear sensor / something behind: never reverse
        roamer = Roamer(cfg, random.Random(1))
        roamer.reset(sense((3.0, 3.0, 3.0), rear))
        outs = [roamer.step(sense(close, rear)) for _ in range(50)]
        assert all(v >= 0.0 for v, _ in outs) and roamer.phase == "turn"
    roamer = Roamer(cfg, random.Random(1))
    roamer.reset(sense((3.0, 3.0, 3.0), 1.0))
    roamer.step(sense(close, 1.0))
    assert roamer.phase == "backoff"
    assert roamer.step(sense(close, 1.0))[0] < 0  # rear sensor present and clear: back off
    assert roamer.step(sense(close, 0.3))[0] == 0.0 and roamer.phase == "turn"  # rear closes: stop


def test_simulated_robot_without_rear_sensor_never_reverses() -> None:
    h = started(sim_cfg=SimEsp32Config(rear_sensor=False))
    h.run(90)
    drives = [m for m in h.sim.received if isinstance(m, Drive)]
    assert len(drives) > 500 and all(m.left + m.right >= -1e-9 for m in drives)
    assert h.brain.rejected == [] and h.sim.collisions == 0
    assert h.brain.status(h.now_s)["sensors"]["ultrasonic"] == "degraded"


# ---------------- breadcrumbs ----------------
def test_breadcrumbs_every_30_cm_and_on_heading_change() -> None:
    crumbs = Breadcrumbs(0.3, 0.5)
    for i in range(1, 101):
        crumbs.update(Pose(i * 0.01, 0.0, 0.0))
    assert len(crumbs.points) == 4 and crumbs.points[1][0] == 0.3
    crumbs.update(Pose(1.0, 0.0, 1.0))  # heading change 10 cm after the last crumb: recorded
    assert len(crumbs.points) == 5 and crumbs.points[-1] == (1.0, 0.0)
    crumbs.update(Pose(1.0, 0.0, 2.0))  # turning on the spot adds nothing
    assert len(crumbs.points) == 5
    crumbs.update(Pose(1.0, 0.06, 2.0))  # ... until the robot moves on
    assert len(crumbs.points) == 6


def test_breadcrumbs_cut_loops_out() -> None:
    crumbs = Breadcrumbs(0.3, 0.5)
    corners = [(1.2, 0.0), (1.2, 1.2), (0.0, 1.2), (0.0, 0.0), (0.4, 0.0)]
    x, y = 0.0, 0.0
    longest = 0
    for tx, ty in corners:  # walk a square in 2 cm steps and pass the start again
        steps = int(round(math.hypot(tx - x, ty - y) / 0.02))
        for i in range(1, steps + 1):
            crumbs.update(Pose(x + (tx - x) * i / steps, y + (ty - y) * i / steps, 0.0))
            longest = max(longest, len(crumbs.points))
        x, y = tx, ty
    assert longest >= 12  # the square was recorded ...
    assert len(crumbs.points) <= 4  # ... and cut out when the path came back to the start
    assert crumbs.points[0] == (0.0, 0.0)


# ---------------- return-to-start ----------------
def test_return_to_start_in_the_open_is_direct() -> None:
    h = started(sim_cfg=SimEsp32Config(room=OPEN_ROOM, obstacles=()))
    assert h.run_until(lambda: math.hypot(h.sim.true.x, h.sim.true.y) > 2.5, 200)
    assert h.command("return_to_start")["accepted"] and h.brain.sup.state == "RETURNING"
    assert h.run_until(lambda: h.brain.sup.state == "IDLE", 120)
    done = last_event(h, "return_done")
    assert done["method"] == "direct" and done["simulated"] is True
    assert done["odom_distance_m"] <= h.cfg.arrival_tolerance_m
    assert "return_blocked" not in event_kinds(h)
    # the simulator knows its own truth: odometry drift included, it ends near the start
    assert math.hypot(h.sim.true.x, h.sim.true.y) < 0.5
    assert h.sim.speed == (0.0, 0.0) and not h.brain.sup.run_open
    assert h.brain.outbox[-1] == {"type": "run.end"}


def test_return_slows_down_near_the_target() -> None:
    cfg = Config()
    returner = Returner(cfg)
    returner.start([(0.0, 0.0)], Pose(-2.0, 0.0, 0.0))
    far = returner.step(Sense(Pose(-2.0, 0.0, 0.0), 0.0, (9.0, 9.0, 9.0), 9.0, 0.02))
    returner._rotate = False
    far = returner.step(Sense(Pose(-2.0, 0.0, 0.0), 0.0, (9.0, 9.0, 9.0), 9.0, 0.02))
    near = returner.step(Sense(Pose(-0.2, 0.0, 0.0), 0.0, (9.0, 9.0, 9.0), 9.0, 0.02))
    assert far[0] == cfg.roam_speed_mps and 0.08 <= near[0] < far[0]


def test_return_when_blocked_uses_breadcrumbs() -> None:
    h = started(Config(sim=True, sim_seed=2))  # default room with its three boxes
    sim = h.sim.cfg

    def direct_line_blocked() -> bool:
        p = h.sim.true
        dist = math.hypot(p.x, p.y)
        if dist < 1.5 or h.brain.roamer.phase != "forward":
            return False
        bearing = math.atan2(-p.y, -p.x)
        return range_along(p.x, p.y, bearing, sim.room, sim.obstacles) < dist - 0.3

    h.run(20)
    assert h.run_until(direct_line_blocked, 200)
    assert h.command("return_to_start")["accepted"]
    assert h.run_until(lambda: h.brain.sup.state == "IDLE", 300)
    kinds = event_kinds(h)
    assert kinds.index("return_start") < kinds.index("return_blocked") < kinds.index("return_done")
    assert last_event(h, "return_blocked")["fallback"] == "breadcrumbs"
    done = last_event(h, "return_done")
    assert done["method"] == "breadcrumbs"
    assert done["odom_distance_m"] <= h.cfg.arrival_tolerance_m
    assert math.hypot(h.sim.true.x, h.sim.true.y) < 0.6  # simulated truth, drift included
    assert h.sim.collisions == 0 and h.brain.rejected == []


def test_return_fails_with_timeout_and_sensor_fault() -> None:
    cfg = Config(return_timeout_s=1.0)
    returner = Returner(cfg)
    returner.start([(0.0, 0.0)], Pose(3.0, 0.0, 0.0))
    free = Sense(Pose(3.0, 0.0, 0.0), 0.0, (9.0, 9.0, 9.0), 9.0, 0.02)
    for _ in range(60):
        returner.step(free)
    assert returner.result is not None and returner.result[0] == "return_failed"
    assert returner.result[1]["reason"] == "timeout"

    returner = Returner(Config())
    returner.start([(0.0, 0.0)], Pose(3.0, 0.0, 0.0))
    returner.step(Sense(Pose(3.0, 0.0, 0.0), 0.0, (None, None, None), 9.0, 0.02))
    assert returner.result is not None and returner.result[1]["reason"] == "sensor_fault"


def test_return_fails_blocked_when_boxed_in() -> None:
    cfg = Config()
    returner = Returner(cfg)
    returner.start([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], Pose(3.0, 0.0, math.pi))
    boxed = Sense(Pose(3.0, 0.0, math.pi), 0.0, (0.3, 0.3, 0.3), 0.3, 0.02)
    outs = [returner.step(boxed) for _ in range(2000)]
    assert all(v == 0.0 for v, _ in outs)  # it never pushes forward into the obstacle
    assert returner.result is not None and returner.result[0] == "return_failed"
    assert returner.result[1]["reason"] == "blocked" and returner.method == "breadcrumbs"
    assert "return_blocked" in [k for k, _ in returner.events]
