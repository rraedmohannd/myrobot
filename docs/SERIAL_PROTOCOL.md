# Pi ↔ ESP32 serial protocol (version 2)

Decided in sprint 4 (2026-11-17), before the hardware exists; version 2 (sprint 5, section 10) adds runtime parameters, diagnostics and bring-up modes. Two implementations, tested against each other:
- Python: `shared/scr_serial/` (used by `robotd` and by the simulated ESP32);
- C++: `firmware/lib/protocol/` (used by the firmware and its native tests).

## 1. Link
- USB serial through the ESP32 DevKit's USB-UART bridge, **460800 baud**, 8N1, no flow control.
- Binary frames. Nothing else is ever written to this port (firmware debug text goes nowhere, or to a second UART chosen after wiring).

## 2. Frame
```
on the wire:  COBS( type u8 | seq u8 | body (0..64 bytes) | crc16 u16 LE )  0x00
```
- **COBS** (Consistent Overhead Byte Stuffing) removes every zero byte from the frame, so `0x00` is the frame delimiter. A receiver that joins in the middle of a frame, or sees a damaged one, resynchronises at the next `0x00`.
- **CRC-16/CCITT-FALSE** over `type | seq | body`: polynomial `0x1021`, start value `0xFFFF`, no reflection, no final XOR. Check value: `"123456789"` → `0x29B1`.
- **seq** is a counter per sender, 0–255, wrapping. The receiver counts gaps (lost frames) for diagnostics; it does not reject frames because of them.
- All integers are little-endian. No padding between fields.
- A frame with a bad CRC, bad COBS, wrong body length or unknown type is dropped and counted. It never feeds the watchdog.

## 3. Pi → ESP32
| type | name | body | meaning |
|---|---|---|---|
| `0x01` | `DRIVE` | `left_mm_s i16`, `right_mm_s i16` | Target track speeds (m/s × 1000), positive = forward. Sent at 20 Hz while moving. Not acknowledged unless rejected. |
| `0x02` | `STOP` | – | Targets to zero now (ramp-down is skipped). Always accepted. Acknowledged. |
| `0x03` | `SERVO` | `pan_cdeg i16` | Camera pan angle in 0.01°, 0 = straight ahead, positive = left. The firmware clamps to its limits and limits the slew rate. Not acknowledged unless rejected. |
| `0x04` | `MODE` | `mode u8` | `0` SAFE (drivers disabled; the power-on state), `1` DRIVE (closed-loop track speeds), `2` TEST (open loop, for the first motor test with the tracks off the ground). Acknowledged. |
| `0x05` | `HEARTBEAT` | – | "The Pi is alive", sent at 10 Hz when nothing else is sent. Not acknowledged. |
| `0x06` | `CALIBRATE_GYRO` | – | Measure the gyro bias for about 2 s. Accepted only while the robot is still (`BUSY` otherwise). Result arrives as an `EVENT`. Acknowledged. |

## 4. ESP32 → Pi
| type | name | body | meaning |
|---|---|---|---|
| `0x81` | `TELEMETRY` | see below (44 bytes) | Raw sensor values at **50 Hz**. |
| `0x82` | `EVENT` | `t_ms u32`, `code u8`, `arg i16` | Something happened (table below). |
| `0x83` | `ACK` | `acked_type u8`, `acked_seq u8`, `status u8` | Answer to `STOP`, `MODE`, `CALIBRATE_GYRO`, and to **any** command that was rejected. |

`TELEMETRY` body:

| field | type | unit / meaning |
|---|---|---|
| `t_ms` | u32 | ms since ESP32 boot |
| `enc[4]` | 4 × i32 | cumulative encoder counts: front-left, rear-left, front-right, rear-right; forward = counting up |
| `gyro_z` | i32 | µrad/s, counter-clockwise positive, bias already removed |
| `accel[3]` | 3 × i16 | mg: x forward, y left, z up |
| `us_mm[4]` | 4 × u16 | ultrasonic distances in mm: front-left, front-centre, front-right, rear. `0xFFFF` = no echo (nothing in range), `0xFFFE` = sensor absent or faulty |
| `battery_mv` | u16 | pack voltage |
| `battery_ma` | i16 | pack current, positive = discharging |
| `flags` | u16 | bit set below |

The ESP32 sends raw values only. The pose is computed on the Pi (docs/ODOMETRY.md).

Flags: bit 0 `MOTORS_ENABLED`, 1 `WATCHDOG_STOP`, 2 `OBSTACLE_STOP`, 3 `LOW_BATTERY`, 4 `IMU_OK`, 5 `GYRO_CALIBRATING`, 6 `SIMULATED` (set by the simulated ESP32 only), 7 `ENCODER_FAULT`, 8 `ULTRASONIC_FAULT`, 9 `POWER_SENSOR_OK`, 10 `REAR_OBSTACLE_STOP`.

Event codes (`arg`): 1 `BOOT`, 2 `WATCHDOG_STOP` (ms since the last valid frame), 3 `OBSTACLE_STOP` (front distance, mm), 4 `REAR_OBSTACLE_STOP` (rear distance, mm), 5 `LOW_BATTERY` (mV), 6 `IMU_FAULT`, 7 `GYRO_CAL_DONE` (bias in 0.1 mrad/s), 8 `GYRO_CAL_FAILED` (the robot moved), 9 `ENCODER_FAULT` (encoder index), 10 `LINK_RESTORED`, 11 `PROTOCOL_ERROR` (bad frames since the last report).

Ack status: 0 `OK`, 1 `REJECTED_MODE`, 2 `BAD_ARGUMENT`, 3 `BUSY`, 4 `UNKNOWN_TYPE`, 5 `REJECTED_SAFETY`.

## 5. Safety rules that live on the ESP32 (CLAUDE.md rule 4)
These hold whatever the Pi or the backend says.
1. **Watchdog.** No valid frame of any kind for **300 ms** → targets to zero, motors stopped, flag `WATCHDOG_STOP`, event `WATCHDOG_STOP`. The next valid frame gives `LINK_RESTORED`; the robot moves again only after a new `DRIVE`.
2. **Front obstacle stop.** While the commanded motion is forward (`left + right > 0`) and the nearest front distance is under the threshold (firmware config, default 250 mm): stop, flag `OBSTACLE_STOP`, event `OBSTACLE_STOP`. Forward `DRIVE` is rejected with `REJECTED_SAFETY` while the front stays blocked. Turning in place and reversing stay allowed, so the Pi can turn away.
3. **Reversing needs rear sensing.** A `DRIVE` with `left + right < 0` is rejected with `REJECTED_SAFETY` when the rear sensor is absent or faulty, and stopped (`REAR_OBSTACLE_STOP`) when the rear distance is under the threshold.
4. **Power-on state is SAFE.** The drivers stay disabled until the Pi sends `MODE DRIVE`.
5. A front sensor that is absent or faulty counts as "blocked" for rule 2 when all front sensors are in that state.
6. **Obstacle hysteresis.** A front or rear blockage starts when the ESP32 stops the robot, or rejects a `DRIVE`, because the distance is under the threshold. It is over only when that distance is above the threshold **plus 40 mm** (firmware config `OBSTACLE_CLEAR_HYSTERESIS_MM`, a placeholder to tune; front: the nearest valid front distance; rear: the rear distance with the sensor working). `0xFFFF` (no echo) counts as clear. While a blockage lasts, forward `DRIVE` (front) or reverse `DRIVE` (rear) is rejected with `REJECTED_SAFETY`, and the `OBSTACLE_STOP` / `REAR_OBSTACLE_STOP` event is sent **once per blockage**, so sensor noise at the threshold cannot release and re-trigger the stop. The hysteresis applies only after a stop: a robot that was never stopped may drive at any distance at or above the threshold. An accepted turn in place clears the flag but does not end the blockage.

## 6. Test frames shared by both implementations
Both sides encode these messages and write one line per frame, `<name> <seq> <hex>`:
- `shared/serial_fixtures/frames_from_python.txt` (`python -m scr_serial.vectors`);
- `shared/serial_fixtures/frames_from_cpp.txt` (written by the firmware native test `test_protocol`).

Each side's tests decode the other side's file and compare every field, and compare the bytes with its own encoding.

| name | seq | message |
|---|---|---|
| `drive` | 1 | `DRIVE` left 0.250 m/s (250), right −0.125 m/s (−125) |
| `stop` | 2 | `STOP` |
| `servo` | 3 | `SERVO` −12.34° (−1234) |
| `mode` | 4 | `MODE` 1 (DRIVE) |
| `heartbeat` | 5 | `HEARTBEAT` |
| `calibrate_gyro` | 6 | `CALIBRATE_GYRO` |
| `telemetry` | 200 | t_ms 123456; enc 1000, −2000, 300000, −4; gyro −12345 µrad/s; accel 12, −34, 1001; us 250, 1200, 0xFFFF, 0xFFFE; 11850 mV; 1520 mA; flags 0x0051 |
| `telemetry_zeros` | 0 | every field zero (44 zero bytes: exercises COBS) |
| `event` | 201 | t_ms 5000, `OBSTACLE_STOP` (3), arg 180 |
| `ack` | 202 | acked_type 0x04, acked_seq 4, status 0 |

Example: `drive` is `04 01 01 fa 05 83 ff 3c 46 00`.

## 7. Timing summary
| what | rate |
|---|---|
| ESP32 control loop (PID, ramps, safety) | 100 Hz |
| `TELEMETRY` | 50 Hz |
| `DRIVE` from the Pi while moving | 20 Hz |
| `HEARTBEAT` from the Pi otherwise | 10 Hz |
| watchdog | 300 ms |

## 8. Simulated ESP32
`robotd --sim` starts a **simulated** ESP32 that speaks exactly this protocol over a local TCP socket. Every telemetry frame it sends has the `SIMULATED` flag; `robotd` then reports `is_simulated: true` everywhere (rule 5).

## 9. Choices made where sections 1–8 are silent (firmware, 2026-11-17)
The firmware README lists all of them; the ones the Pi side must know:
- A frame with a good CRC but an unknown type or a wrong body length is counted, does **not** feed the watchdog, and is answered with `ACK UNKNOWN_TYPE` / `BAD_ARGUMENT`. An out-of-range `MODE` value is a valid frame answered with `BAD_ARGUMENT`. (The Python decoder is stricter with frames it *receives*: an unknown enum value is a `FrameError`.)
- A rejected `DRIVE` zeroes the targets (hard stop); the old targets never stay.
- `OBSTACLE_STOP` / `REAR_OBSTACLE_STOP` flags clear on the next accepted `DRIVE` or when the blockage is gone. The event is sent once per blockage, with `arg = -1` when there is no valid distance (for example the rear sensor failing while reversing).
- The watchdog also fires once 300 ms after power-on if the Pi is silent; the first valid frame then gives `LINK_RESTORED`.
- `MODE DRIVE` is refused with `REJECTED_MODE` while the firmware's `METRES_PER_TICK` is still the placeholder 0 (an uncalibrated robot cannot be driven in closed loop), and `MODE DRIVE` / `TEST` with `REJECTED_SAFETY` when the motor driver has unassigned pins.
- `TEST` mode: `DRIVE` values are mapped to an open-loop duty with a low cap. All rules of section 5 still apply.
- `SERVO` is accepted in every mode; there is no servo pulse before the first `SERVO`.
- `DRIVE` during gyro calibration gets `BUSY` and zero targets. The gyro bias is not re-estimated automatically while stopped; the Pi asks with `CALIBRATE_GYRO`.
- An ultrasonic sensor reports `0xFFFE` until its first finished measurement and after 3 pings without an echo pulse.
- An encoder fault in `DRIVE` mode stops the robot until that encoder counts again. Low battery only reports; the Pi decides.

## 10. Version 2 (sprint 5, 2026-11-17): parameters, diagnostics, bring-up
Additive to version 1: framing, CRC, COBS, sequence numbers, baud rate and the watchdog are unchanged, and every valid frame of any type still feeds the watchdog. Both implementations and both fixture files carry all frames of both versions (section 6 table plus 12 new vectors in `shared/scr_serial/vectors.py`). `f32` = IEEE-754 single precision, little-endian.

### 10.1 Telemetry grows to 48 bytes
`TELEMETRY` gets one field after `flags`: `param_hash u32` (section 10.4). Everything before it is unchanged. A version-1 receiver would reject the frame (wrong length); both sides were updated together.

### 10.2 New Pi → ESP32 messages
| type | name | body | answer |
|---|---|---|---|
| `0x07` | `PARAM_SET` | `id u16`, `value f32` | `ACK` `OK` / `OUT_OF_RANGE` / `UNKNOWN_PARAM` / `BUSY`; after `OK` a `PARAM_REPORT` of that id |
| `0x08` | `PARAM_GET` | `id u16` (`0xFFFF` = all) | one `PARAM_REPORT` per parameter in id order, then `ACK OK` |
| `0x09` | `PARAM_SAVE` | – | `ACK OK` after writing NVS; `REJECTED_MODE` unless the drive mode is SAFE, or both targets are 0 and the tracks are still |
| `0x0A` | `DIAG_REQUEST` | – | `DIAG_REPORT` |
| `0x0B` | `BRINGUP` | `kind u8`, `arg i16` | `ACK`. Kind 0 `OFF` (every bring-up stream off), 1 `IMU_RAW` stream (arg = rate in Hz, 1–200), 2 `US_RAW` stream (one report per ultrasonic round), 3 `SERVO_JOG` (arg = pulse step in µs, \|arg\| ≤ 50, bounded by the compiled range 500–2500 µs, answered with `EVENT SERVO_PULSE`). Kinds 1–3 are refused with `REJECTED_MODE` outside `MODE TEST`. Leaving TEST or a watchdog stop ends every stream. |

`PARAM_SET` is answered `BUSY` while a target is not zero or a track moves faster than 10 mm/s: **no parameter changes while the tracks move.** Nothing can be set outside the compiled `[min, max]`.

### 10.3 New ESP32 → Pi messages
| type | name | body |
|---|---|---|
| `0x84` | `PARAM_REPORT` | `id u16`, `value f32`, `min f32`, `max f32`, `source u8` (0 compiled default, 1 loaded from NVS, 2 set since boot and not saved) — 15 bytes |
| `0x85` | `DIAG_REPORT` | 37 bytes: `fw_major u8`, `fw_minor u8`, `fw_patch u8`, `git_hash u32` (first 8 hex digits of the commit; 0 = unknown), `build_env u8` (0 esp32dev, 1 esp32-compile-check, 2 esp32-ci-pins, 3 sim-check, 4 native test, 5 robotd's simulated ESP32), `driver_type u8` (0 L298N, 1 VNH), `diag_flags u8` (bit 0 PINS_CONFIRMED, 1 CI_PINS_DO_NOT_FLASH, 2 PARAMS_FROM_NVS, 3 SIMULATED), `param_hash u32`, `reset_reason u8` (0 unknown, 1 power-on, 2 brownout, 3 task watchdog, 4 panic, 5 software, 6 reset pin, 7 other watchdog, 8 deep-sleep wake), `uptime_ms u32`, `loop_period_max_us u16`, `loop_jitter_us u16`, `free_heap u32`, `i2c_count u8`, `i2c_addr[8] u8` (7-bit addresses found by the scan, unused slots 0), `sensors u8` (bit 0 IMU, 1 INA219, 2 I2C expander, 3–6 ultrasonic 0–3 answered at least once). Also sent once at boot, right after `EVENT BOOT`. |
| `0x86` | `IMU_RAW` | `t_ms u32`, `gyro[3] i32` (µrad/s, robot axes forward/left/up, bias **not** removed), `accel[3] i16` (mg) — 22 bytes |
| `0x87` | `US_RAW` | `t_ms u32`, `dist_mm[4] u16` (special values as in telemetry), `misses[4] u8` (consecutive misses per sensor) — 16 bytes |

New event codes: 12 `SLIP` (arg: side, 0 left, 1 right; once per episode: \|front − rear\| ticks of one side over a 0.5 s window above `SLIP_RATIO` × the larger of the two and above `SLIP_MIN_TICKS`), 13 `I2C_RECOVERED` (arg: bus-clear attempts), 14 `PARAM_CHANGED` (arg: id), 15 `SERVO_PULSE` (arg: pulse µs), 16 `BATTERY_CUTOFF` (arg: mV; the motors go to SAFE; only when `BATTERY_CUTOFF_MV` > 0).
New `ACK` status values: 6 `OUT_OF_RANGE`, 7 `UNKNOWN_PARAM`.
New telemetry flags: bit 11 `PARAMS_UNSAVED`, 12 `BRINGUP_ACTIVE`, 13 `VOLTAGE_COMP_FALLBACK` (no INA219 reading; the duty cap is computed for a full 12.6 V pack).

### 10.4 Runtime parameters and their hash
- The table is `shared/scr_serial/params.json` (40 parameters: id, name, unit, type, default, min, max, group, the `robotctl` step that measures it, whether the calibration needs it). It is the **single source**: the firmware compiles `firmware/lib/params/scr_param_table.h`, generated by `python -m scr_serial.gen_params_header`, and a Python test fails when the header is out of date.
- The defaults are the sprint 4 placeholders: **an uncalibrated robot does not move** (`METRES_PER_TICK`, the PID gains, ramp, maximum speed, `TEST_MAX_DUTY` and `MOTOR_MAX_DUTY` are 0). `OBSTACLE_THRESHOLD_MM` cannot be set below 150 mm.
- Values persist in NVS after `PARAM_SAVE`.
- **Hash:** FNV-1a 32-bit (offset `0x811C9DC5`, prime `0x01000193`) over, for every parameter in ascending id order, `id u16 LE` followed by the current `value f32 LE`. Same function in Python (`scr_serial.param_hash`) and C++. `robotd` pushes the calibration values at every connection and compares the hash it expects with the hash in telemetry, so the Pi and the ESP32 can no longer disagree about `METRES_PER_TICK` (the sprint 4 problem).

### 10.5 Motor voltage compensation
`duty_cap = min(MOTOR_MAX_DUTY, MOTOR_RATED_V / max(V_batt − V_DRIVER_DROP, 0.1))`. `V_batt` comes from the INA219 (filtered), or is taken as 12.6 V (a full pack, the most conservative value) when the INA219 has no valid reading (flag 13). It applies to DRIVE and TEST mode; TEST is also capped by `TEST_MAX_DUTY`. `MOTOR_RATED_V` 7.2 V is the Rover 5 datasheet value; `V_DRIVER_DROP` is a placeholder (L298N 2.0 V, VNH about 0.3 V).

### 10.6 Choices made where 10.1–10.5 are silent (firmware, 2026-11-17)
- **`PARAM_SET`:** unknown id and bounds are checked first, then `BUSY`. `int` and `bool` values must be whole numbers; NaN and infinity are `OUT_OF_RANGE`; −0.0 is stored as +0.0. "Moving" = a target or ramp output not zero, or a measured track speed above 10 mm/s. The `ACK` comes before the `PARAM_REPORT`; `PARAM_CHANGED` is sent only when the stored value really changed. Setting `METRES_PER_TICK` to 0 while in DRIVE switches the drive mode to SAFE.
- **`PARAM_SAVE`:** "tracks still" = no encoder change for 300 ms. A failed NVS write (or no storage) is answered `BUSY`. After a save, source `SET` becomes `NVS`; a value still at its compiled default keeps `DEFAULT`. A stored value outside the bounds is ignored at boot. `PARAMS_UNSAVED` compares with the NVS value, or the compiled default when NVS has none.
- **`DIAG_REQUEST`:** no `ACK`; the answer is the report, sent when a fresh I2C scan finishes (at most 250 ms later). Loop period maximum and jitter (max − min) are counted since the previous report.
- **`BRINGUP`:** an unknown kind, an IMU rate outside 1–200 or \|jog\| > 50 is `BAD_ARGUMENT`; `OFF` is accepted in any mode; `IMU_RAW` without an IMU or `SERVO_JOG` without a servo is `REJECTED_SAFETY`; IMU rates above 100 Hz are sent at the control rate (100 Hz); `IMU_RAW` has no `GYRO_SCALE` applied; the jog starts from the last pulse written (or `SERVO_CENTER_PULSE_US`) and holds until the next `SERVO`. Any exit from TEST mode (including the battery cut-off) and the watchdog stop end every bring-up stream.
- **`BATTERY_CUTOFF`:** the battery must stay below `BATTERY_CUTOFF_MV` for 2 s; the event is sent once, the drive mode goes to SAFE, and `MODE DRIVE/TEST` is `REJECTED_SAFETY` until the battery is 300 mV above the cut-off.
- **Slip:** tumbling 0.5 s windows on the signed, inversion-corrected tick sums; condition `max(|f|, |r|) > SLIP_MIN_TICKS` and `|f − r| > SLIP_RATIO × max(|f|, |r|)`.
- **Voltage compensation:** the battery reading is followed at once when it rises and with a 2 s time constant when it falls; a reading outside 3–20 V counts as missing (12.6 V fallback, flag 13). The cap is also the PID's output limit; the motor inversion is applied after the cap.
- **Task watchdog:** 500 ms on Arduino core 3.x; core 2.x (ESP-IDF 4.4) takes whole seconds, so 1 s there. After any reset the firmware boots in SAFE.
- **CI pin profile** (`esp32-ci-pins`, `esp32-ci-pins-core3`, never flashable): permanent SAFE, `SERVO` is `REJECTED_SAFETY`, `BRINGUP` is `REJECTED_MODE`.
- A message type of the ESP32 → Pi direction arriving at the ESP32 is answered `ACK UNKNOWN_TYPE`.

### 10.7 Rules added after the independent safety review (2026-11-17)
- **Motion means targets or ramp.** The front obstacle rule and the reverse rule look at the DRIVE targets **and** at the ramp outputs the motors are still following (0.5 mm/s dead band). A `DRIVE 0 0` that is still ramping down, or a turn that still carries forward or backward motion, is stopped by an obstacle like any other motion. A symmetric turn in place from standstill stays allowed.
- **Encoder inversion change:** changing `ENCODER_INVERT_*` drops the encoder baseline, so the next delta is not a phantom jump (`robotd` also re-baselines its odometry on `PARAM_CHANGED` for ids 15–18).
- **Expander trigger failure:** when the trigger pulse of an ultrasonic sensor cannot be sent (MCP23017 write failed, expander not set up, or I2C task not running), that sensor reads ABSENT (`0xFFFE`) from the end of that slot; it does not wait for 3 misses. It recovers with its next completed measurement.
- **Late echo:** at the end of a slot, an echo still high that rose less than `US_ECHO_TIMEOUT_US` (25 ms) ago means the ping went out late; it counts as a miss (the last reading is kept; 3 misses → ABSENT) and is never reported as no echo (`0xFFFF`).
- **Centre front sensor (stricter than rule 5):** forward motion is blocked (`REJECTED_SAFETY`; running forward motion stopped with `OBSTACLE_STOP`) when the centre front sensor alone is absent or faulty, even if both side sensors work. The event argument is the nearest valid side distance, or −1.
- **Speed-sign mismatch (MODE DRIVE only):** a track driven with \|duty\| > 0.2 whose measured speed has the opposite sign and exceeds 50 mm/s for 300 ms (placeholders in `config.h`) → stop, MODE SAFE, `EVENT ENCODER_FAULT` with arg 10 (left) or 11 (right). The Pi must send `MODE DRIVE` again after the cause is fixed. Not checked in MODE TEST, so `robotctl motor-test` can discover inverted motors and encoders.
- **`PARAM_SAVE`** writes one parameter per control period (about 0.41 s for 40 parameters and the commit) and feeds the task watchdog; the `ACK` comes when the save has finished (`OK`, or `BUSY` if an NVS write failed). During a save `PARAM_SET` and another `PARAM_SAVE` are `BUSY`; `PARAMS_UNSAVED` stays set until the commit. An interrupted save can leave old and new values mixed in NVS; every stored value is within bounds and is checked again at boot. Timeouts on the Pi side must be ≥ 1 s (`robotctl` uses 3 s).
- **Voltage compensation floor:** a battery reading below 9.0 V or above 20 V counts as no reading (12.6 V full-pack constant, flag 13); a 3S pack cannot legitimately read below 9.0 V while running.
- The serial transmit buffer is 2 KB, so `PARAM_GET` of all parameters does not block the control task.
- **On the Pi side:** `robotd` sends `STOP` first on `LINK_RESTORED` (stale `DRIVE` frames from before a stall never act), sends `DRIVE` at 20 Hz whenever it wants motion (it never relies on `HEARTBEAT` to keep motion alive) and `STOP` to stop, and treats a missing centre sensor as "front blocked".
- **Pin table:** the checker refuses GPIO 1, 3, 5, 14 and 15 on the motor rows (pins that are UART or reported to output signals during boot).
