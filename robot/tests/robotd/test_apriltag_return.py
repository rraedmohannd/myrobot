"""EXPERIMENTAL start-mark AprilTag correction of the return (S7), with pure kinematics.

The odometry is given a constant error (`drift`): the robot believes it is at `odom`, it really
is at `odom + drift`. The tag sighting is computed from the TRUE pose. Everything is simulated;
nothing here is a physical result.
"""

from __future__ import annotations

import math
import random

from scr_odometry import Pose

from robotd.config import Config, load_config
from robotd.navigation import Returner, Roamer, Sense, TagSighting, tag_from_vision

DT = 0.05
FREE = (math.inf, math.inf, math.inf)
TAG_TRUE = (-0.7, 0.3)  # where the tag stands, true frame (the start is the true origin)


def sighting(true: Pose, tag_id: int = 0, age_ms: float = 50.0) -> TagSighting:
    dx, dy = TAG_TRUE[0] - true.x, TAG_TRUE[1] - true.y
    return TagSighting(
        id=tag_id,
        bearing_rad=math.atan2(dy, dx) - true.theta,
        distance_m=math.hypot(dx, dy),
        age_ms=age_ms,
    )


def recorded(cfg: Config) -> Returner:
    """A returner that saw the tag at the start (true pose = odometry pose = origin)."""
    r = Returner(cfg)
    assert r.observe_start(Sense(Pose(0.0, 0.0, 0.0), 0.0, FREE, None, DT, sighting(Pose())))
    return r


def run_return(
    r: Returner,
    start: Pose,
    drift: tuple[float, float],
    *,
    see_tag: bool = True,
    tag_id: int = 0,
    age_ms: float = 50.0,
) -> tuple[list[tuple[float, float]], Pose, list[float]]:
    """Drive the return to its end. Returns (commands, final TRUE pose, odom distance from the
    origin at each apriltag_correction event)."""
    odom = start
    r.start([], odom)
    commands: list[tuple[float, float]] = []
    corrections: list[float] = []
    for _ in range(4000):
        true = Pose(odom.x + drift[0], odom.y + drift[1], odom.theta)
        tag = sighting(true, tag_id, age_ms) if see_tag else None
        before = len(r.events)
        v, w = r.step(Sense(odom, 0.0, FREE, None, DT, tag))
        for kind, _data in r.events[before:]:
            if kind == "apriltag_correction":
                corrections.append(math.hypot(odom.x, odom.y))
        commands.append((round(v, 9), round(w, 9)))
        if r.result is not None:
            break
        theta = odom.theta + w * DT
        odom = Pose(odom.x + v * math.cos(theta) * DT, odom.y + v * math.sin(theta) * DT, theta)
    assert r.result is not None
    true_end = Pose(odom.x + drift[0], odom.y + drift[1], odom.theta)
    return commands, true_end, corrections


def baseline(start: Pose, drift: tuple[float, float]) -> list[tuple[float, float]]:
    commands, _, _ = run_return(Returner(Config()), start, drift, see_tag=False)
    return commands


DRIFT = (0.25, -0.15)
START = Pose(2.5, 0.4, 0.3)


def kinds(r: Returner) -> list[str]:
    return [kind for kind, _ in r.events]


def test_off_by_default_and_identical_to_before() -> None:
    cfg = Config()
    assert cfg.apriltag_return is False
    r = Returner(cfg)
    assert not r.observe_start(Sense(Pose(), 0.0, FREE, None, DT, sighting(Pose())))
    commands, true_end, corrections = run_return(r, START, DRIFT)
    assert commands == baseline(START, DRIFT)
    assert corrections == [] and not any(k.startswith("apriltag") for k in kinds(r))
    assert r.result is not None and "apriltag_used" not in r.result[1]
    # without the correction the robot ends where the drifted odometry says the start is
    assert math.hypot(true_end.x, true_end.y) > 0.2


def test_enabled_corrects_the_final_approach_and_marks_it() -> None:
    cfg = Config(apriltag_return=True)
    r = recorded(cfg)
    _, true_end, corrections = run_return(r, START, DRIFT)
    assert r.result is not None and r.result[0] == "return_done"
    assert math.hypot(true_end.x, true_end.y) <= cfg.arrival_tolerance_m + 0.02
    assert corrections, "a correction event must be emitted"
    events = [d for k, d in r.events if k == "apriltag_correction"]
    assert all(d["experimental"] is True for d in events)
    assert r.result[1]["apriltag_used"] is True
    assert kinds(r)[0] == "apriltag_start_recorded"


def test_correction_only_in_the_final_approach() -> None:
    cfg = Config(apriltag_return=True)
    r = recorded(cfg)
    _, _, corrections = run_return(r, START, DRIFT)
    assert corrections and max(corrections) <= cfg.apriltag_final_approach_m
    # outside the final approach the commands are those of the plain return
    commands_on, _, _ = run_return(recorded(cfg), START, DRIFT)
    plain = baseline(START, DRIFT)
    odom_far = 0
    odom = START
    for v, w in plain:  # how many steps the plain return spends beyond 1.5 m
        if math.hypot(odom.x, odom.y) <= cfg.apriltag_final_approach_m:
            break
        theta = odom.theta + w * DT
        odom = Pose(odom.x + v * math.cos(theta) * DT, odom.y + v * math.sin(theta) * DT, theta)
        odom_far += 1
    assert odom_far > 10
    assert commands_on[:odom_far] == plain[:odom_far]


def test_stale_wrong_id_or_inconsistent_sightings_change_nothing() -> None:
    cfg = Config(apriltag_return=True)
    plain = baseline(START, DRIFT)
    stale = recorded(cfg)
    assert run_return(stale, START, DRIFT, age_ms=1000.0)[0] == plain
    wrong = recorded(cfg)
    assert run_return(wrong, START, DRIFT, tag_id=9)[0] == plain
    big_drift = (0.9, 0.4)  # more than apriltag_max_correction_m: not believed
    odd = recorded(cfg)
    assert run_return(odd, START, big_drift)[0] == baseline(START, big_drift)
    unseen = recorded(cfg)
    assert run_return(unseen, START, DRIFT, see_tag=False)[0] == plain
    for r in (stale, wrong, odd, unseen):
        assert "apriltag_correction" not in kinds(r)
        assert r.result is not None and "apriltag_used" not in r.result[1]


def test_no_start_record_no_correction() -> None:
    cfg = Config(apriltag_return=True)
    r = Returner(cfg)  # the tag was never seen at the start
    assert run_return(r, START, DRIFT)[0] == baseline(START, DRIFT)
    far = Returner(cfg)  # seen, but far from the start: not recorded
    away = Pose(1.0, 0.0, 0.0)
    assert not far.observe_start(Sense(away, 0.0, FREE, None, DT, sighting(away)))
    cleared = recorded(cfg)
    cleared.clear_start_tag()
    assert run_return(cleared, START, DRIFT)[0] == baseline(START, DRIFT)


def test_never_used_while_roaming() -> None:
    cfg = Config(apriltag_return=True)
    pose = Pose(0.5, 0.2, 0.4)
    with_tag, without = Roamer(cfg, random.Random(3)), Roamer(cfg, random.Random(3))
    a = Sense(pose, 1.0, FREE, None, DT, sighting(pose))
    b = Sense(pose, 1.0, FREE, None, DT)
    with_tag.reset(a)
    without.reset(b)
    for i in range(50):
        a.travelled_m = b.travelled_m = 1.0 + i * 0.01
        assert with_tag.step(a) == without.step(b)
    assert with_tag.events == without.events
    # recording the start sighting only records; it moves nothing and corrects nothing
    r = Returner(cfg)
    r.observe_start(Sense(Pose(), 0.0, FREE, None, DT, sighting(Pose())))
    assert kinds(r) == ["apriltag_start_recorded"]


def test_tag_from_vision() -> None:
    msg = {"type": "vision.state", "start_tag": {"id": 0, "center_x": 0.5, "bearing_deg": 10.0,
                                                  "distance_m": 1.2, "age_ms": 100,
                                                  "experimental": True}}  # fmt: skip
    tag = tag_from_vision(msg, 0.2)
    assert tag is not None and tag.id == 0
    assert math.isclose(tag.bearing_rad, math.radians(10.0))
    assert tag.age_ms == 300.0  # visiond's age + the message's age
    assert tag_from_vision({"type": "vision.state"}, 0.0) is None
    assert tag_from_vision(None, 0.0) is None
    bad = {"start_tag": {"id": "0", "bearing_deg": 1.0, "distance_m": 1.0, "age_ms": 0}}
    assert tag_from_vision(bad, 0.0) is None
    nan = {"start_tag": {"id": 0, "bearing_deg": float("nan"), "distance_m": 1.0, "age_ms": 0}}
    assert tag_from_vision(nan, 0.0) is None


def test_flag_from_env_and_marked_in_odom_config() -> None:
    assert load_config(env={}).apriltag_return is False
    cfg = load_config(env={"ROBOTD_APRILTAG_RETURN": "1", "ROBOTD_APRILTAG_ID": "4"})
    assert cfg.apriltag_return is True and cfg.apriltag_id == 4
    assert "EXPERIMENTAL" in cfg.odom_config(False)["apriltag_return"]
    assert "apriltag_return" not in Config().odom_config(False)
