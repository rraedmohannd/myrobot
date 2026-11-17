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

## Backend support (parts of S3.4, S6.2, S6.3, S6.5)
- **`stop_roaming`** (defaults row 14): ROAMING or PAUSED → IDLE where the robot stands, no return, the run stays open; refused in any other state. In the shared contract, the admin "Robot (live)" tab (a "Stop roaming" button with the same confirmation), the command endpoint and the backend's simulated robot. Commit `7e1732b`.
- **Optional `robot.status` fields** (protocol stays version 2, old clients unaffected): `battery_a`, `energy_wh`, `battery_warning`, `health{cpu_temp_c, under_voltage_now/seen, throttled_now/seen, cpu_load_1m, mem_used_pct, disk_used_pct}`, `firmware{version, git, param_hash, pins_confirmed, build_env, reset_reason}`, `calibration{status, updated, is_simulated}` (`status` and `is_simulated` are required inside that block, rule 5). New message `calibration.report`; `power{v, a, wh}` in pose samples. Migration **0012** (`pose_samples.power`, `calibration_records`, `bringup_steps`) — applied to your database (schema only, no content changed). `under_voltage` and `battery_warning` events are stored even when no run is open. The "Robot (live)" tab shows battery %, V, A, Wh, the warning and a Pi health line. Commit `15a9f20`.
- **Admin "Bring-up" tab** (S6.3): live diagnostics, calibration records (real ones, and simulated ones apart with "SIMULATED — not a measurement"), the checklist parsed from `docs/BRINGUP.md` with ticks stored in the database (who, when, note; a tick written in the file itself is ignored), and a power chart (V / A / Wh of real runs). Endpoints `GET /api/admin/bringup`, `PUT /api/admin/bringup/steps/{id}`, `GET /api/admin/runs/{id}/power`, all behind the admin login or token. Commits `9dd6e0e`, `75b04ca` (layout).
- **`export-report`** (S6.5): `calibration.csv` (real records with history), `calibration_SIMULATED.csv`, a calibration section in the report README, `power.csv` and `power_energy.png` (real runs only; "No data yet" otherwise). Commit `748b4b6`.
- `docs/PROTOCOL.md`: prose for every addition. Commit `ad80403`.
- Tests: **268 passed** (255 at the start; 13 new).
- **Not verified:** the tabs were rendered in headless Edge against a mock server with fake JSON only (screenshots looked right, no JS errors), never against the real backend; nothing real feeds the new fields yet.

## S4 — Bring-up kit: `robotctl` and `docs/BRINGUP.md` (and the P1 `robotctl` steps of S6.4 and S6.6)
- `python -m robotctl <command>` reaches the ESP32 three ways: through `robotd`'s local control API (`--ctl`, robotd must be IDLE and agree), directly on the serial port when `robotd` is stopped (`--port`, a background heartbeat; if `robotctl` dies the ESP32's own 300 ms watchdog stops the motors), or the simulated ESP32 (`--sim-tcp`).
- **Safety frame:** a typed confirmation before anything moves ("tracks off the ground"); drives bounded to 30 s and duty ≤ 0.5; STOP, bring-up off and MODE SAFE on every exit, including errors and Ctrl-C. On a never-calibrated firmware the duty caps, ramp and maximum speed are raised only for that one test, after a second typed confirmation, and put back afterwards. Values are set and written to the calibration file only after the operator accepts; `PARAM_SAVE` is offered separately. A simulated result can never be written into a real calibration file, nor the reverse. Every step has non-interactive flags for tests.
- **Commands:** `status`, `diag` (expects the IMU 0x68/0x69, INA219 0x40 and, by default, the MCP23017 at 0x20 of option B; `--expect-version` for the flash script), `param get|set|save`, `motor-test`, `encoder-check`, `ultrasonic-check`, `gyro-cal`, `rotate-test` (`GYRO_SCALE`, and `track_width_m` when driven), `servo-cal` (8 `SERVO_*` values), `battery-cal`, `distance-cal` (`METRES_PER_TICK` with mean, spread, standard deviation), `return-trial` (through the backend's admin API with a token typed by you, never stored; refused for simulated robots), and the P1 steps `pid-step` (CSV, PNG if matplotlib is installed, rule-of-thumb gain suggestions applied only if accepted), `square-test`, `hfov-cal`, `bundle` (logs, configuration, calibration, versions; extension allow-list, so no images, audio or models; secrets redacted).
- **`docs/BRINGUP.md`:** 8 sections in the required order (before power, flash, tracks off the ground, encoders, sensors, calibration, Pi, floor), 50 checklist steps in the format the admin "Bring-up" tab reads (checked with the backend's parser rules: no problems). Each step gives the exact command, the expected output, what to write down and when to stop and ask. It includes the option A/B decision (default B), the parts to buy, the WROOM-32 check, the 3.3 V encoder measurement, the ENA/ENB pull-downs, and filling the pin table → `pins_from_table.py`. **No step says "write code".** Sections 1–6 run on the laptop over USB; 7–8 on the Pi.
- Tests: 49 in `robot/tests/robotctl/` (against the simulated ESP32 in-process, over TCP, and through `robotd --sim`'s real control API; a fake backend). Whole robot suite at that point: 280 passed.
- Commits: `6369793`, `a385590`, `e635bb3`, `975082b`.
- **Not verified:** everything ran against the simulated ESP32. No serial port, no real backend for `return-trial`, no matplotlib PNG; the interactive prompts were exercised through a scripted input; the `pid-step` and `square-test` numbers come from the simulator's idealised control loop.
