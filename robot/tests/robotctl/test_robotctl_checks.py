"""robotctl status, diag, param, ultrasonic-check, battery-cal, gyro-cal, servo-cal, hfov-cal
against the SIMULATED ESP32 (labelled; nothing here is a measurement)."""

import json
import math

from robotctl_helpers import Runner, calibrated_sim
from scr_serial import AckStatus, BringupKind, DriveMode

from robotctl.calstore import CalStore
from robotctl.device import SimPipeDevice
from robotctl.operator import Operator
from robotctl.session import Session, StepError
from robotd import calibration
from robotd.sim_esp32 import SimEsp32, SimEsp32Config


def values(run: Runner) -> dict:
    return json.loads(run.cal.read_text(encoding="utf-8"))["values"]


async def test_status_is_labelled_simulated_and_logged(run: Runner) -> None:
    code = await run(["status"])
    assert code == 0, run.text
    assert "SIMULATED ESP32" in run.text and "[SIMULATED]   encoders" in run.text
    assert "firmware 0.5.0" in run.text
    log = (run.cal.parent / "calibration-log.jsonl").read_text(encoding="utf-8")
    assert '"step": "status"' in log and '"is_simulated": true' in log


async def test_diag_expects_the_mcp23017_by_default(run: Runner) -> None:
    code = await run(["diag"])  # the default simulated scan has 0x40 and 0x68 only
    assert code == 4
    assert "MISSING I2C expander (MCP23017, option B) 0x20" in run.text
    assert "PINS_CONFIRMED is off" in run.text


async def test_diag_option_a_and_full_scan(run: Runner) -> None:
    code = await run(["diag", "--no-expander"])
    assert "OK      IMU" in run.text and "OK      INA219" in run.text
    assert "expander" not in run.text.split("I2C scan")[1].split("\n")[1]
    sim = SimEsp32(SimEsp32Config(i2c_addresses=(0x20, 0x40, 0x69, 0x3C)))
    run.lines.clear()
    code = await run(["diag", "--expander", "0x20"], sim=sim)
    assert code == 4
    assert "OK      I2C expander" in run.text and "UNEXPECTED: 0x3C" in run.text


async def test_diag_reads_option_a_from_the_calibration_file(run: Runner) -> None:
    cal = calibration.Calibration(robot_id="t", is_simulated=True)
    cal.set("expander_addr", 0, method="pin plan option A")
    calibration.save(cal, run.cal)
    await run(["diag"])
    assert "(option A: no expander)" in run.text


async def test_param_set_refuses_out_of_range_and_sets_with_confirmation(run: Runner) -> None:
    code = await run(["param", "set", "METRES_PER_TICK", "0.5"])
    assert code == 2 and "outside" in run.text
    code = await run(["param", "set", "OBSTACLE_THRESHOLD_MM", "300",
                      "--yes-i-confirm", "set OBSTACLE_THRESHOLD_MM", "--accept",
                      "--save-params"])  # fmt: skip
    assert code == 0, run.text
    assert run.sim is not None and run.sim.p("OBSTACLE_THRESHOLD_MM") == 300
    assert run.sim.nvs[27] == 300  # PARAM_SAVE in SAFE mode
    assert values(run)["OBSTACLE_THRESHOLD_MM"]["value"] == 300


async def test_param_set_cannot_go_below_the_compiled_bound(run: Runner) -> None:
    code = await run(["param", "set", "OBSTACLE_THRESHOLD_MM", "100",
                      "--yes-i-confirm", "set OBSTACLE_THRESHOLD_MM", "--accept"])  # fmt: skip
    assert code == 2 and "outside" in run.text
    assert run.sim is not None and run.sim.p("OBSTACLE_THRESHOLD_MM") == 250


async def test_bringup_is_refused_outside_test_mode(tmp_path) -> None:  # noqa: ANN001
    sim = SimEsp32()
    dev = SimPipeDevice(sim)
    s = Session(dev, Operator(False), CalStore(tmp_path / "c.json", "t"), out=lambda _t: None)
    await dev.wait_telemetry()
    try:
        await s.bringup(BringupKind.IMU_RAW, 100)
    except StepError as exc:
        assert AckStatus.REJECTED_MODE.name in str(exc)
    else:
        raise AssertionError("BRINGUP outside MODE TEST must be refused")


async def test_ultrasonic_check_box_in_front(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(obstacles=((0.6, -0.5, 0.8, 0.5),)))
    code = await run(["ultrasonic-check", "--distance", "0.60", "--sensors", "1"], sim=sim)
    assert code == 0, run.text
    entry = values(run)["ultrasonic_check_mm"]
    assert abs(entry["value"]["front-centre"]) < 20
    assert entry["raw"]["table"][3]["checked"] is False
    assert "US_RAW" in run.text and sim.mode == DriveMode.SAFE


async def test_ultrasonic_check_wrong_distance_fails(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(obstacles=((0.6, -0.5, 0.8, 0.5),)))
    code = await run(["ultrasonic-check", "--distance", "0.40", "--sensors", "1"], sim=sim)
    assert code == 4 and "FAIL" in run.text


async def test_battery_cal(run: Runner) -> None:
    sim = SimEsp32(SimEsp32Config(ina_voltage_error=0.98))
    code = await run(["battery-cal", "--multimeter", "11.80", "--accept", "--no-save-params"],
                     sim=sim)  # fmt: skip
    assert code == 0, run.text
    assert math.isclose(values(run)["BATTERY_VOLTAGE_SCALE"]["value"], 1 / 0.98, rel_tol=0.003)


async def test_battery_cal_out_of_range_multimeter(run: Runner) -> None:
    code = await run(["battery-cal", "--multimeter", "20"])
    assert code == 2 and "outside" in run.text


async def test_gyro_cal(run: Runner) -> None:
    code = await run(["gyro-cal"])
    assert code == 0, run.text
    entry = values(run)["gyro_bias_rad_s"]
    assert math.isclose(entry["value"], 0.001, abs_tol=0.0015)  # simulated bias + noise
    assert entry["method"].startswith("SIMULATED")


async def test_servo_cal_with_keys(run: Runner) -> None:
    code = await run([
        "servo-cal", "--keys", "c +450 a +300 l -1350 r q", "--yes-i-confirm",
        "camera mount clear", "--accept", "--no-save-params",
    ])  # fmt: skip
    assert code == 0, run.text
    v = {k: e["value"] for k, e in values(run).items()}
    assert v["SERVO_CENTER_PULSE_US"] == 1500 and math.isclose(v["SERVO_US_PER_DEG"], 10.0)
    assert v["SERVO_INVERT"] == 0
    assert v["SERVO_MAX_CDEG"] == 7200 and v["SERVO_MIN_CDEG"] == -5700
    assert v["SERVO_PULSE_MIN_US"] == 900 and v["SERVO_PULSE_MAX_US"] == 2250
    assert v["SERVO_SLEW_CDEG_PER_S"] == 9000


async def test_servo_cal_refuses_without_confirmation_or_marks(run: Runner) -> None:
    assert await run(["servo-cal", "--keys", "c +100 a q"]) == 2
    assert run.sim is not None and run.sim.servo_pulse_us == 1500  # nothing moved
    code = await run(["servo-cal", "--keys", "c +100 a q", "--yes-i-confirm",
                      "camera mount clear"])  # fmt: skip
    assert code == 2 and "missing: l, r" in run.text


async def test_hfov_cal(run: Runner) -> None:
    assert await run(["hfov-cal", "--edge-angles", "30,-31"]) == 2  # servo not calibrated
    code = await run(["hfov-cal", "--edge-angles", "30,-31", "--accept"], sim=calibrated_sim())
    assert code == 0, run.text
    assert values(run)["camera_hfov_deg"]["value"] == 61.0


async def test_hfov_cal_from_vision_data(run: Runner) -> None:
    """A SIMULATED visiond: the face's image x follows the pan angle (hfov 60 deg)."""
    from robotctl.steps.servo import fit_hfov

    points = [(a, 0.5 + a / 60.0) for a in range(-20, 21, 2)]
    assert math.isclose(fit_hfov(points), 60.0)


async def test_simulated_result_never_goes_into_a_real_file(run: Runner) -> None:
    real = calibration.Calibration(robot_id="rover5-1", is_simulated=False)
    real.set("METRES_PER_TICK", 0.000512, method="distance-cal", operator="Mohannd")
    calibration.save(real, run.cal)
    before = run.cal.read_text(encoding="utf-8")
    code = await run(["gyro-cal"])
    assert code == 6 and "refusing to write a SIMULATED result" in run.text
    assert run.cal.read_text(encoding="utf-8") == before


async def test_diag_expect_version_and_records_the_option(run: Runner) -> None:
    assert await run(["diag", "--expect-version", "0.5.0"]) == 0  # problems reported, version ok
    assert "SAME" in run.text
    assert await run(["diag", "--expect-version", "1.0.0"]) == 4
    await run(["diag", "--no-expander"])
    assert values(run)["expander_addr"]["value"] == 0
