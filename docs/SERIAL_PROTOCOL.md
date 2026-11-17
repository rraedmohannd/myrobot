# Pi ↔ ESP32 serial protocol (version 1)

Decided in sprint 4 (2026-11-17), before the hardware exists. Two implementations, tested against each other:
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
