"""The SIMULATED vision client (scripts/fake_vision.py) speaks visiond's `vision.state` contract
and makes the brain engage. Everything here is simulation (CLAUDE.md rule 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import fake_vision  # noqa: E402

from robotd.supervisor import Facts, Supervisor  # noqa: E402
from visiond.messages import FACE_KEYS, STATE_KEYS  # noqa: E402

ENGAGED_FROM = fake_vision.APPEAR_S + fake_vision.FACING_S


def test_every_scripted_message_has_exactly_the_contract_fields() -> None:
    for i in range(0, 200):
        _, state = fake_vision.scripted_state(i * 0.1, hold_s=12.0)
        assert set(state) == STATE_KEYS
        assert state["type"] == "vision.state" and state["camera_ok"] is True
        for face in state["faces"]:
            assert set(face) == FACE_KEYS
            assert len(face["box"]) == 4 and all(0.0 <= v <= 1.0 for v in face["box"])


def test_script_phases_engage_only_after_facing_long_enough_then_leave() -> None:
    phases = [fake_vision.scripted_state(t, hold_s=12.0) for t in (0.5, 1.5, 3.0, 13.0, 15.0, 17.0)]
    assert [p for p, _ in phases] == ["appear", "facing", "engaged", "engaged", "leave", "done"]
    assert [s["engaged"] for _, s in phases] == [False, False, True, True, False, False]
    assert phases[0][1]["faces"][0]["facing"] is False
    assert phases[1][1]["faces"][0]["facing"] is True
    assert phases[4][1]["faces"] == [] and phases[4][1]["selected_id"] is None


def test_servo_target_moves_both_ways_while_engaged() -> None:
    targets = [
        fake_vision.scripted_state(ENGAGED_FROM + u * 0.1, hold_s=12.0)[1]["servo_target_deg"]
        for u in range(0, 120)
    ]
    assert targets[0] == 0.0
    assert max(targets) > 15 and min(targets) < -10


def test_the_supervisor_engages_on_the_script_and_not_before() -> None:
    sup = Supervisor()
    sup.state = "ROAMING"
    kinds: list[tuple[float, str]] = []
    for i in range(0, 40):
        t = i * 0.1
        _, state = fake_vision.scripted_state(t, hold_s=12.0)
        for action in sup.update(Facts(now_s=100.0 + t, vision_engaged=state["engaged"])):
            kinds.append((t, action.kind))
    assert sup.state == "ENGAGED"
    assert [k for _, k in kinds] == ["stop", "engage"]
    assert kinds[0][0] >= ENGAGED_FROM
