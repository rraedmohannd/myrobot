#!/usr/bin/env python3
"""Pin-map checker and generator (docs/SPRINT_5.md S1.2, CLAUDE.md rule 8).

The ONLY way pin numbers get into the firmware:

    docs/HARDWARE.md pin-map table  --(this tool)-->  firmware/include/pins_generated.h

It reads the pin-map table, checks it against the ESP32 DevKit 38-pin (ESP32-WROOM-32) rules
written down in docs/PIN_PLAN.md, and writes the header only when there is no error at all.

    python firmware/tools/pins_from_table.py            check, then write pins_generated.h
    python firmware/tools/pins_from_table.py --check    check only, write nothing
    options: --table FILE  --pins-h FILE  --out FILE

Exit status: 0 = table OK (and header written unless --check), 1 = the table has errors
(nothing written), 2 = a file could not be read or has no pin-map table.

Standard library only, Python 3.11+.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TABLE = REPO_DIR / "docs" / "HARDWARE.md"
DEFAULT_PINS_H = REPO_DIR / "firmware" / "include" / "pins.h"
DEFAULT_OUT = REPO_DIR / "firmware" / "include" / "pins_generated.h"

# ---- ESP32 DevKit 38-pin (ESP32-WROOM-32) rules: sources in docs/PIN_PLAN.md section 1 -------
# GPIOs that exist on the chip and are brought out on the 38-pin DevKit.
EXISTING_GPIOS = frozenset([*range(0, 20), 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39])
FLASH_GPIOS = frozenset(range(6, 12))  # SPI flash of the module
UART0_GPIOS = frozenset({1, 3})  # TX0 / RX0 = USB link to the Pi and the flashing port
INPUT_ONLY_GPIOS = frozenset({34, 35, 36, 39})  # no output driver, no internal pull-up/down
STRAPPING_GPIOS = frozenset({0, 2, 5, 12, 15})
# Never on a motor line (safety review 4b): UART0 TX/RX carry the ROM boot log, GPIO5/15 are
# strapping pins with pull-ups at reset, GPIO5/14/15 are commonly reported to output a signal
# during boot (not in Espressif docs). See docs/PIN_PLAN.md section 7.
MOTOR_BOOT_FORBIDDEN_GPIOS = frozenset({1, 3, 5, 14, 15})
STRAPPING_NOTES = {
    0: "GPIO0 low at reset = download mode: the robot does not boot",
    2: "GPIO2 takes part in the boot-mode selection; many DevKits have an LED on it",
    5: "GPIO5 selects the SDIO-slave timing at reset (harmless for a normal boot)",
    12: "GPIO12 (MTDI) high at reset selects 1.8 V flash voltage: the 3.3 V flash browns out",
    15: "GPIO15 (MTDO) low at reset silences the ROM boot log (harmless)",
}
GPIO12_WARNING = (
    "HARD WARNING: GPIO12 (MTDI) is a strapping pin. If anything holds it HIGH at reset, the "
    "ESP32 sets the flash voltage to 1.8 V, the 3.3 V flash of the WROOM-32 browns out and the "
    "robot neither boots nor flashes. Prefer any other pin."
)

# ---- MCP23017 expander (option B of docs/PIN_PLAN.md) ------------------------------------------
EXPANDER_ADDR_NAME = "SCR_EXPANDER_I2C_ADDR"
EXPANDER_BASE = 100  # must equal SCR_PIN_EXPANDER_BASE in firmware/include/pins.h
EXPANDER_PIN_COUNT = 16  # X0..X15 = GPA0..GPA7, GPB0..GPB7
EXPANDER_ADDRESSES = range(0x20, 0x28)
# Only the four ultrasonic triggers: a motor direction on I2C would fail unsafe (PIN_PLAN.md section 4).
EXPANDER_ALLOWED = re.compile(r"^PIN_US_(FL|FC|FR|REAR)_TRIG$")

PIN_NAME_RE = re.compile(r"^\s*#\s*define\s+(PIN_[A-Z0-9_]+)\s+PIN_UNASSIGNED\b", re.MULTILINE)
DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
GPIO_RE = re.compile(r"^(?:GPIO\s*)?(\d+)$", re.IGNORECASE)
EXPANDER_RE = re.compile(r"^X(\d+)$", re.IGNORECASE)


class TableNotFound(Exception):
    """The file has no pin-map table, or the table misses a column."""


@dataclass
class Row:
    line: int
    name: str
    direction: str
    gpio: str
    strapping_ok: str
    checked: str


@dataclass
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pins: dict[str, int] = field(default_factory=dict)  # name -> GPIO or EXPANDER_BASE + n
    expander_addr: int | None = None
    table_sha256: str = ""
    table_date: str = ""
    table_text: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


# ---- parsing -------------------------------------------------------------------------------------
def pin_names_from_pins_h(text: str) -> list[str]:
    """Every PIN_* constant that pins.h sets to PIN_UNASSIGNED (the required signals)."""
    names = PIN_NAME_RE.findall(text)
    return list(dict.fromkeys(names))


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _norm_header(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("`", "")).strip().lower()


def _find_columns(header: list[str]) -> dict[str, int]:
    wanted = {
        "name": lambda h: h == "pins.h name",
        "direction": lambda h: h.startswith("direction"),
        "gpio": lambda h: h == "esp32 gpio",
        "strapping": lambda h: h.startswith("strapping pin ok"),
        "checked": lambda h: h.startswith("checked by"),
    }
    cols: dict[str, int] = {}
    norm = [_norm_header(h) for h in header]
    for key, match in wanted.items():
        for i, h in enumerate(norm):
            if match(h):
                cols[key] = i
                break
    missing = [k for k in wanted if k not in cols]
    if missing:
        labels = {
            "name": "`pins.h` name",
            "direction": "Direction (ESP32 side)",
            "gpio": "ESP32 GPIO",
            "strapping": "Strapping pin OK? (yes)",
            "checked": "Checked by / date",
        }
        raise TableNotFound(
            "the pin-map table misses the column(s): " + ", ".join(labels[k] for k in missing)
        )
    return cols


def parse_table(text: str) -> tuple[list[Row], str]:
    """Return the rows of the first table whose header has '`pins.h` name' and 'ESP32 GPIO'."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = [_norm_header(c) for c in _cells(line)]
        if "pins.h name" not in header or "esp32 gpio" not in header:
            continue
        cols = _find_columns(_cells(line))
        if i + 1 >= len(lines) or not re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            raise TableNotFound(f"line {i + 1}: the pin-map table header has no separator line")
        rows: list[Row] = []
        j = i + 2
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            cells = _cells(lines[j])
            cells += [""] * (max(cols.values()) + 1 - len(cells))

            def cell(key: str, cells: list[str] = cells) -> str:
                return cells[cols[key]].strip()

            rows.append(
                Row(
                    line=j + 1,
                    name=cell("name").replace("`", "").strip(),
                    direction=cell("direction"),
                    gpio=cell("gpio"),
                    strapping_ok=cell("strapping"),
                    checked=cell("checked"),
                )
            )
            j += 1
        table_text = "\n".join(l.rstrip() for l in lines[i:j])
        return rows, table_text
    raise TableNotFound("no pin-map table found (a table with '`pins.h` name' and 'ESP32 GPIO')")


# ---- rules ---------------------------------------------------------------------------------------
def expected_direction(name: str) -> str:
    if name.startswith("PIN_I2C_"):
        return "bidirectional"
    if name.startswith("PIN_ENC_") or name.endswith("_ECHO"):
        return "in"
    return "out"


def _check_date(checked: str) -> tuple[str | None, str | None]:
    """Return (iso date, error)."""
    if not checked:
        return None, "'Checked by / date' is empty (write your name and the date YYYY-MM-DD)"
    m = DATE_RE.search(checked)
    if not m:
        return None, f"'Checked by / date' has no date YYYY-MM-DD: {checked!r}"
    try:
        day = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, f"'Checked by / date' has an impossible date: {m.group(0)}"
    who = (checked[: m.start()] + checked[m.end() :]).strip(" /,;-–—")
    if not who:
        return None, "'Checked by / date' has a date but no name"
    return day.isoformat(), None


def check(table_text: str, pins_h_text: str) -> Result:
    res = Result()
    required = pin_names_from_pins_h(pins_h_text)
    if not required:
        res.errors.append("pins.h: no '#define PIN_... PIN_UNASSIGNED' lines found")
        return res
    rows, table = parse_table(table_text)
    res.table_text = table
    res.table_sha256 = hashlib.sha256(table.encode("utf-8")).hexdigest()

    def err(row: Row, msg: str) -> None:
        res.errors.append(f"line {row.line} ({row.name or '?'}): {msg}")

    def warn(row: Row, msg: str) -> None:
        res.warnings.append(f"line {row.line} ({row.name or '?'}): {msg}")

    seen_names: dict[str, int] = {}
    used: dict[int, Row] = {}
    dates: list[str] = []
    expander_rows: list[Row] = []
    addr_row: Row | None = None

    for row in rows:
        if row.name in seen_names:
            err(row, f"signal listed twice (first on line {seen_names[row.name]})")
            continue
        seen_names[row.name] = row.line

        if row.name == EXPANDER_ADDR_NAME:
            addr_row = row
            continue
        if row.name not in required:
            err(row, "unknown signal: not a PIN_* constant of firmware/include/pins.h")
            continue

        want_dir = expected_direction(row.name)
        if row.direction.strip().lower() != want_dir:
            err(row, f"direction must be '{want_dir}', the table says {row.direction!r}")

        date, date_err = _check_date(row.checked)
        if date_err:
            err(row, date_err)
        elif date:
            dates.append(date)

        value = row.gpio.strip()
        if not value:
            err(row, "ESP32 GPIO not filled in")
            continue

        mx = EXPANDER_RE.match(value)
        if mx:
            idx = int(mx.group(1))
            if not EXPANDER_ALLOWED.match(row.name):
                err(row, (
                    f"{value}: expander pins are allowed only on the four PIN_US_*_TRIG rows; "
                    "motor, encoder, echo, I2C and servo signals must be on a GPIO "
                    "(docs/PIN_PLAN.md section 4)"
                ))
                continue
            if idx >= EXPANDER_PIN_COUNT:
                err(row, f"{value}: the MCP23017 has X0..X{EXPANDER_PIN_COUNT - 1} only")
                continue
            code = EXPANDER_BASE + idx
            if code in used:
                err(row, f"{value} is already used on line {used[code].line} ({used[code].name})")
                continue
            used[code] = row
            res.pins[row.name] = code
            expander_rows.append(row)
            if row.strapping_ok:
                warn(row, "'Strapping pin OK?' is filled in for an expander pin; ignored")
            continue

        mg = GPIO_RE.match(value)
        if not mg:
            err(row, f"{value!r} is not a GPIO number (write e.g. 25, or X0 for an expander pin)")
            continue
        gpio = int(mg.group(1))
        if gpio not in EXISTING_GPIOS:
            err(row, f"GPIO{gpio} does not exist on the ESP32 DevKit 38-pin")
            continue
        if row.name.startswith("PIN_MOTOR_") and gpio in MOTOR_BOOT_FORBIDDEN_GPIOS:
            err(
                row,
                f"GPIO{gpio} outputs a signal / is a strapping or UART pin during boot; a motor "
                "line there can move a track while the ESP32 boots (docs/PIN_PLAN.md)",
            )
            continue
        if gpio in FLASH_GPIOS:
            err(row, f"GPIO{gpio} is a flash pin (6-11): never usable")
            continue
        if gpio in UART0_GPIOS:
            err(row, f"GPIO{gpio} is UART0 (1/3) = the USB link to the Pi: never usable")
            continue
        if gpio in INPUT_ONLY_GPIOS and want_dir != "in":
            err(row, f"GPIO{gpio} is input-only (34/35/36/39) but this signal is {want_dir}")
            continue
        if gpio in STRAPPING_GPIOS:
            acknowledged = row.strapping_ok.strip().lower() == "yes"
            if gpio == 12:
                text = GPIO12_WARNING
                if want_dir != "in":
                    text += " This row is an OUTPUT the firmware may drive HIGH."
                if acknowledged:
                    res.warnings.append(f"line {row.line} ({row.name}): {text}")
                else:
                    err(row, text + " Write 'yes' in 'Strapping pin OK?' only if you accept this.")
            elif not acknowledged:
                err(
                    row,
                    f"GPIO{gpio} is a strapping pin ({STRAPPING_NOTES[gpio]}); "
                    "write 'yes' in 'Strapping pin OK?' to accept it",
                )
            else:
                warn(row, f"strapping pin accepted: {STRAPPING_NOTES[gpio]}")
        elif row.strapping_ok:
            warn(row, f"'Strapping pin OK?' is filled in but GPIO{gpio} is not a strapping pin")
        if gpio in used:
            err(row, f"GPIO{gpio} is already used on line {used[gpio].line} ({used[gpio].name})")
            continue
        used[gpio] = row
        res.pins[row.name] = gpio

    # the expander address row
    addr_value = addr_row.gpio.strip() if addr_row else ""
    if addr_row and addr_value:
        try:
            addr = int(addr_value, 0)
        except ValueError:
            addr = -1
        if addr not in EXPANDER_ADDRESSES:
            err(addr_row, f"{addr_value!r} is not an MCP23017 address (0x20..0x27)")
        else:
            date, date_err = _check_date(addr_row.checked)
            if date_err:
                err(addr_row, date_err)
            elif date:
                dates.append(date)
            if expander_rows:
                res.expander_addr = addr
            else:
                warn(addr_row, "expander address given but no row uses an X pin; ignored")
    elif expander_rows:
        where = f"line {addr_row.line}" if addr_row else f"a row named {EXPANDER_ADDR_NAME}"
        for row in expander_rows:
            err(row, f"uses an expander pin but the expander address ({where}) is not filled in")

    for name in required:
        if name not in seen_names:
            res.errors.append(f"missing signal: no row for {name} (every pins.h constant needs one)")

    res.table_date = max(dates) if dates else ""
    if res.errors:
        res.pins = {}
    return res


# ---- output --------------------------------------------------------------------------------------
def render_header(res: Result, order: list[str], source: str) -> str:
    assert res.ok
    out = [
        "// GENERATED by firmware/tools/pins_from_table.py - DO NOT EDIT BY HAND.",
        f"// Source: {source} (pin-map table), checked {res.table_date}.",
        "// Committed with the filled-in table: it is the record of the pins this firmware uses.",
        "// Included only through pins.h. The only file that defines PINS_CONFIRMED.",
        "#pragma once",
        "",
        "#if !defined(SCR_PINS_H_INCLUDING_GENERATED)",
        '#error "Include pins.h, not pins_generated.h."',
        "#endif",
        "",
        "#define PINS_GENERATED_BY_TOOL 1",
        f'#define PINS_TABLE_SHA256 "{res.table_sha256}"',
        f'#define PINS_TABLE_DATE "{res.table_date}"',
        "#define PINS_CONFIRMED 1",
        "",
    ]
    width = max(len(n) for n in order)
    for name in order:
        v = res.pins[name]
        if v >= EXPANDER_BASE:
            value = f"(SCR_PIN_EXPANDER_BASE + {v - EXPANDER_BASE})  // X{v - EXPANDER_BASE}"
        else:
            value = str(v)
        out.append(f"#define {name.ljust(width)} {value}")
    if res.expander_addr is not None:
        out += [
            "",
            "// Option B: MCP23017 I/O expander on the I2C bus (docs/PIN_PLAN.md section 4).",
            "#define SCR_PINS_USE_EXPANDER 1",
            f"#define {EXPANDER_ADDR_NAME} 0x{res.expander_addr:02X}",
        ]
    return "\n".join(out) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pins_generated.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _existing_sha(path: Path) -> str | None:
    try:
        m = re.search(r'PINS_TABLE_SHA256 "([0-9a-f]+)"', path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--table", type=Path, default=DEFAULT_TABLE, help="markdown file with the table")
    ap.add_argument("--pins-h", type=Path, default=DEFAULT_PINS_H, help="pins.h (signal names)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="header to write")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = ap.parse_args(argv)

    try:
        table_text = args.table.read_text(encoding="utf-8")
        pins_h_text = args.pins_h.read_text(encoding="utf-8")
        res = check(table_text, pins_h_text)
    except (OSError, TableNotFound) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for w in res.warnings:
        print(f"WARNING: {w}")
    if not res.ok:
        for e in res.errors:
            print(f"ERROR: {e}")
        print(
            f"{len(res.errors)} error(s) in the pin-map table of {_display(args.table)}. "
            f"{'Nothing was written.' if not args.check else ''}".rstrip()
        )
        if all("ESP32 GPIO not filled in" in e or "'Checked by / date' is empty" in e for e in res.errors):
            print("The table is not filled in yet: expected until the robot is wired (docs/PIN_PLAN.md).")
        return 1

    order = pin_names_from_pins_h(pins_h_text)
    print(f"Pin-map table OK: {len(order)} signals, checked {res.table_date}, sha256 {res.table_sha256[:12]}")
    if args.check:
        old = _existing_sha(args.out)
        if old is not None and old != res.table_sha256:
            print(f"NOTE: {_display(args.out)} was generated from a different table; run without --check")
        return 0
    write_atomic(args.out, render_header(res, order, _display(args.table)))
    print(f"Wrote {_display(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
