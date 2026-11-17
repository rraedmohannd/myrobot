"""The calibration file (sprint 5 interface B): load, save, migrate, hash, status."""

import json
from pathlib import Path

import pytest
from scr_serial import load_table, param_hash

from robotd import calibration as calib
from robotd.calibration import Calibration, CalibrationError, CalibrationMissing

TABLE = load_table()


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_missing_file_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CalibrationMissing, match="no calibration file"):
        calib.load(tmp_path / "nope.json")


def test_save_then_load_round_trip_with_history(tmp_path: Path) -> None:
    cal = Calibration(robot_id="rover5-1")
    cal.set("METRES_PER_TICK", 0.000512, operator="Mohannd", method="distance-cal: 3 m tape",
            raw={"runs": [1, 2], "mean": 0.000512})  # fmt: skip
    cal.set("track_width_m", 0.2, operator="Mohannd", method="rotate-test")
    path = calib.save(cal, tmp_path / "sub" / "calibration.json")
    assert path.is_file() and not list(path.parent.glob("*.tmp"))  # atomic: no leftovers
    back = calib.load(path)
    assert back.robot_id == "rover5-1" and back.is_simulated is False
    assert back.value("METRES_PER_TICK") == 0.000512
    assert back.values["METRES_PER_TICK"]["unit"] == "m/tick"
    assert back.values["METRES_PER_TICK"]["raw"]["mean"] == 0.000512
    assert [h["key"] for h in back.history] == ["METRES_PER_TICK", "track_width_m"]
    back.set("METRES_PER_TICK", 0.0005, operator="Mohannd", method="again")
    calib.save(back)
    again = calib.load(path)
    assert again.history[-1]["old"] == 0.000512 and again.history[-1]["new"] == 0.0005
    assert len(again.history) == 3
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "scr-calibration"


def test_save_keeps_history_written_by_someone_else(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    a = Calibration()
    a.set("GYRO_SCALE", 1.01, operator="A")
    calib.save(a, path)
    b = Calibration()  # an older copy in memory, without A's history
    b.set("track_width_m", 0.2, operator="B")
    calib.save(b, path)
    keys = [h["key"] for h in calib.load(path).history]
    assert keys == ["GYRO_SCALE", "track_width_m"]


def test_unknown_schema_newer_and_unmigratable_versions_are_refused(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="schema"):
        calib.load(_write(tmp_path / "a.json", {"schema": "other", "version": 1}))
    with pytest.raises(CalibrationError, match="newer robotd"):
        calib.load(_write(tmp_path / "b.json", {"schema": "scr-calibration", "version": 9}))
    with pytest.raises(CalibrationError, match="too old"):
        calib.load(_write(tmp_path / "c.json", {"schema": "scr-calibration", "version": -1}))
    with pytest.raises(CalibrationError, match="not valid JSON"):
        (tmp_path / "d.json").write_text("{", encoding="utf-8")
        calib.load(tmp_path / "d.json")


def test_old_version_is_migrated(tmp_path: Path) -> None:
    path = _write(tmp_path / "old.json", {
        "schema": "scr-calibration", "version": 0, "values": {"METRES_PER_TICK": 0.0005},
    })  # fmt: skip
    cal = calib.load(path)
    assert cal.migrated_from == 0 and cal.version == 1
    assert cal.value("METRES_PER_TICK") == 0.0005


def test_out_of_range_and_unknown_keys_are_refused(tmp_path: Path) -> None:
    bad = {"schema": "scr-calibration", "version": 1, "values": {"METRES_PER_TICK": {"value": 1}}}
    with pytest.raises(CalibrationError, match="outside"):
        calib.load(_write(tmp_path / "a.json", bad))
    with pytest.raises(CalibrationError, match="not a firmware parameter"):
        Calibration().set("NO_SUCH_PARAM", 1.0)
    with pytest.raises(CalibrationError, match="ascending"):
        Calibration().set("battery_table", [[12.0, 100], [10.0, 0]])
    with pytest.raises(CalibrationError, match="outside"):
        Calibration().set("MOTOR_INVERT_LEFT", 0.5)  # a bool parameter


def test_firmware_params_expected_hash_and_status() -> None:
    cal = Calibration()
    assert calib.status(cal) == "none"
    assert calib.expected_hash(cal) == param_hash(TABLE.defaults())
    cal.set("METRES_PER_TICK", 0.0005)
    cal.set("track_width_m", 0.19)
    mpt = TABLE.by_name("METRES_PER_TICK").id
    assert calib.firmware_params(cal) == {mpt: 0.0005}  # Pi-only keys are not pushed
    assert calib.expected_hash(cal) == param_hash({**TABLE.defaults(), mpt: 0.0005})
    assert calib.status(cal) == "partial"
    assert "PID_KP" in calib.missing_keys(cal)
    sim = calib.simulated()
    assert sim.is_simulated and calib.status(sim) == "complete"
    assert calib.status_dict(sim) == {"status": "complete", "updated": None, "is_simulated": True}


def test_report_message_is_valid_for_the_backend_contract() -> None:
    from scr_protocol import parse_robot_message

    cal = calib.simulated()
    cal.set("battery_table", [[9.0, 0], [12.6, 100]], method="placeholder")
    report = cal.report()
    parse_robot_message(report)
    assert report["is_simulated"] is True and report["values"]["battery_table"]["value"]
    assert calib.battery_table(cal) == [(9.0, 0.0), (12.6, 100.0)]
