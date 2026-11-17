# ESP32 firmware (sprint 4, R2)

Low-level controller of the Smart Campus Robot: motors, encoders, IMU, ultrasonics, pan servo,
battery sensing, the serial link to the Raspberry Pi, and the **local safety rules**.
PlatformIO project, Arduino-ESP32 core, board: ESP32 DevKit (38-pin).

> **Status: written before the hardware existed. Nothing in this directory has run on a real
> ESP32.** What was verified, and how, is listed under "What is and is not verified".

The contract with the Pi is `docs/SERIAL_PROTOCOL.md` (version 1). The ESP32 sends **raw values
only**; the pose is computed on the Pi (`docs/ODOMETRY.md`).

## Layout

```
firmware/
  platformio.ini            environments: esp32dev, esp32-compile-check, native, sim-check
  include/
    pins.h                  THE ONLY FILE WITH PIN NUMBERS - all -1, #error without PINS_CONFIRMED
    config.h                every tunable, all marked PLACEHOLDER until measured/calibrated
  lib/                      hardware-independent C++ (no Arduino headers) - compiles natively
    protocol/               link: COBS, CRC-16/CCITT-FALSE, seq, all messages, stream decoder
    control/                speed_pid (PID), ramp limiter, servo limits/slew, gyro bias
                            calibration, battery monitor, encoder fault detector
    ultrasonic/             HC-SR04 scheduling: one sensor per slot (staggered), timeouts, faults
    safety/                 watchdog, front/rear obstacle rules, SAFE mode (protocol section 5)
    hal/                    driver interfaces (motors, encoders, imu, ultrasonic, servo, power, link)
    app/                    the application: control tick (100 Hz) and telemetry tick (50 Hz)
    drivers_stub/           STUB drivers + a crude simulated plant, for tests and sim-check
  src/
    main_esp32.cpp          ESP32 entry: real drivers, two FreeRTOS tasks (control 100 Hz, telemetry 50 Hz)
    drivers_esp32/          real drivers: LEDC PWM motors + servo, PCNT encoders, ICM-20948,
                            INA219, HC-SR04 by interrupts, the USB serial link
    main_sim.cpp            sim-check entry: application + stubs, short SIMULATED smoke run
  test/
    test_protocol/          codec tests + cross-test with the Python implementation
    test_control/           PID, ramping, servo, gyro calibration, ultrasonic scheduling, battery
    test_safety/            watchdog, obstacle stop (front, rear, reverse without rear sensor), SAFE
    test_app/               whole application against the stubs
  tools/
    Dockerfile, test.sh, test.ps1   run everything in Docker (no compiler needed on the host)
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

### The PINS_CONFIRMED procedure

1. Wire the robot. Write every connection into the "Pin map - to be confirmed after wiring" table
   in `docs/HARDWARE.md`.
2. Copy the numbers into `firmware/include/pins.h` (the names in the table and in the file are the
   same). It is the only file that may contain pin numbers.
3. In `platformio.ini`, add `-DPINS_CONFIRMED` to `build_flags` of `[env:esp32dev]`.
4. `pio run -e esp32dev`, then `pio run -e esp32dev -t upload` with the DevKit on USB.

Guards:
- **Compile time:** without `PINS_CONFIRMED`, `pins.h` stops the `esp32dev` build with an `#error`.
- **Run time:** a driver whose pin is still negative does not start (`scr::pins::allAssigned`).
  Without a usable motor driver `MODE DRIVE`/`MODE TEST` are answered `REJECTED_SAFETY`; a missing
  ultrasonic sensor is reported as absent (`0xFFFE`), which blocks forward driving when all three
  front sensors are missing and blocks reversing when the rear one is missing.
- `native` and `sim-check` never include `pins.h`.
- `[env:esp32-compile-check]` (`-DPINS_COMPILE_CHECK_ONLY`) only proves that the ESP32 sources
  compile. All pins stay `-1`, every driver refuses to start, and the firmware prints one line on
  the serial port saying so. Do not flash it onto the robot.

### First power-on (tracks off the ground)

`include/config.h` ships with placeholders chosen so that **an uncalibrated robot does not move**.
Raise them one at a time:

1. Decide the motor driver type (`SCR_MOTOR_DRIVER_VNH` or `SCR_MOTOR_DRIVER_L298N`) and how the
   7.2 V motors are protected from the 12.6 V pack, then set `MOTOR_MAX_DUTY`.
2. `TEST_MAX_DUTY`, `RAMP_MM_S2`, `MAX_SPEED_MM_S` small; send `MODE TEST` and `DRIVE`: open loop.
   Check that each track runs forward for a positive command (`MOTOR_INVERT_*`) and that all four
   encoder counts go **up** (`ENCODER_INVERT`).
3. Measure `METRES_PER_TICK` (below). Only then does the firmware accept `MODE DRIVE`.
4. Tune `PID_*` (start with `PID_KFF`, then `PID_KP`, then `PID_KI`).
5. Servo: `SERVO_US_PER_DEG`, limits, pulse limits, slew; check that positive = left.
6. `LOW_BATTERY_MV` / `LOW_BATTERY_RECOVER_MV` from the pack and BMS data.
7. `CALIBRATE_GYRO` with the robot still; check `GYRO_CAL_MAX_SPREAD_URAD_S` and the IMU axis mapping.

## Calibration constants (docs/ODOMETRY.md section 5)

| Constant | Where | How to measure | Shipped value |
|---|---|---|---|
| `metres_per_tick` | `firmware/include/config.h` (`METRES_PER_TICK`) **and** the Pi's odometry config - the two must be equal | Drive 3 m straight × 5 runs, tape-measure the real distance, divide by the mean encoder ticks (TEST mode works before calibration; the counts are in `TELEMETRY`) | `0` = not calibrated → `MODE DRIVE` refused |
| `track_width` | the Pi's odometry config. The firmware does not use it (it never computes the pose); `TRACK_WIDTH_M` in `config.h` is only a note | 360° in place × 5 runs; used on the Pi for the encoder-only heading that is logged for comparison | `0` = not measured |

The firmware uses `metres_per_tick` only to close the speed loop (ticks → m/s).

## Behaviour where the protocol document is silent

Implemented as follows (also sent to the coordinator; to be confirmed in `docs/SERIAL_PROTOCOL.md`):

- A **rejected `DRIVE` sets the targets to zero** (an older `DRIVE` never keeps running).
- `OBSTACLE_STOP` / `REAR_OBSTACLE_STOP` **flags** clear on the next accepted `DRIVE` or when the
  blocking condition is gone; the **event** is sent once per blockage. Event `arg` is `-1` when no
  valid distance exists.
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
- Low battery is reported (flag + event) but does not stop the robot.
- The gyro bias is measured only on `CALIBRATE_GYRO` (no automatic re-estimation while stopped).

## What is and is not verified

Verified (in Docker, Linux x86-64, g++):
- `pio test -e native`: all unit tests pass (protocol, PID, ramping, servo, gyro calibration,
  ultrasonic scheduling, battery, watchdog, obstacle rules, the application against stubs).
- `frames_from_cpp.txt` is byte-identical to `frames_from_python.txt`.
- `pio run -e sim-check` builds; its run is a **SIMULATED** scenario with stub drivers.
- `pio run -e esp32dev` stops with the `#error` of `pins.h` (as intended).

NOT verified:
- **Nothing ran on a real ESP32.** No motor, encoder, sensor, servo or serial port was ever driven.
- `src/drivers_esp32/*` and `src/main_esp32.cpp` were only **compiled and linked**
  (`pio run -e esp32-compile-check`, in Docker, 2026-11-17: PlatformIO platform espressif32 6.13.0,
  Arduino-ESP32 core 2.0.17, no warnings with `-Wall -Wextra`). They were never executed.
  Register addresses and scale factors of the ICM-20948 and the INA219 come from the datasheets
  and were not checked against devices. The PCNT wrap handling, the echo interrupts and the task
  timing are untested. The code path for Arduino-ESP32 core 3.x (different LEDC API) was not compiled.
- Every value in `config.h` marked PLACEHOLDER. No PID gain, speed, threshold or servo constant
  has been tuned or measured.
- Timing on the target (control-loop jitter, I2C read time inside the 10 ms period, serial
  buffer behaviour at 460800 baud).
- The simulated plant in `lib/drivers_stub` is arbitrary; it is not a model of the Rover 5.

## Open questions for the student

1. **Motor driver:** `docs/SPRINT_4.md` says 2 × dual VNH shields, `docs/HARDWARE.md` says 2 × L298N.
   The code supports both by a compile-time switch in `config.h` (default: L298N).
2. **Motor voltage:** 7.2 V motors on a 12.6 V pack - what limits the motor voltage?
3. **Pin budget:** 6 motor + 8 encoder + 8 ultrasonic + 2 I2C + 1 servo = 25 signals. That is close
   to what a 38-pin DevKit can offer once input-only, boot-strapping and flash pins are excluded.
   Check the DevKit's pin table before wiring. The firmware already drives each side with one
   PWM + two direction signals (front and rear driver channels of a side wired together).
4. **Encoders:** one PCNT unit per encoder (4 units, full quadrature). Both encoder signals need
   the 5 V → 3.3 V level shifters: 8 channels.
5. **I2C:** the ICM-20948 and the INA219 share one bus; check their addresses
   (`IMU_I2C_ADDRESS`, `INA219_I2C_ADDRESS`) and that the breakouts have pull-ups to 3.3 V.
6. **HC-SR04 at 3.3 V logic:** trigger from 3.3 V is usually accepted, echo needs the level shifter.
7. **Debug output:** the USB serial port carries only protocol frames. A second UART for debug
   text can be added after wiring.
