# Hardware (confirmed parts, v1 — 2026-11-02)

Bought locally (MikroElectron, Amman). Full priced list: project doc `claude/hardware-bom-v1.md`.

| Role | Part |
|---|---|
| Chassis | Rover 5, 4 DC motors (7.2 V) + 4 Hall quadrature encoders, tracks |
| Main computer | Raspberry Pi 5 8GB (owned) + fan |
| Low-level controller | ESP32 DevKit 38-pin, connected to the Pi over **USB serial** |
| Motor drivers | 2× L298N |
| Encoder level shifting | 2× logic level converter (5 V → 3.3 V) |
| IMU | ICM-20948 (gyro used for heading) |
| Obstacles | 4× HC-SR04 (3 front, 1 rear) — borrowed from university |
| Camera | USB webcam (2K, has mic) on MG996R pan servo (servo driven by ESP32) |
| Audio | USB mic; existing speaker (type TBD — add MAX98357 I2S amp if analog) |
| Screen | Waveshare 7" HDMI LCD (C), 1024×600, USB touch |
| Power | 3S Li-ion 18650 + 3S BMS + fuse + switch; 10 A buck → 5 V for Pi; 5 A buck for servo; INA219 on main line |
| USB | 4-port hub (owned). Webcam on a Pi port directly; mic, touch, ESP32 on hub |

**Open point (sprint 4, 2026-11-17):** this table says 2× L298N, while `docs/SPRINT_4.md` names 2 × dual VNH motor-driver shields and INA219 + a 12.6 V pack as provisional. The firmware supports both driver types behind one interface (a build option, see `firmware/README.md`); the student confirms which one is on the robot.

## Pin map — to be confirmed after wiring
Pin assignments are **not defined yet** (CLAUDE.md rule 8). A proposal on paper, with the ESP32 rules and sources, is in [`docs/PIN_PLAN.md`](PIN_PLAN.md) (**PROPOSAL — the firmware does not use that file**). The firmware takes pins **only from this table**, through the checker:

1. Wire the robot. Fill in this table **from the wired robot**: one GPIO number per row (or `X0`…`X15` for an MCP23017 expander pin, on the four ultrasonic **trigger** rows only, option B of `PIN_PLAN.md`), `yes` in "Strapping pin OK?" for GPIO 0, 2, 5, 12 or 15, and your name and the date (`YYYY-MM-DD`) in every "Checked by / date" cell.
2. `python firmware/tools/pins_from_table.py --check` lists every problem with its row. Fix the table until it passes.
3. `python firmware/tools/pins_from_table.py` writes `firmware/include/pins_generated.h` (the only file that defines `PINS_CONFIRMED`). Commit it together with this table.

Until step 3 every pin in `firmware/include/pins.h` is `-1` and the ESP32 build stops with an `#error`. The GPIO column is intentionally empty. The names are the constants of `pins.h`; keep the column headers and the names exactly as they are (the checker reads them).

| Signal | `pins.h` name | Direction (ESP32 side) | Level shifting | ESP32 GPIO | Strapping pin OK? (yes) | Checked by / date |
|---|---|---|---|---|---|---|
| Left motors PWM | `PIN_MOTOR_LEFT_PWM` | out | – | | | |
| Left motors direction A | `PIN_MOTOR_LEFT_DIR_A` | out | – | | | |
| Left motors direction B | `PIN_MOTOR_LEFT_DIR_B` | out | – | | | |
| Right motors PWM | `PIN_MOTOR_RIGHT_PWM` | out | – | | | |
| Right motors direction A | `PIN_MOTOR_RIGHT_DIR_A` | out | – | | | |
| Right motors direction B | `PIN_MOTOR_RIGHT_DIR_B` | out | – | | | |
| Encoder front-left A | `PIN_ENC_FL_A` | in | 5 V → 3.3 V | | | |
| Encoder front-left B | `PIN_ENC_FL_B` | in | 5 V → 3.3 V | | | |
| Encoder rear-left A | `PIN_ENC_RL_A` | in | 5 V → 3.3 V | | | |
| Encoder rear-left B | `PIN_ENC_RL_B` | in | 5 V → 3.3 V | | | |
| Encoder front-right A | `PIN_ENC_FR_A` | in | 5 V → 3.3 V | | | |
| Encoder front-right B | `PIN_ENC_FR_B` | in | 5 V → 3.3 V | | | |
| Encoder rear-right A | `PIN_ENC_RR_A` | in | 5 V → 3.3 V | | | |
| Encoder rear-right B | `PIN_ENC_RR_B` | in | 5 V → 3.3 V | | | |
| I2C data (ICM-20948, INA219) | `PIN_I2C_SDA` | bidirectional | – (3.3 V devices) | | | |
| I2C clock (ICM-20948, INA219) | `PIN_I2C_SCL` | bidirectional | – (3.3 V devices) | | | |
| Ultrasonic front-left trigger | `PIN_US_FL_TRIG` | out | – | | | |
| Ultrasonic front-left echo | `PIN_US_FL_ECHO` | in | 5 V → 3.3 V | | | |
| Ultrasonic front-centre trigger | `PIN_US_FC_TRIG` | out | – | | | |
| Ultrasonic front-centre echo | `PIN_US_FC_ECHO` | in | 5 V → 3.3 V | | | |
| Ultrasonic front-right trigger | `PIN_US_FR_TRIG` | out | – | | | |
| Ultrasonic front-right echo | `PIN_US_FR_ECHO` | in | 5 V → 3.3 V | | | |
| Ultrasonic rear trigger | `PIN_US_REAR_TRIG` | out | – | | | |
| Ultrasonic rear echo | `PIN_US_REAR_ECHO` | in | 5 V → 3.3 V | | | |
| Pan servo signal | `PIN_SERVO_PAN` | out | – | | | |
| I2C expander (MCP23017) address — only with `X` pins (option B); leave empty otherwise | `SCR_EXPANDER_I2C_ADDR` | – | – | | | |

Also to write down after wiring: the I2C addresses actually seen on the bus (scan), which motor driver type is fitted, and the direction sign of each motor and encoder (first motor test, `firmware/README.md`).
Mounting: glue / tape / zip ties; optional printed parts (display case, MG996R holder, HC-SR04 mounts, Pi 5 case).
