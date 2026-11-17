"""robotctl motor-test, encoder-check, distance-cal, rotate-test, pid-step, square-test against
the SIMULATED ESP32: confirmations, refusals, the safety exit, and what lands in the
calibration file (always `is_simulated: true`)."""

import json
import math
from pathlib import Path

from robotctl_helpers import Runner, calibrated_sim, fake_operator
from scr_serial import Bringup, BringupKind, Drive, DriveMode, Mode, ParamSet, Stop

from robotctl.device import SimPipeDevice
from robotctl.session import RAISE_CAPS
from robotd.sim_esp32 import SimEsp32, SimEsp32Config

TRACKS = "tracks off the ground"


def cal(run: Runner) -> dict:
    return json.loads(run.cal.read_text(encoding="utf-8"))


def mode_safe_last(dev: SimPipeDevice) -> bool:
    modes = [m for m in dev.sent if isinstance(m, Mode)]
    return bool(modes) and modes[-1].mode == DriveMode.SAFE


# ------------------------------------------------------------------ motor-test
async def test_motor_test_refuses_without_confirmation_and_moves_nothing(run: Runner) -> None:
    code = await run(["motor-test", "--side", "left"])
    assert code == 2
    assert "REFUSED" in run.text and TRACKS in run.text
    assert run.dev is not None and run.sim is not None
    assert not any(isinstance(m, Drive | ParamSet) for m in run.dev.sent)
    assert not any(isinstance(m, Mode) and m.mode == DriveMode.TEST for m in run.sim.received)
    assert not run.cal.exists()


async def test_motor_test_refuses_wrong_phrase_and_bad_duty(run: Runner) -> None:
    code = await run(["motor-test", "--side", "left", "--yes-i-confirm", "tracks on the floor"])
    assert code == 2 and "does not match" in run.text
    code = await run(["motor-test", "--side", "left", "--duty", "0.9", "--yes-i-confirm", TRACKS])
    assert code == 2 and "--duty" in run.text


async def test_motor_test_needs_the_caps_confirmation_on_a_fresh_firmware(run: Runner) -> None:
    code = await run(["motor-test", "--side", "left", "--yes-i-confirm", TRACKS])
    assert code == 2 and RAISE_CAPS in run.text
    assert run.dev is not None
    assert not any(isinstance(m, Drive | ParamSet) for m in run.dev.sent)


async def test_motor_test_left_ok_writes_simulated_calibration(run: Runner) -> None:
    code = await run([
        "motor-test", "--side", "left", "--yes-i-confirm", TRACKS, "--yes-i-confirm", RAISE_CAPS,
        "--track-moved", "forward", "--accept", "--save-params",
    ])  # fmt: skip
    assert code == 0, run.text
    assert "[SIMULATED]" in run.text and "SIMULATED ESP32" in run.text
    data = cal(run)
    assert data["is_simulated"] is True
    values = data["values"]
    assert values["MOTOR_INVERT_LEFT"]["value"] == 0
    assert values["ENCODER_INVERT_FL"]["value"] == 0
    assert values["ENCODER_INVERT_RL"]["value"] == 0
    assert values["MOTOR_INVERT_LEFT"]["method"].startswith("SIMULATED")
    assert values["MOTOR_INVERT_LEFT"]["raw"]["deltas"][0] > 100
    sim = run.sim
    assert sim is not None and run.dev is not None
    assert sim.p("TEST_MAX_DUTY") == 0.0 and sim.p("RAMP_MM_S2") == 0.0  # caps restored
    assert sim.mode == DriveMode.SAFE and mode_safe_last(run.dev)
    log = (run.cal.parent / "calibration-log.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["is_simulated"] is True


async def test_motor_test_finds_inverted_motor_and_encoder(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(
        motor_wiring_inverted=(True, False),
        encoder_wiring_inverted=(True, False, False, False),
    ))  # fmt: skip
    code = await run([
        "motor-test", "--side", "left", "--yes-i-confirm", TRACKS, "--yes-i-confirm", RAISE_CAPS,
        "--track-moved", "backward", "--accept", "--no-save-params",
    ], sim=sim)  # fmt: skip
    assert code == 0, run.text
    values = cal(run)["values"]
    assert values["MOTOR_INVERT_LEFT"]["value"] == 1
    assert values["ENCODER_INVERT_FL"]["value"] == 1
    assert values["ENCODER_INVERT_RL"]["value"] == 0
    assert sim.p("MOTOR_INVERT_LEFT") == 1 and sim.p("ENCODER_INVERT_FL") == 1
    assert sim.nvs == {}  # not saved: the operator said no


async def test_motor_test_reports_a_dead_track(run: Runner) -> None:
    code = await run([
        "motor-test", "--side", "right", "--yes-i-confirm", TRACKS, "--yes-i-confirm", RAISE_CAPS,
        "--track-moved", "none",
    ])  # fmt: skip
    assert code == 4 and "did not move" in run.text
    assert not run.cal.exists()


class Boom(RuntimeError):
    pass


async def test_safety_exit_on_an_exception_while_driving(run: Runner) -> None:
    """An exception in the middle of a drive still ends with STOP, bring-up OFF and MODE SAFE."""
    sim = SimEsp32()

    class FailingDevice(SimPipeDevice):
        drives = 0

        async def send(self, msg):  # type: ignore[no-untyped-def]
            if isinstance(msg, Drive):
                self.drives += 1
                if self.drives == 5:
                    raise Boom("simulated failure in the middle of a drive")
            await super().send(msg)

    dev = FailingDevice(sim)
    from robotctl import cli

    lines: list[str] = []
    try:
        await cli.run([
            "motor-test", "--side", "left", "--yes-i-confirm", TRACKS, "--yes-i-confirm",
            RAISE_CAPS, "--track-moved", "forward", "--non-interactive",
            "--calibration", str(run.cal),
        ], device=dev, out=lines.append)  # fmt: skip
    except Boom:
        pass
    else:
        raise AssertionError("the exception must not be swallowed")
    after = dev.sent[[i for i, m in enumerate(dev.sent) if isinstance(m, Drive)][-1] :]
    assert any(isinstance(m, Stop) for m in after)
    assert any(isinstance(m, Bringup) and m.kind == BringupKind.OFF for m in after)
    assert mode_safe_last(dev)
    assert sim.mode == DriveMode.SAFE and sim.target == (0.0, 0.0)
    assert sim.p("TEST_MAX_DUTY") == 0.0  # the temporary caps were put back
    assert not run.cal.exists()


# ------------------------------------------------------------------ encoder-check
def push(side_by_label: dict[str, str], metres_per_step: float = 0.001):
    def hand(sim: SimEsp32, _t: float, label: str) -> None:
        side = side_by_label.get(label)
        if side:
            sim.hand_push(side, metres_per_step)

    return hand


async def test_encoder_check_all_count_up(run: Runner) -> None:
    hand = push({"push-left": "left", "push-right": "right"})
    code = await run(["encoder-check", "--hand-secs", "1.0"], hand=hand)
    assert code == 0, run.text
    assert "every encoder counts up" in run.text
    assert run.sim is not None and run.sim.mode == DriveMode.SAFE


async def test_encoder_check_proposes_an_inversion(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(encoder_wiring_inverted=(False, False, False, True)))
    hand = push({"push-left": "left", "push-right": "right"})
    code = await run(["encoder-check", "--hand-secs", "1.0", "--accept", "--no-save-params"],
                     sim=sim, hand=hand)  # fmt: skip
    assert code == 0, run.text
    assert cal(run)["values"]["ENCODER_INVERT_RR"]["value"] == 1
    assert sim.p("ENCODER_INVERT_RR") == 1


async def test_encoder_check_finds_swapped_cables(run: Runner) -> None:
    hand = push({"push-left": "right", "push-right": "left"})  # pushing left moves the right
    code = await run(["encoder-check", "--hand-secs", "1.0"], hand=hand)
    # a check that found a problem: exit 4 (BRINGUP.md exit codes), not 5 (nothing was driven)
    assert code == 4 and "swapped" in run.text and "STOPPED" not in run.text


# ------------------------------------------------------------------ distance-cal
async def test_distance_cal_pushed(run: Runner) -> None:
    def both(sim: SimEsp32, t: float, label: str) -> None:
        if label == "push-forward":
            sim.hand_push("left", 0.03)
            sim.hand_push("right", 0.03)

    code = await run(
        ["distance-cal", "--runs", "2", "--hand-secs", "1.0", "--measured", "3.0,3.0",
         "--accept", "--no-save-params"], hand=both,
    )  # fmt: skip
    assert code == 0, run.text
    entry = cal(run)["values"]["METRES_PER_TICK"]
    assert math.isclose(entry["value"], 0.0005, rel_tol=0.01)
    assert len(entry["raw"]["runs"]) == 2 and "spread" in entry["raw"]
    assert run.sim is not None and math.isclose(run.sim.p("METRES_PER_TICK"), entry["value"],
                                                rel_tol=1e-6)  # fmt: skip


async def test_distance_cal_measured_out_of_range_is_refused(run: Runner) -> None:
    def both(sim: SimEsp32, t: float, label: str) -> None:
        if label == "push-forward":
            sim.hand_push("left", 0.03)
            sim.hand_push("right", 0.03)

    code = await run(["distance-cal", "--runs", "1", "--hand-secs", "1.0", "--measured", "30"],
                     hand=both)  # fmt: skip
    assert code == 2 and "outside" in run.text
    assert not run.cal.exists()


async def test_distance_cal_driven_with_typed_tape_value(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(obstacles=()))
    start = sim.true.x
    answer = fake_operator({"distance on the tape": lambda: f"{sim.true.x - start:.4f}"})
    code = await run(["distance-cal", "--runs", "1", "--drive", "--drive-secs", "4"],
                     sim=sim, input_fn=answer)  # fmt: skip
    assert code == 0, run.text
    value = cal(run)["values"]["METRES_PER_TICK"]["value"]
    assert math.isclose(value, 0.0005, rel_tol=0.03)  # SIMULATED slip of about 1 %
    assert sim.mode == DriveMode.SAFE and sim.p("TEST_MAX_DUTY") == 0.0


# ------------------------------------------------------------------ rotate-test
def turning_hand(rate_rps: float, track_width: float = 0.19):
    """Stands in for the operator turning the robot by hand (SIMULATED)."""
    state = {"on": False, "patched": False}

    def hand(sim: SimEsp32, _t: float, label: str) -> None:
        if not state["patched"]:
            original = sim._track_speeds

            def speeds(dt: float, v: float) -> tuple[float, float]:
                if state["on"]:
                    state["on"] = False
                    return (-rate_rps * track_width / 2, rate_rps * track_width / 2)
                return original(dt, v)

            sim._track_speeds = speeds  # type: ignore[method-assign]
            state["patched"] = True
        state["on"] = label == "rotate-ccw"

    return hand


async def test_rotate_test_by_hand_finds_the_gyro_scale(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(gyro_scale_true=1.05, slip_turn=0.0, gyro_noise_rps=0.002))
    code = await run(["rotate-test", "--runs", "3", "--hand-secs", "2.0", "--accept",
                      "--no-save-params"], sim=sim, hand=turning_hand(math.pi))  # fmt: skip
    assert code == 0, run.text
    entry = cal(run)["values"]["GYRO_SCALE"]
    assert math.isclose(entry["value"], 1 / 1.05, rel_tol=0.01)
    assert entry["raw"]["source"] == "IMU_RAW" and len(entry["raw"]["runs"]) == 3
    assert math.isclose(sim.p("GYRO_SCALE"), entry["value"], rel_tol=1e-6)


async def test_rotate_test_driven_gives_track_width(run: Runner) -> None:
    sim = calibrated_sim(slip_turn=0.0, obstacles=())
    code = await run([
        "rotate-test", "--runs", "1", "--driven", "--duty", "0.2", "--measured-deg", "360",
        "--yes-i-confirm", "robot on the floor and 1 m clear around it", "--accept",
        "--no-save-params",
    ], sim=sim)  # fmt: skip
    assert code == 0, run.text
    width = cal(run)["values"]["track_width_m"]["value"]
    assert math.isclose(width, 0.19, rel_tol=0.05)
    assert sim.mode == DriveMode.SAFE


# ------------------------------------------------------------------ pid-step / square-test
async def test_pid_step_refused_without_distance_calibration(run: Runner) -> None:
    code = await run(["pid-step", "--yes-i-confirm", TRACKS])
    assert code == 2 and "distance-cal" in run.text
    assert run.dev is not None and not any(isinstance(m, Drive) for m in run.dev.sent)


async def test_pid_step_writes_csv_and_proposes_gains(run: Runner, tmp_path: Path) -> None:
    sim = calibrated_sim()
    code = await run(["pid-step", "--yes-i-confirm", TRACKS, "--accept", "--no-save-params",
                      "--out-dir", str(tmp_path / "pid")], sim=sim)  # fmt: skip
    assert code == 0, run.text
    csvs = list((tmp_path / "pid").glob("pid-step-*.csv"))
    assert len(csvs) == 1 and csvs[0].read_text().startswith("t_s,target_mps")
    assert "PID_KP" in cal(run)["values"]
    assert sim.mode == DriveMode.SAFE


async def test_pid_step_trial_gains_are_restored_when_rejected(run: Runner, tmp_path: Path
                                                               ) -> None:  # fmt: skip
    sim = calibrated_sim()
    code = await run(["pid-step", "--kp", "2.5", "--yes-i-confirm", TRACKS, "--reject",
                      "--out-dir", str(tmp_path)], sim=sim)  # fmt: skip
    assert code == 0, run.text
    assert sim.p("PID_KP") == 1.0  # back to the value before the trial
    assert not run.cal.exists()


async def test_square_test_records_the_typed_closure(run: Runner) -> None:
    sim = calibrated_sim(obstacles=(), room=(-5.0, -5.0, 5.0, 5.0))
    code = await run([
        "square-test", "--side", "1.0", "--measured", "0.12,0.08", "--hand-secs", "0.1",
        "--yes-i-confirm", "floor clear for the square",
    ], sim=sim)  # fmt: skip
    assert code == 0, run.text
    entry = cal(run)["values"]["square_test_closure_m"]
    assert entry["value"] == {"cw": 0.12, "ccw": 0.08}
    assert "odometry_closure_m" in entry["raw"]["results"]["cw"]
    assert sim.mode == DriveMode.SAFE
