# ESP32 firmware (sprint 4 R2, sprint 5 S1.3/S2)

Low-level controller of the Smart Campus Robot: motors, encoders, IMU, ultrasonics, pan servo,
battery sensing, the serial link to the Raspberry Pi, and the **local safety rules**.
PlatformIO project, Arduino-ESP32 core, board: ESP32 DevKit (38-pin).

> **Status: written before the hardware existed. Nothing in this directory has run on a real
> ESP32.** What was verified, and how, is listed under "What is and is not verified".

The contract with the Pi is `docs/SERIAL_PROTOCOL.md` (**version 2**: parameters, diagnostics,
bring-up streams, 48-byte telemetry with the parameter hash). Firmware version: **2.0.0**
(`lib/app/scr_version.h`, the only place). The ESP32 sends **raw values
only**; the pose is computed on the Pi (`docs/ODOMETRY.md`).

## Layout

```
firmware/
  platformio.ini            environments: esp32dev, esp32-compile-check, esp32-ci-pins,
                            esp32-ci-pins-core3, native, sim-check
  include/
    pins.h                  pin source selection; pins only from pins_generated.h (tools/pins_from_table.py)
    pins_ci.h               FAKE pins, compile only (esp32-ci-pins*), never flash
    config.h                compile-time constants (runtime parameters live in params.json)
  lib/                      hardware-independent C++ (no Arduino headers) - compiles natively
    protocol/               link: COBS, CRC-16/CCITT-FALSE, seq, all messages, stream decoder
    control/                speed_pid (PID), ramp limiter, servo limits/slew, gyro bias
                            calibration, battery monitor, encoder fault detector
    ultrasonic/             HC-SR04 scheduling: one sensor per slot (staggered), timeouts, faults
    safety/                 watchdog, front/rear obstacle rules, SAFE mode (protocol section 5)
    hal/                    driver interfaces (motors, encoders, imu, ultrasonic, servo, power, link)
    app/                    the application: control tick (100 Hz) and telemetry tick (50 Hz),
                            parameters, diagnostics, bring-up, battery cut-off; scr_version.h
    params/                 scr_param_table.h (GENERATED from shared/scr_serial/params.json) and
                            ParamStore (bounds, NVS load/save, FNV-1a hash)
    drivers_stub/           STUB drivers + a crude simulated plant, for tests and sim-check
  src/
    main_esp32.cpp          ESP32 entry: motor pins LOW first, task watchdog, tasks control 100 Hz,
                            telemetry 50 Hz (both on the task WDT), i2c 100 Hz
    drivers_esp32/          real drivers: LEDC PWM motors + servo, PCNT encoders (core 2.x and 3.x),
                            I2C task (ICM-20948, INA219, MCP23017 triggers, scan, bus recovery),
                            HC-SR04 by interrupts, NVS storage, system services, the USB serial link
    main_sim.cpp            sim-check entry: application + stubs, short SIMULATED smoke run
  test/
    test_protocol/          codec tests + cross-test with the Python implementation
    test_control/           PID, ramping, servo, gyro calibration, ultrasonic scheduling, battery
    test_safety/            watchdog, obstacle stop (front, rear, reverse without rear sensor), SAFE
    test_app/               whole application against the stubs
    test_sprint5/           parameters + NVS reboot, voltage compensation, DIAG, slip, bring-up,
                            battery cut-off, PCNT wrap, I2C recovery event, CI permanent SAFE
  tools/
    Dockerfile, test.sh, test.ps1   run everything in Docker (no compiler needed on the host)
    git_hash.py             PlatformIO extra script: SCR_GIT_HASH for DIAG
    build_release.sh        builds esp32dev in Docker and copies the flash files to firmware/release/
```

Module table of `docs/SPRINT_4.md` → code:

| Module | Logic (native, tested) | ESP32 driver (not run on hardware) |
|---|---|---|
| `motors` | ramp + duty in `lib/app`, `RampLimiter` in `lib/control` | `Esp32Motors` (`esp32_motion.cpp`) |
| `encoders` | speed from tick differences in `lib/app` | `Esp32Encoders`, PCNT ×4 |
| `speed_pid` | `Pid` in `lib/control`, one per track | – |
| `imu` | `GyroBiasCalibrator` in `lib/control` | `Esp32Imu` (ICM-20948) |
| `ultrasonic` | `UltrasonicScheduler` in `lib/ultrasonic` | `Esp32Ultrasonic` |
| `servo` | `ServoController` in `lib/control` | `Esp32Servo` (LEDC 50 Hz) |
| `power` | `BatteryMonitor` in `lib/control` | `Esp32Power` (INA219) |
| `link` | `lib/protocol` | `Esp32Link` |
| `safety` | `lib/safety` | – (pure logic, runs in the control task) |

## Run the tests

### With Docker (the laptop needs nothing else)

```
# Git Bash
firmware/tools/test.sh

# PowerShell
powershell -ExecutionPolicy Bypass -File firmware\tools\test.ps1
```

This builds a small image (`firmware/tools/Dockerfile`: Python + g++ + PlatformIO), then runs
`pio test -e native` and `pio run -e sim-check -t exec` inside it. Downloads are kept in the
Docker volume `scr-pio-cache`; build output stays inside that volume, nothing is written into the
repository except `shared/serial_fixtures/frames_from_cpp.txt`.

The same without the wrapper (Git Bash; this exact line was not run during sprint 4, the wrapper was):

```
MSYS_NO_PATHCONV=1 docker run --rm -v "C:/Users/hanan/Documents/smart-campus-robot:/work" \
  -w /work/firmware python:3.12 bash -c \
  "pip install -q platformio && pio test -e native && pio run -e sim-check -t exec"
```

Another command inside the image: `firmware/tools/test.sh "pio test -e native -f test_safety -v"`.

### With PlatformIO installed (needs a host C++ compiler for `native`)

```
cd firmware
pio test -e native            # unit tests
pio run  -e sim-check         # build everything off-target with stub drivers
pio run  -e sim-check -t exec # ... and run the SIMULATED smoke scenario
```

### Cross-test with Python (protocol)

`test_protocol` writes `shared/serial_fixtures/frames_from_cpp.txt` and reads
`shared/serial_fixtures/frames_from_python.txt` (decodes every frame, compares every field with
the canonical values of `docs/SERIAL_PROTOCOL.md` section 6, and the bytes with its own
encoding). The fixture directory is `../shared/serial_fixtures` relative to `firmware/`
(build flag `SCR_FIXTURE_DIR` in `platformio.ini`); the environment variable `SCR_FIXTURE_DIR`
overrides it at run time. The Python side decodes the C++ file in its own tests.

## Build and flash the real firmware

Not possible until the pins are confirmed - this is intended (CLAUDE.md rule 8).

### Pins: the only way in

1. Wire the robot. Fill in the pin-map table in `docs/HARDWARE.md` (proposal: `docs/PIN_PLAN.md`,
   option B = HC-SR04 trigger lines on an MCP23017 at 0x20).
2. Run `python firmware/tools/pins_from_table.py`. It checks the table (duplicates, flash/UART0/
   input-only pins, strapping pins, missing signals, "Checked by / date") and writes
   `firmware/include/pins_generated.h`, the only file that defines `PINS_CONFIRMED`.
3. Commit `firmware/include/pins_generated.h` together with the table.
4. `pio run -e esp32dev` (or the release build below).

`-DPINS_CONFIRMED` in `platformio.ini` and hand-written numbers in `pins.h` are `#error`s.
Motor direction lines on the expander are an `#error` too (fail-unsafe, `docs/PIN_PLAN.md` §4);
only the four `*_TRIG` lines may be `X0..X15`.

Guards:
- **Compile time:** without `pins_generated.h`, `pins.h` stops the `esp32dev` build with an `#error`.
- **Run time:** a driver whose pin is still negative does not start (`scr::pins::allAssigned`).
  Without a usable motor driver `MODE DRIVE`/`MODE TEST` are answered `REJECTED_SAFETY`; a missing
  ultrasonic sensor is reported as absent (`0xFFFE`), which blocks forward driving when all three
  front sensors are missing and blocks reversing when the rear one is missing.
- `native` and `sim-check` never include `pins.h`.
- `[env:esp32-compile-check]` (`-DPINS_COMPILE_CHECK_ONLY`) only proves that the ESP32 sources
  compile. All pins stay `-1`, every driver refuses to start, and the firmware prints one line on
  the serial port saying so. Do not flash it onto the robot.

### CI pin profile (compile only — never flash)

`[env:esp32-ci-pins]` (Arduino core 2.x, platform `espressif32@^6.9.0`) and
`[env:esp32-ci-pins-core3]` (Arduino core 3.x from the community platform **pioarduino**
55.03.312-1, needs PlatformIO ≥ 6.1.19; the image has 6.2.0) build with
`-DSCR_CI_PINS_DO_NOT_FLASH` and `include/pins_ci.h`: a FAKE but valid pin set (no flash pins, no
UART0, no GPIO0/2/12, input-only pins only as inputs, triggers on the MCP23017), so that the real
driver code — LEDC, PCNT (legacy driver on 2.x, `pulse_cnt` on 3.x), the I2C task, the expander —
is compiled **and linked** with real pin numbers on both cores (`allAssigned()` reads a volatile that
is never written, so the optimiser cannot drop the driver calls; checked with `nm`: `pcnt_unit_config`,
`ledcSetup` on 2.x, `pcnt_new_unit`, `ledcAttachChannel` on 3.x). Such a firmware:
- does **not** run `motorPinsLowAtBoot()` and starts **no** driver (`allAssigned()` is false): it
  never drives a pin (only UART0, the USB serial port, is used);
- prints `CI build — not flashable` once on the serial port;
- stays in **SAFE for ever**: `MODE DRIVE/TEST`, `SERVO` → `REJECTED_SAFETY`, `BRINGUP` → `REJECTED_MODE`
  (`AppConfig::permanent_safe`, tested natively in `test_sprint5`); DIAG has flag
  `CI_PINS_DO_NOT_FLASH` and `build_env = 2`.

### Release binary for flashing from the Pi

Built on the laptop in Docker (`firmware/tools/build_release.sh`, Git Bash), after
`pins_generated.h` exists:

```
firmware/tools/build_release.sh            # = the command below
firmware/tools/test.sh "pio run -e esp32dev && mkdir -p release && \
  cp \$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/firmware.bin \
     \$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/bootloader.bin \
     \$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/partitions.bin release/ && \
  cp /root/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin release/"
```

It writes `firmware/release/` (git-ignored, never committed). Files and flash offsets for
`esptool` (ESP32, Arduino core 2.x, default partition table, flash mode dio, 40 MHz, 4 MB):

| File | Offset |
|---|---|
| `bootloader.bin` | `0x1000` |
| `partitions.bin` | `0x8000` |
| `boot_app0.bin` | `0xe000` |
| `firmware.bin` | `0x10000` |

```
esptool.py --chip esp32 --port /dev/esp32 --baud 460800 write_flash -z \
  0x1000 bootloader.bin 0x8000 partitions.bin 0xe000 boot_app0.bin 0x10000 firmware.bin
```

After flashing, `robotctl diag` must show version 2.0.0 and the git hash of the build
(`DIAG_REPORT`); `robot/deploy/flash_esp32.sh` does both. Updating only the application:
`write_flash 0x10000 firmware.bin` is enough. NVS (the parameters) is not erased by
`write_flash`; `erase_flash` erases it (all parameters back to the compiled defaults).

### First power-on (tracks off the ground)

Every tunable ships at a value that makes **an uncalibrated robot not move** (params.json defaults:
`METRES_PER_TICK = 0`, gains 0, `MOTOR_MAX_DUTY = 0`, `TEST_MAX_DUTY = 0`, ...). They are raised at
run time with `robotctl` (`docs/BRINGUP.md`), no reflashing:

1. `MOTOR_MAX_DUTY`, `V_DRIVER_DROP` (L298N 2.0 V placeholder), `TEST_MAX_DUTY` small; `MODE TEST`
   + `DRIVE`: open loop. Check each track runs forward for a positive command (`MOTOR_INVERT_*`)
   and all four encoders count **up** (`ENCODER_INVERT_*`).
2. Measure `METRES_PER_TICK`. Only then does the firmware accept `MODE DRIVE`.
3. `RAMP_MM_S2`, `MAX_SPEED_MM_S`, `PID_*` (start with `PID_KFF`, then `PID_KP`, then `PID_KI`).
4. Servo: `SERVO_US_PER_DEG`, limits, pulse limits, slew (bring-up `SERVO_JOG` finds them).
5. `LOW_BATTERY_*`, `BATTERY_CUTOFF_MV` from the pack and BMS data; `BATTERY_VOLTAGE_SCALE` from a multimeter.
6. `CALIBRATE_GYRO` with the robot still; `GYRO_SCALE` from `rotate-test`.

## Runtime parameters (S2.1)

- **Source:** `shared/scr_serial/params.json` (id, name, type, default, **hard min/max**). The
  firmware compiles `lib/params/scr_param_table.h`, generated by
  `cd shared && python -m scr_serial.gen_params_header` (never edit it by hand; a Python test
  checks it is up to date). `config.h` no longer holds these values.
- **Set:** `PARAM_SET {id, value f32}` → `ACK` (`OK`, `OUT_OF_RANGE`, `UNKNOWN_PARAM`, `BUSY`), then
  on OK a `PARAM_REPORT` and, if the value changed, `EVENT PARAM_CHANGED {id}`. Nothing is ever
  outside `[min, max]`; int/bool parameters must be whole numbers; NaN/inf are `OUT_OF_RANGE`.
  `BUSY` while a target or ramp output is non-zero or a measured track speed is above 10 mm/s.
  The value is used from the next control tick on. Setting `METRES_PER_TICK = 0` in `MODE DRIVE`
  switches to `SAFE`.
- **Get:** `PARAM_GET {id}` → one `PARAM_REPORT` + `ACK OK`; `0xFFFF` → all 40 in id order, then `ACK OK`.
- **Save:** `PARAM_SAVE` → writes every value to NVS (Preferences namespace `scr-params`, key
  `p<id>`), `ACK OK`; `REJECTED_MODE` unless SAFE or idle (DRIVE/TEST, targets 0, no encoder change
  for 300 ms); `BUSY` when the NVS write failed. The NVS write runs on the control task (allowed
  only while nothing moves; it may delay that tick).
- **Boot:** compiled defaults, then NVS. A stored value outside the compiled bounds is ignored
  (the default stays). Telemetry flag `PARAMS_UNSAVED` (bit 11) while a value differs from NVS.
- **Hash:** FNV-1a 32 over `(id u16 LE, value f32 LE)` of every parameter in id order — in every
  `TELEMETRY` (`param_hash`) and `DIAG_REPORT`. For the shipped defaults it is **0x8522FADF**
  (`cd shared && python -c "from scr_serial import load_table,param_hash; print(hex(param_hash(load_table().defaults())))"`;
  `test_default_hash_equals_python` checks the C++ value). robotd compares it with the hash of
  the calibration file.

## Motor voltage compensation (S2.2, interface spec A8)

`duty_cap = min(MOTOR_MAX_DUTY, MOTOR_RATED_V / max(V_batt − V_DRIVER_DROP, 0.1 V))`, applied to
|duty| in DRIVE (also the PID's anti-windup limit) and TEST (additionally `TEST_MAX_DUTY`).
`V_batt` is the INA219 bus voltage × `BATTERY_VOLTAGE_SCALE`, filtered **asymmetrically**: a rise
is followed at once (the cap drops at once), a fall only with a 2 s time constant (a sag under
load does not raise the cap at once). Without a valid reading (no INA219, read error, or a value
outside 3..20 V, e.g. USB power only) the full-pack constant **12.6 V** is used and telemetry flag
`VOLTAGE_COMP_FALLBACK` (bit 13) is set. Motor inversion is applied after the cap, at the output.

## Diagnostics (S2.3)

`DIAG_REQUEST` → `DIAG_REPORT` (37 bytes): version 2.0.0, git hash (`tools/git_hash.py`: first 8 hex
digits of `HEAD` at build time, 0 without git), build env (0 esp32dev, 1 compile-check, 2 CI pins,
3 sim-check, 4 native test), driver type (0 L298N), flags (`PINS_CONFIRMED`, `CI_PINS_DO_NOT_FLASH`,
`PARAMS_FROM_NVS`), parameter hash, reset reason (from `esp_reset_reason()`: power-on, brownout,
task watchdog, panic, software, external, other watchdog, deep sleep), uptime, control-loop period
max and jitter (max − min) since the last report, free heap, the I2C scan (up to 8 addresses) and
sensor presence (IMU, INA219, MCP23017, each ultrasonic that ever answered). The I2C scan runs in
the I2C task (at boot and on each request, at most 50 ms), never on the control task; the report
waits at most 250 ms for it. Sent once at boot right after `EVENT BOOT`.

## Robustness (S2.4)

- **Motor pins LOW first:** `setup()` starts with `motorPinsLowAtBoot()` (with the 10 kΩ
  pull-downs on ENA/ENB of `docs/PIN_PLAN.md`).
- **Task watchdog:** control and telemetry tasks are subscribed to `esp_task_wdt` with panic
  (`TASK_WDT_TIMEOUT_MS = 500`; ESP-IDF 4.4 / core 2.x takes whole seconds → 1 s). A hang resets
  the chip; every boot is SAFE. If the telemetry task hangs holding the shared lock, the control
  task blocks on it, stops checking in, and the watchdog resets the chip.
- **I2C never on the control task:** the I2C task (prio 3, core 0) reads the IMU every 10 ms and
  the INA219 every 100 ms with a 5 ms Wire timeout; the drivers only copy the latest values
  (stale after 50 / 500 ms). After 3 failed transfers in a row it clears the bus (up to 9 SCL
  clocks + STOP, up to 3 attempts, at most once a second) and the application sends
  `EVENT I2C_RECOVERED {attempts}`. The I2C task is not on the task watchdog: if it hangs, the
  IMU goes stale (`IMU_FAULT`) and the voltage compensation falls back to 12.6 V; the robot stays
  controllable and the obstacle stop is unaffected (it uses GPIO echoes). With option B, a hung bus
  also means no trigger pulses → sensors "absent" → driving blocked (fail safe).
- **PCNT wrap:** the 16-bit counters reset at ±32767; `scr::pcntDelta` takes the shortest way round
  (tested at both ends and over 100 000 reads), and the 32-bit total wraps with defined
  two's-complement arithmetic.
- **Brownout:** reported as reset reason 2 in DIAG.

## Bring-up modes (S2.6)

`BRINGUP {kind, arg}`, only in `MODE TEST` (else `REJECTED_MODE`), `ACK OK` on success:
- `1 IMU_RAW` (arg = 1..200 Hz): `IMU_RAW` frames with raw gyro x/y/z (µrad/s, robot axes, bias not
  removed, no `GYRO_SCALE`) and accel. Rates above 100 Hz are sent at the control rate (100 Hz).
- `2 US_RAW`: one `US_RAW` per ultrasonic round (4 slots = 120 ms), with the consecutive misses.
- `3 SERVO_JOG` (|arg| ≤ 50 µs, else `BAD_ARGUMENT`): moves the pulse by the step, from the current
  pulse (or `SERVO_CENTER_PULSE_US`), clamped to the compiled 500..2500 µs, ignoring the `SERVO_*`
  limit parameters; answers `EVENT SERVO_PULSE {µs}`. After the jog the servo holds that pulse
  until the next `SERVO` command (no jump).
- `0 OFF`: everything off (accepted in any mode).
Leaving TEST (any reason) or a watchdog stop ends every stream. Telemetry flag `BRINGUP_ACTIVE`
(bit 12). Motor duty in TEST is always capped by `TEST_MAX_DUTY` and the voltage cap; all safety
rules and the 300 ms watchdog keep running. `IMU_RAW` without an IMU, `SERVO_JOG` without a servo
→ `REJECTED_SAFETY`.

**Battery hard cut-off:** `BATTERY_CUTOFF_MV > 0` and the battery below it for 2 s → `MODE SAFE`,
`EVENT BATTERY_CUTOFF {mV}` once; `MODE DRIVE/TEST` → `REJECTED_SAFETY` until the battery is back
above cut-off + 300 mV. 0 = off (the default until the student sets it).

**Slip (S2.5):** per side, over 0.5 s windows: `|front − rear| > SLIP_RATIO × max(|front|, |rear|)`
with `max(|front|, |rear|) > SLIP_MIN_TICKS` → `EVENT SLIP {0 left, 1 right}` once per episode,
re-armed after a window without slip.

## Calibration constants (docs/ODOMETRY.md section 5)

| Constant | Where | How to measure | Shipped value |
|---|---|---|---|
| `metres_per_tick` | runtime parameter `METRES_PER_TICK` (id 1), pushed by robotd from the calibration file (one source; the hash proves both sides agree) | Drive 3 m straight × 5 runs, tape-measure the real distance, divide by the mean encoder ticks (TEST mode works before calibration; the counts are in `TELEMETRY`) | `0` = not calibrated → `MODE DRIVE` refused |
| `track_width` | the Pi's odometry config. The firmware does not use it (it never computes the pose); `TRACK_WIDTH_M` in `config.h` is only a note | 360° in place × 5 runs; used on the Pi for the encoder-only heading that is logged for comparison | `0` = not measured |

The firmware uses `metres_per_tick` only to close the speed loop (ticks → m/s).

## Behaviour where the protocol document is silent

Implemented as follows (also sent to the coordinator; to be confirmed in `docs/SERIAL_PROTOCOL.md`):

- A **rejected `DRIVE` sets the targets to zero** (an older `DRIVE` never keeps running).
- **Obstacle hysteresis** (`OBSTACLE_CLEAR_HYSTERESIS_MM`, default 40, a placeholder to tune; same
  rule as the simulated ESP32 of `robotd`): a blockage starts when the firmware stops or rejects a
  `DRIVE` with the distance **under** the threshold, and is gone only when the distance is **above
  threshold + hysteresis** (front: nearest valid front distance; rear: rear distance with the sensor
  present). While it lasts, forward (front) or reverse (rear) `DRIVE` stays `REJECTED_SAFETY`, so
  noise right at the threshold cannot release and re-trigger the stop. The band is not applied
  before a stop: a robot that was never stopped may drive at threshold + 5 mm.
- `OBSTACLE_STOP` / `REAR_OBSTACLE_STOP` **flags** clear on the next accepted `DRIVE` (for example
  a turn in place) or when the blockage is gone; the **event** is sent once per blockage (an
  accepted turn does not end the blockage). Event `arg` is `-1` when no valid distance exists.
- The rear sensor failing **while** reversing stops the robot with `REAR_OBSTACLE_STOP` (arg `-1`).
- The **watchdog also fires once after power-on** when the Pi sends nothing for 300 ms
  (`WATCHDOG_STOP`, later `LINK_RESTORED`).
- A frame with a good CRC but an **unknown type** is counted as bad, does not feed the watchdog,
  and is answered `ACK UNKNOWN_TYPE`; a **wrong body length** likewise with `ACK BAD_ARGUMENT`.
  ESP32→Pi types arriving at the ESP32 are treated as unknown.
- `MODE` with a value above 2 → `BAD_ARGUMENT`. `MODE DRIVE` without calibration → `REJECTED_MODE`.
  `MODE DRIVE`/`TEST` without a usable motor driver → `REJECTED_SAFETY`. Every mode change zeroes the targets.
- **TEST mode:** `DRIVE` speeds are mapped open loop to duty (`TEST_FULL_SCALE_MM_S`, capped by
  `TEST_MAX_DUTY`). All safety rules still apply, so the first motor test needs working front
  ultrasonics (or the Pi sends turn-in-place commands, `left + right = 0`).
- `DRIVE` targets above `MAX_SPEED_MM_S` are clamped silently (the safety rules look at the
  commanded values before clamping).
- `DRIVE` during a gyro calibration → `BUSY`, targets zero. `CALIBRATE_GYRO` with a dead IMU →
  `ACK OK` followed by `IMU_FAULT` and `GYRO_CAL_FAILED`.
- `SERVO` works in every mode including SAFE; the servo gets **no pulse until the first `SERVO`**
  (no movement at power-on). `SERVO` without a usable servo output → `REJECTED_SAFETY`.
- An ultrasonic sensor is "absent" (`0xFFFE`) until its first completed measurement (about 0.1 s
  after power-on) and after 3 consecutive pings without any echo pulse.
- A silent encoder on a driven track (`ENCODER_FAULT`) stops the robot in DRIVE mode, and `DRIVE` is
  answered `REJECTED_SAFETY` until that encoder counts again; TEST mode only reports.
- Low battery is reported (flag + event) but does not stop the robot; the separate hard cut-off
  (`BATTERY_CUTOFF_MV`, off by default) does.
- The gyro bias is measured only on `CALIBRATE_GYRO` (no automatic re-estimation while stopped).

## What is and is not verified

Verified (in Docker, Linux x86-64, g++, PlatformIO 6.2.0, 2026-11-17):
- `pio test -e native`: all unit tests pass (protocol v2, PID, ramping, servo, gyro calibration,
  ultrasonic scheduling, battery, watchdog, obstacle rules, the application against stubs,
  parameters, voltage compensation, diagnostics, slip, bring-up, cut-off, PCNT wrap).
- `frames_from_cpp.txt` is byte-identical to `frames_from_python.txt` (22 frames, protocol v2).
- `pio run -e sim-check -t exec`: a **SIMULATED** run with stub drivers, including the fault
  injections (stuck I2C, hung telemetry task → watchdog model → reset into SAFE, parameter out of
  range).
- `pio run -e esp32dev` stops with the `#error` of `pins.h` (as intended).

NOT verified:
- **Nothing ran on a real ESP32.** No motor, encoder, sensor, servo, NVS or serial port was ever driven.
- `src/drivers_esp32/*` and `src/main_esp32.cpp` were only **compiled and linked**
  (`esp32-compile-check`, `esp32-ci-pins`: platform espressif32 6.13.0 / Arduino-ESP32 2.0.17;
  `esp32-ci-pins-core3`: pioarduino 55.03.312-1 / Arduino-ESP32 3.3.12 / ESP-IDF 5.5.5; no
  compiler warnings from `src/`, `lib/`, `include/` with `-Wall -Wextra`). Never executed: the task
  watchdog, the I2C task and bus clear, the MCP23017 trigger pulses, NVS, `esp_reset_reason`, the
  PCNT drivers (the wrap arithmetic itself is unit-tested), the echo interrupts, the task timing.
  Register addresses and scale factors of the ICM-20948, INA219 and MCP23017 come from the
  datasheets and were not checked against devices.
- Every parameter default and every `config.h` value marked PLACEHOLDER. Nothing tuned or measured.
- Timing on the target (control-loop jitter, serial buffer behaviour at 460800 baud, NVS write time).
- The simulated plant in `lib/drivers_stub` is arbitrary; it is not a model of the Rover 5.

## Open questions for the student

1. **Motor driver:** L298N (default, `docs/HARDWARE.md`) or VNH: compile-time switch in `config.h`.
   `V_DRIVER_DROP` 2.0 V is the L298N placeholder (VNH ≈ 0.3 V).
2. **Battery cut-off:** `BATTERY_CUTOFF_MV` stays 0 (off) until set from the pack/BMS data.
3. **Expander:** option B (MCP23017 at 0x20 for the triggers) is supported; motor direction lines on
   the expander are refused on purpose.
4. **HC-SR04 at 3.3 V logic:** trigger from 3.3 V is usually accepted, echo needs the level shifter.
5. **Debug output:** the USB serial port carries only protocol frames (and the one-line banner of the
   compile-check / CI builds).
