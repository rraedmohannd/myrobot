"""Tests of firmware/tools/pins_from_table.py (docs/SPRINT_5.md S1.2).

Run from the repository root:  robot/.venv/Scripts/python -m pytest firmware/tools/tests -q
The fixture tables are TEST DATA, not the robot's wiring.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_DIR = TOOLS_DIR.parents[1]
PINS_H = REPO_DIR / "firmware" / "include" / "pins.h"
HARDWARE_MD = REPO_DIR / "docs" / "HARDWARE.md"

_spec = importlib.util.spec_from_file_location("pins_from_table", TOOLS_DIR / "pins_from_table.py")
assert _spec and _spec.loader
pft = importlib.util.module_from_spec(_spec)
sys.modules["pins_from_table"] = pft
_spec.loader.exec_module(pft)


def run(tmp_path: Path, fixture: str, *extra: str) -> tuple[int, Path]:
    out = tmp_path / "pins_generated.h"
    code = pft.main(["--table", str(FIXTURES / fixture), "--pins-h", str(PINS_H), "--out", str(out), *extra])
    return code, out


def pins_h_names() -> list[str]:
    text = PINS_H.read_text(encoding="utf-8")
    return re.findall(r"^#define\s+(PIN_[A-Z0-9_]+)\s+PIN_UNASSIGNED", text, re.MULTILINE)


def test_pins_h_lists_25_signals() -> None:
    names = pins_h_names()
    assert len(names) == 25
    assert pft.pin_names_from_pins_h(PINS_H.read_text(encoding="utf-8")) == names


def test_good_table_writes_header_with_exactly_the_pins_h_names(tmp_path: Path, capsys) -> None:
    code, out = run(tmp_path, "good.md")
    assert code == 0, capsys.readouterr().out
    header = out.read_text(encoding="ascii")
    generated = re.findall(r"^#define\s+(PIN_[A-Z0-9_]+)\s", header, re.MULTILINE)
    assert sorted(generated) == sorted(pins_h_names())
    assert len(generated) == len(set(generated))
    assert "#define PINS_CONFIRMED 1" in header
    assert '#define PINS_TABLE_DATE "2027-01-06"' in header  # latest date of the table
    assert re.search(r'#define PINS_TABLE_SHA256 "[0-9a-f]{64}"', header)
    assert re.search(r"#define PIN_MOTOR_LEFT_PWM\s+25$", header, re.MULTILINE)
    assert re.search(r"#define PIN_US_REAR_TRIG\s+\(SCR_PIN_EXPANDER_BASE \+ 3\)", header)
    assert "#define SCR_PINS_USE_EXPANDER 1" in header
    assert "#define SCR_EXPANDER_I2C_ADDR 0x20" in header
    assert "#error" in header and "SCR_PINS_H_INCLUDING_GENERATED" in header


def test_expander_base_matches_pins_h() -> None:
    m = re.search(r"#define SCR_PIN_EXPANDER_BASE (\d+)", PINS_H.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == pft.EXPANDER_BASE


def test_check_mode_writes_nothing(tmp_path: Path) -> None:
    code, out = run(tmp_path, "good.md", "--check")
    assert code == 0
    assert not out.exists()


def test_checksum_changes_with_the_table(tmp_path: Path) -> None:
    good = (FIXTURES / "good.md").read_text(encoding="utf-8")
    a = pft.check(good, PINS_H.read_text(encoding="utf-8"))
    b = pft.check(good.replace("| 13 | |", "| 4 | |").replace("| divider | 4 |", "| divider | 13 |"),
                  PINS_H.read_text(encoding="utf-8"))
    assert a.ok and b.ok
    assert a.table_sha256 != b.table_sha256


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("duplicate.md", "GPIO25 is already used"),
        ("input_only_output.md", "GPIO34 is input-only"),
        ("flash_pin.md", "GPIO9 is a flash pin"),
        ("uart0.md", "GPIO1 is UART0"),
        ("strapping_no_yes.md", "GPIO15 is a strapping pin"),
        ("gpio12_output.md", "HARD WARNING: GPIO12"),
        ("missing_signal.md", "missing signal: no row for PIN_SERVO_PAN"),
        ("missing_date.md", "'Checked by / date' is empty"),
        ("invalid_gpio.md", "GPIO24 does not exist"),
        ("expander_no_addr.md", "expander address"),
        ("expander_on_echo.md", "expander pins are allowed only"),
        ("expander_on_dir.md", "X4: expander pins are allowed only on the four PIN_US_*_TRIG rows"),
    ],
)
def test_bad_tables_are_refused_and_nothing_is_written(
    tmp_path: Path, capsys, fixture: str, expected: str
) -> None:
    out = tmp_path / "pins_generated.h"
    out.write_text("// previous header\n", encoding="ascii")
    code, _ = run(tmp_path, fixture)
    printed = capsys.readouterr().out
    assert code == 1
    assert expected in printed
    assert "ERROR: line " in printed or "ERROR: missing signal" in printed
    assert "Nothing was written." in printed
    assert out.read_text(encoding="ascii") == "// previous header\n"  # untouched


def test_errors_carry_the_row_line_number(tmp_path: Path, capsys) -> None:
    run(tmp_path, "flash_pin.md")
    lines = (FIXTURES / "flash_pin.md").read_text(encoding="utf-8").splitlines()
    servo_line = next(i + 1 for i, l in enumerate(lines) if "PIN_SERVO_PAN" in l)
    assert f"ERROR: line {servo_line} (PIN_SERVO_PAN)" in capsys.readouterr().out


def test_gpio12_accepted_with_yes_still_warns(tmp_path: Path, capsys) -> None:
    text = (FIXTURES / "gpio12_output.md").read_text(encoding="utf-8").replace("| 12 | |", "| 12 | yes |")
    table = tmp_path / "t.md"
    table.write_text(text, encoding="utf-8")
    out = tmp_path / "h.h"
    code = pft.main(["--table", str(table), "--pins-h", str(PINS_H), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 0
    assert "WARNING:" in printed and "HARD WARNING: GPIO12" in printed and "OUTPUT" in printed


def test_all_errors_are_reported_at_once(tmp_path: Path, capsys) -> None:
    text = (FIXTURES / "good.md").read_text(encoding="utf-8")
    text = text.replace("| out | – | 13 |", "| out | – | 7 |").replace("| 15 | yes |", "| 15 | |")
    table = tmp_path / "t.md"
    table.write_text(text, encoding="utf-8")
    code = pft.main(["--table", str(table), "--pins-h", str(PINS_H), "--check"])
    printed = capsys.readouterr().out
    assert code == 1
    assert "GPIO7 is a flash pin" in printed and "GPIO15 is a strapping pin" in printed
    assert "2 error(s)" in printed


def test_real_hardware_table_is_empty_and_fails_cleanly(tmp_path: Path, capsys) -> None:
    """The committed docs/HARDWARE.md table is intentionally empty until the robot is wired."""
    out = tmp_path / "pins_generated.h"
    code = pft.main(["--table", str(HARDWARE_MD), "--pins-h", str(PINS_H), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 1
    assert printed.count("ESP32 GPIO not filled in") == 25
    assert not out.exists()


def test_no_table_is_exit_2(tmp_path: Path, capsys) -> None:
    table = tmp_path / "t.md"
    table.write_text("# nothing here\n", encoding="utf-8")
    assert pft.main(["--table", str(table), "--pins-h", str(PINS_H), "--check"]) == 2
