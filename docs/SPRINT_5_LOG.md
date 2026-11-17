# Sprint 5 log

Task file: `docs/SPRINT_5.md`. Branch: `sprint-5` (created from `sprint-4` at 14167cd).

**Start time: Tue 2026-11-17 22:14 (laptop clock, `date`).** Time budget: 5 hours (the run instruction; the task file says 6) → stop by 03:14.

**Nothing in this sprint ran on real hardware.** There is no Raspberry Pi, ESP32, Rover 5, sensor or screen yet. Everything was tested on the laptop, against the simulated ESP32, in Docker, or in an emulated arm64 container. Every simulated result is labelled as such (rule 5).

The questions for Mohannd, what only he can do, the steps for when the parts arrive and what is not verified are at the end of this file.

## S0 — Baseline
- `sprint-5` created from `sprint-4`. Suites at the start:

  | Suite | Result |
  |---|---|
  | Backend `pytest -q` | 255 passed |
  | Robot `pytest -q` | 186 passed |
  | Firmware `tools/test.sh` | 100 test cases: 100 succeeded; `sim-check` 0 failures |
  | JS speaker logic | all checks passed |
- `docs/VIDEO_SCRIPTS.md` and `tools/video/` (renderer, three program specs, fonts Cairo and Outfit under SIL OFL 1.1 with `OFL.txt`, `.gitignore` for outputs) committed unchanged. Not run (needs Node, Playwright and ffmpeg); `python -m py_compile tools/video/*.py` passes.
- Commit: `171767d`.

## S1 — Pin plan on paper, and the only way pins get into the firmware
### S1.1 `docs/PIN_PLAN.md` ("PROPOSAL — the firmware does not use this file")
- The ESP32-DevKit 38-pin (ESP32-WROOM-32) rules with sources: flash pins 6–11, input-only 34/35/36/39, strapping pins 0/2/5/12/15 and their boot risks, UART0 1/3 for the USB link, PCNT units, LEDC channels. The ESP-IDF GPIO, LEDC and PCNT pages and the esptool boot-mode page were read today. The ESP32 datasheet, the WROOM-32 datasheet and the errata were **not** re-read (the PDF could not be read here); the plan marks those points.
- Signal count 25. **Option A (all on GPIO) does not fit:** 15 free pins + 4 input-only + 5 strapping = 24, even using GPIO0 and GPIO12, which can stop the robot booting.
- **Recommended: Option B, an MCP23017 at I2C 0x20 carrying the 4 HC-SR04 trigger lines.** The 25 signals then fit, only GPIO5 and GPIO15 are used as strapping pins (as echo inputs, harmless at either level), all motor signals stay on direct GPIO, and a hung I2C bus fails safe (no ping → no echo → driving is blocked). The direction lines were not put on the expander, because a failed I2C write could leave a wrong direction while the PWM is on.
- Level shifting: the Rover 5 encoder supply range could not be found in any source (product pages only say "+5 V"); the plan assumes 5 V and asks you to measure at 3.3 V on the bench. HC-SR04 echoes go through 1 kΩ / 2 kΩ dividers.
- Boot safety: 10 kΩ pull-downs on L298N ENA/ENB; motor pins LOW first thing in `setup()`.
- A wiring table with a "Proposed GPIO" column (proposal only). Commit: `8a466dd`.

### S1.2 Checker and generator
- `firmware/tools/pins_from_table.py` reads the pin-map table in `docs/HARDWARE.md` and refuses to write anything on any error: duplicates, input-only pins as outputs, flash pins, UART0, strapping pins without `yes` (GPIO12 always warns), a missing signal, an empty or invalid "Checked by / date", GPIOs that do not exist, expander rules (`X0`–`X15` only on trigger and direction rows). On success it writes `firmware/include/pins_generated.h`, the **only** file that defines `PINS_CONFIRMED` (with the table's SHA-256 and date). You commit that file after wiring.
- `firmware/include/pins.h` has three mutually exclusive paths: the CI profile (`pins_ci.h`), the compile check (all `-1`), or `pins_generated.h`; otherwise the `#error` stays. Defining `PINS_CONFIRMED` by hand is now an `#error`.
- The real (empty) table: `--check` exits 1 with 50 errors ("not filled in" ×25, "Checked by / date empty" ×25), as expected until wiring.
- Guard paths were built on a scratch copy of `firmware/` (7 cases, each giving the expected `#error` or SUCCESS).
- Tests: 21 (`firmware/tools/tests/`, 12 fixture tables). Commit: `7902f1c`.
- Consequence passed on to the firmware: Option B needs an MCP23017 driver in the firmware, otherwise a filled-in Option B table would not compile.
