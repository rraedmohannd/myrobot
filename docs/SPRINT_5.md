# Sprint 5 — robot software complete and bring-up kit, before the parts arrive (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-17. Run it **after sprint 4** (`docs/SPRINT_4_LOG.md`).

**Goal (Mohannd, 2026-11-17).** The backend is done. Before the parts arrive, **every line of code for the Raspberry Pi and the ESP32 must exist and be ready**: all modules, the complete firmware, the install and update scripts, and the tools that take the robot from "wired" to "calibrated". When the parts come, the only work left is wiring, filling in the pin table, running the bring-up steps (which write the calibration values for him) and the floor tests. **No new code should be needed at that point.**

Read first:
- `CLAUDE.md` (rules 4, 5 and 8);
- `docs/SPRINT_4_LOG.md` (all of it, especially "Questions for Mohannd", the checklist and "What is not verified");
- `docs/ROBOT_SOFTWARE.md`, `docs/SERIAL_PROTOCOL.md`, `docs/ODOMETRY.md`, `docs/HARDWARE.md`, `firmware/README.md`, `robot/README.md`.

**Rules.** Same as `docs/DEMO_SPRINT.md` and `CLAUDE.md`, with these changes:
- **Branch:** `sprint-5`, created from `sprint-4`. Push it; never push to `main`.
- **Log:** `docs/SPRINT_5_LOG.md`, same format as sprint 4, ending with "Questions for Mohannd", "What is done" and "What is not verified, said plainly".
- **Stop** after 6 hours. Do the P0 items first, then P1, then P2. Use parallel subagents where the parts are independent (sprint 4 did this well).
- **OpenAI budget:** 1 `evaluate`, 1 `latency` run. TTS only through the cache. No ElevenLabs call.
- **The parts are not bought yet.** Every hardware value stays a placeholder and is configurable. Nothing in this sprint is a physical result (rule 5).

**Rule 8, how it applies in this sprint.** Mohannd asked on 2026-11-17 for everything to be ready before wiring, so a **pin plan on paper** is now wanted (S1). The firmware still takes pin numbers **only** from the pin-map table in `docs/HARDWARE.md` once he fills it in after wiring. The table goes through a checker and a generator (S1); there is no other way into the firmware.

**Defaults for the sprint 4 questions.** Use these; each one is configurable, and Mohannd confirms or changes them in the log's questions:

| Sprint 4 question | Default for this sprint |
|---|---|
| 1 TTS before 2027-01-06 | No change. Mohannd decides by 2026-12-20 |
| 2 WAV from `openai_realtime` | Accept WAV; no mp3 encoder |
| 3 Stale sources stop answering | Keep as is (safer for facts) |
| 4 Source 66 numbers | Unchanged; the question stays open |
| 5 VNH or L298N | L298N (the `docs/HARDWARE.md` list); the VNH switch stays |
| 6 Motor voltage 7.2 V on a 12.6 V pack | Voltage compensation in the firmware (S2b) |
| 7 Pin budget | The pin plan (S1) |
| 8 I2C addresses | An I2C scan in the firmware's diagnostics (S2c) and in `robotctl diag` |
| 9 Low battery | Defaults in S3c |
| 10 Speeds and distances | Keep the placeholders; tuned with the bring-up tools |
| 11 Backend lost while roaming | Resume by itself only if the backend was lost for ≤ 60 s (configurable); otherwise stay PAUSED until `resume` |
| 12 Engagement | Only from IDLE or ROAMING (unchanged); tolerate a look-away of up to 0.3 s before "engaged" drops |
| 13 Camera ownership | `visiond` owns the webcam; in robot mode the page never opens the camera (enforce it, with a test) |
| 14 Stop roaming without returning | Add `stop_roaming` (→ IDLE in place) |
| 15 Webcam field of view, servo limits | Placeholders, measured with the bring-up tools (S4) |
| 16 Simulated run 543 | Keep (it is labelled simulated) |
| 17 `docs/VIDEO_SCRIPTS.md` | Commit it, together with `tools/video/` (S0) |

---

## P0

### S0 — Baseline (15 min)
1. Create the branch, then run every suite and record the counts:
   - backend `pytest` (255);
   - robot tests (186);
   - firmware `tools/test.sh` (100 + `sim-check`);
   - the JS speaker test.
2. Commit, in its own commit:
   - `docs/VIDEO_SCRIPTS.md`;
   - the new `tools/video/` folder: the program-video renderer made in the chat on 2026-11-17, with its `README.md`, fonts under SIL OFL and a `.gitignore` for outputs.

   Do not run it (it needs Node, Playwright and ffmpeg). Only check that `python -m py_compile tools/video/*.py` passes.

### S1 — Pin plan on paper, and the only way pins get into the firmware
1. **`docs/PIN_PLAN.md`, marked "PROPOSAL — the firmware does not use this file".**
   - **The ESP32-DevKit 38-pin (ESP32-WROOM-32) rules, with a source:**
     - flash pins 6–11 are unusable;
     - 34/35/36/39 are input-only, with no internal pull-ups;
     - strapping pins 0, 2, 5, 12 and 15 have their boot levels and risks (12 high at boot breaks the flash voltage);
     - UART0 (1/3) is the USB link;
     - PCNT units (8) for the 4 encoders, LEDC channels for the PWM and the servo.
   - **The signal count:** 6 motor, 8 encoder, 8 ultrasonic, 2 I2C, 1 servo = 25.
   - **Option A — everything on GPIO:** does it fit, and which strapping pins it needs and why that is risky.
   - **Option B — I2C expander:** move the 4 HC-SR04 trigger lines (or the 4 L298N direction lines) to an MCP23017 or PCF8574 on the existing I2C bus. What to buy, the address, and the effect on timing (a trigger pulse ≥ 10 µs over I2C is fine; echoes stay on GPIO).
   - **Recommend one option, with reasons.**
   - **Level shifting:**
     - encoder outputs: check the Rover 5 encoder supply range, since at 3.3 V supply they need no shifter;
     - HC-SR04 echoes: 5 V, so a divider or shifter is needed;
     - how many channels the 2 BSS138 modules in the list give, and what is missing.
   - **Boot safety:** a 10 kΩ pull-down on each L298N enable (ENA/ENB), so the tracks cannot move while the ESP32 boots or resets; motor outputs driven LOW as the very first thing in `setup()`.
   - **A wiring table** in the same format as the `docs/HARDWARE.md` pin map, with the proposed GPIO in a "proposed" column. Mohannd checks it with his supervisor and copies it after wiring.
2. **`firmware/tools/pins_from_table.py`:** reads the pin-map table of `docs/HARDWARE.md`, checks it against the rules above and writes `firmware/include/pins_generated.h`. Checks:
   - duplicates;
   - input-only pins used as outputs;
   - flash pins;
   - strapping-pin warnings that need a "yes" in the table;
   - a missing signal;
   - an empty "Checked by / date".

   It refuses to write the file on any error. `pins.h` includes the generated file when it exists, and only that file defines `PINS_CONFIRMED`; the `#error` path stays. Add tests with fixture tables: good, duplicate, input-only output, flash pin, missing date.
3. **Compile-only pin profile.** Add an `esp32-ci-pins` environment with a fake but valid pin set **for compiling only**. A build flag `SCR_CI_PINS_DO_NOT_FLASH` makes the firmware boot into permanent SAFE mode, print "CI build — not flashable" and never drive a pin. Compile it for Arduino core 2.x **and** 3.x, so the real driver code is compiled with real pin numbers on both cores (the 3.x branch was never compiled in sprint 4).

### S2 — Firmware completion (`firmware/`)
1. **Runtime parameters (no reflashing for every tuning step).**
   - **Protocol:** add `PARAM_SET {id, value}`, `PARAM_GET {id}`, `PARAM_SAVE` and `PARAM_REPORT` to the protocol, in both codecs, with new shared fixtures and cross-tests (`docs/SERIAL_PROTOCOL.md` updated).
   - **Which values:** the tunable values of `config.h` become runtime parameters with hard min/max bounds compiled in (`config.h` keeps the defaults, which stay "does not move"):
     - `METRES_PER_TICK`;
     - the PID gains, ramp and maximum speed;
     - the duty caps;
     - the encoder inversions;
     - the servo limits, centre, µs/deg and invert;
     - the obstacle thresholds;
     - the battery thresholds;
     - the INA219 shunt and scale.
   - **Storage:** values persist in NVS.
   - **Safety rules:**
     - out of range → `REJECTED`;
     - no change while the tracks move;
     - a parameter hash in the telemetry and in diagnostics;
     - `PARAM_SAVE` only in SAFE or IDLE mode.
   - **Single source of truth:** `robotd` pushes the calibration values (S3a) at every connection and checks the hash. The "the two `metres_per_tick` must be equal" problem of sprint 4 then cannot happen.
2. **Motor voltage compensation (question 6).**
   - Duty cap = `MOTOR_RATED_V / max(V_batt − V_DRIVER_DROP, ε)`, clamped by an absolute cap. `V_batt` comes from the INA219; `V_DRIVER_DROP` is a parameter (L298N placeholder 2.0 V, VNH 0.3 V).
   - Without the INA219, use the most conservative constant for a full pack (12.6 V).
   - Tests: full pack, half pack, INA219 missing, the voltage jumping.
3. **Diagnostics.**
   - `DIAG_REQUEST` returns `DIAG_REPORT`:
     - firmware version and git hash, build environment, driver type;
     - `PINS_CONFIRMED`, the parameter hash;
     - reset reason (power-on, brownout, watchdog, panic) and uptime;
     - control-loop period (max and jitter), free heap;
     - **I2C scan results**, presence of each sensor.
   - Send it once at boot as well.
4. **Robustness.**
   - Task watchdog (`esp_task_wdt`) on the control and telemetry tasks: a hang resets, and a reset boots into SAFE.
   - An I2C timeout and bus recovery (9 clocks), so a stuck IMU or INA219 can never block the control loop: I2C off the control task, or with bounded time.
   - PCNT overflow and wrap tested at both ends.
   - The brownout reset is reported in `DIAG`.
   - Motor pins LOW before anything else at boot.
5. **Slip event:** a large front/rear encoder disagreement on one side sends `EVENT SLIP {side}` (`docs/ODOMETRY.md` §3), with a threshold parameter and tests.
6. **Bring-up modes** (all refused outside `MODE TEST`, all bounded by `TEST_MAX_DUTY`, all still under the watchdog):
   - a fast raw IMU stream for gyro-scale calibration;
   - raw ultrasonic distances;
   - servo jog by small steps.
7. **Flash from the Pi:** `robot/deploy/flash_esp32.sh` flashes a firmware file built on the laptop (Docker) with `esptool`, then checks the version through `DIAG`. Document how to build the file.
8. **Safety review by a separate subagent** that did not write the firmware. It reviews:
   - the watchdog;
   - the obstacle stop and hysteresis;
   - the reverse rule;
   - boot and reset states, brownout;
   - parameter writes;
   - the I2C hang;
   - interrupt safety, PCNT;
   - the SAFE mode exits.

   Each finding goes in the log; fix the real ones with a test. Add fault-injection cases to `sim-check` (stuck I2C, missing telemetry task, parameter out of range).

### S3 — `robotd` completion (`robot/robotd/`)
1. **Calibration file:** `/etc/smart-campus-robot/calibration.json`, with a development path.
   - It is versioned, with a schema, and every value carries its date, operator, raw measurements and method.
   - `robotd` loads it, pushes the firmware parameters (S2a) and checks the hash.
   - `MODE DRIVE` stays refused until the distance calibration exists (the firmware already enforces this).
   - Tests: missing file, old version, hash mismatch.
2. **Gyro bias** (missing from sprint 4):
   - send `CALIBRATE_GYRO` at start while the robot is still;
   - re-estimate the bias whenever the robot stands still (`docs/ODOMETRY.md` §3), with tests on the simulated ESP32.
3. **Battery (question 9).**
   - Voltage → percentage for a 3S Li-ion pack (placeholder table, marked so).
   - Defaults:
     - warning at 10.5 V;
     - refuse roaming and return to start below 9.9 V;
     - the firmware hard cut stays a parameter, 0 until Mohannd sets it.
   - Energy used (Wh) integrated from the INA219 current, for the 30-minute test.
   - The battery shows on the robot page and in the admin.
4. **Commands and behaviour from the defaults table:**
   - `stop_roaming`;
   - the 60 s backend-lost rule;
   - the 0.3 s look-away tolerance;
   - camera ownership in robot mode.

   Tests for each; update `docs/ROBOT_SOFTWARE.md` and `docs/PROTOCOL.md`.

### S4 — Bring-up kit: `robotctl` and `docs/BRINGUP.md`
A command-line tool (`python -m robotctl …`) that talks to `robotd`, or straight to the ESP32 when `robotd` is stopped. Every step:
- prints what to do;
- refuses to move anything without a typed confirmation such as "tracks off the ground";
- stops on its own;
- writes its result, with the raw measurements, to the calibration file and to a local calibration log.

Every step has non-interactive flags for tests and is tested against the **simulated** ESP32 (labelled).

| Command | What it does |
|---|---|
| `status` | Live view: state, telemetry, encoders, ultrasonics, gyro, battery, servo, faults, parameter hash, firmware version |
| `diag` | `DIAG_REPORT` + the I2C scan compared with the expected addresses (IMU 0x68/0x69, INA219 0x40, expander if S1 option B) |
| `motor-test --side left\|right --duty 0.15 --secs 1` | Open loop in `MODE TEST`; checks that the encoders of that side count up; proposes the motor/encoder invert values and saves them after confirmation |
| `encoder-check` | Push each track by hand; shows each count and its sign |
| `ultrasonic-check --distance 0.50` | Box at a known distance; table per sensor (reading, error, misses) |
| `gyro-cal`, `rotate-test --turns 1 --runs 5` | Bias; 360° by hand or driven → gyro scale |
| `servo-cal` | Jog to find the limits, centre trim, µs per degree, which way is left |
| `battery-cal --multimeter 12.34` | Voltage scale from a multimeter reading |
| `distance-cal --runs 5` | 3 m on a tape; the robot drives (`MODE TEST`, low duty) or is pushed; the operator types the measured distance → `metres_per_tick` mean and spread |
| `return-trial` | Mark the start, roam N seconds, return, type the tape distance → saved as a real return trial for that run in the backend |

`docs/BRINGUP.md` replaces the sprint 4 checklist with the exact commands, the expected output, what to write down, and when to stop and ask. Keep the same order:
1. before power;
2. flash;
3. tracks off the ground;
4. encoders;
5. sensors;
6. calibration;
7. Pi;
8. floor.

### S5 — The Pi install, verified without a Pi
1. **Check the current Raspberry Pi OS release and its Python version** (official site, with the date read). Check that `opencv-python-headless` and `mediapipe` have aarch64 wheels for that Python. If not, use a pinned Python managed by `uv` in the virtualenv. Pin all robot dependencies in a lock file.
2. **`setup.sh --container-test`:** run the install in an **arm64 Debian container of the same release** (`docker run --platform linux/arm64 …`, emulated on the laptop). The flag skips systemd, udev, the kiosk and the hardware, but installs the packages, the virtualenv and the models. Then inside the container:
   - the robot tests;
   - `robotd --sim` for 20 s;
   - `visiond` on a synthetic video;
   - `import mediapipe`.

   Record the times and fix every failure in `setup.sh`. Run `shellcheck` on every script.
3. **Kiosk on the current release's desktop** (Wayland, labwc or what the release uses):
   - autostart, with an X11 fallback;
   - no cursor, no screen blanking;
   - touch mapped to 1024×600.

   Say plainly in the log what could not be checked without the screen.
4. **`robot/deploy/update.sh`:**
   - pull;
   - reinstall the dependencies if the lock changed;
   - restart the services;
   - offer to flash the ESP32 if the firmware version differs (asks first).
5. **Network at the demo site:**
   - `BACKEND_URL` may list several addresses, tried in order;
   - document the laptop-hotspot option.

## P1

### S6 — Robot page and backend support
1. **Settings page on the robot screen,** behind a hidden gesture and a PIN from `robot.env`:
   - backend address;
   - volume;
   - a speaker test tone and a microphone level meter (nothing recorded or stored);
   - Wi-Fi status and IP;
   - versions, battery, calibration status.
2. **Pi health in `robot.status`** (additive optional fields, protocol version stays 2, contract tests):
   - CPU temperature;
   - throttling and under-voltage flags;
   - CPU load, memory, disk;
   - firmware version, parameter hash, calibration status, battery %, Wh.

   An under-voltage flag is logged as a warning event (evidence for the power design).
3. **Admin "Bring-up" tab:**
   - live diagnostics;
   - the calibration records (real ones only; simulated ones labelled);
   - the `BRINGUP.md` checklist with checkboxes stored in the database (who and when);
   - a power chart.

   Also add a `stop_roaming` button in "Robot (live)".
4. **Black box:**
   - `robotd` keeps the last 60 s of telemetry and events;
   - on FAULT, obstacle stop or communication loss it writes them to `/var/log/smart-campus-robot/blackbox-<time>.jsonl`;
   - `robotctl bundle` zips the logs, configuration, calibration and versions (never images or audio).
5. **`export-report`:** add the calibration table and the power and energy chart of real runs.
6. **Remaining `robotctl` steps:**
   - `pid-step`: closed-loop step on the stand; CSV and plot; suggested gains, accepted only by the operator;
   - `square-test`: 2 m × 2 m clockwise and counter-clockwise, with the measured closure typed in;
   - `hfov-cal`: camera field of view using the servo and a face or marker.
7. **One `evaluate`** (the golden set was not re-run after sprint 4) and **one `latency` run.** Record both; if the time from the end of speech to the first audio is above 4 s, write in the log which stage is slow.

## P2 (only if time is left)

### S7 — AprilTag correction at the start point (optional, off by default)
- **Detection:** `visiond` detects an AprilTag 36h11 (OpenCV aruco) printed near the start mark.
- **Correction:** while RETURNING, `robotd` may use it to correct the final approach.
- **Labelling:** marked experimental and behind a flag.
- **Tests:** synthetic images (tag rendered in perspective).
- **Docs:** printing size and placement.
- **Never counted:** it is not used for any result unless Mohannd turns it on and says so in the report.

### S8 — Mechanical parts as code (`mechanical/`)
- **Parts:** parametric OpenSCAD files:
  - 7" display bracket;
  - MG996R pan bracket with webcam clamp;
  - HC-SR04 holders;
  - Pi 5 mount;
  - 3S 18650 holder plate.
- **Dimensions:** every one at the top of the file, marked "VERIFY WITH CALIPERS".
- **Exports:** STL and a PNG preview through OpenSCAD in Docker.
- **No claims:** no dimension is claimed as verified.

### S9 — CI
GitHub Actions on the sprint branches:
- backend tests (Postgres + pgvector service);
- robot tests;
- firmware native tests and the compile checks of S1.3.

---

## Wrap-up
- **Docs:** update `docs/PLAN.md` (robot software: "code complete, bring-up pending"), `docs/DECISIONS.md`, `docs/HARDWARE.md` (link to `PIN_PLAN.md`), `HOW_TO_START.md`, `robot/README.md`, `firmware/README.md`.
- **Final run of every suite;** commit, push, write the log.
- **The log ends with:**
  - "Questions for Mohannd";
  - "What only Mohannd can do" (buy the parts, choose option A/B of the pin plan, wiring, fill the table, the bring-up steps, places and labs, listen to the voices);
  - "What is not verified, said plainly".

## Done when
- P0 is complete with commits and tests.
- Every suite passes.
- `docs/BRINGUP.md` and `docs/PIN_PLAN.md` exist.
- The arm64 container install ran, or its failure is explained.
- The log says, step by step, what Mohannd does when the parts arrive. **No step in it says "write code".**
