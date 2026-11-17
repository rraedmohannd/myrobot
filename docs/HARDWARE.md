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
Pin assignments are **not defined yet** (CLAUDE.md rule 8). The firmware keeps every pin in `firmware/include/pins.h` with the value `-1` and refuses to compile for the ESP32 until `PINS_CONFIRMED` is defined. Fill in this table **from the wired robot**, copy the numbers into `pins.h`, then define `PINS_CONFIRMED` (procedure in `firmware/README.md`).

The GPIO column is intentionally empty. The names are the constants of `pins.h`.

| Signal | `pins.h` name | Direction (ESP32 side) | Level shifting | ESP32 GPIO | Checked by / date |
|---|---|---|---|---|---|
| Left motors PWM | `PIN_MOTOR_LEFT_PWM` | out | – | | |
| Left motors direction A | `PIN_MOTOR_LEFT_DIR_A` | out | – | | |
| Left motors direction B | `PIN_MOTOR_LEFT_DIR_B` | out | – | | |
| Right motors PWM | `PIN_MOTOR_RIGHT_PWM` | out | – | | |
| Right motors direction A | `PIN_MOTOR_RIGHT_DIR_A` | out | – | | |
| Right motors direction B | `PIN_MOTOR_RIGHT_DIR_B` | out | – | | |
| Encoder front-left A / B | `PIN_ENC_FL_A`, `PIN_ENC_FL_B` | in | 5 V → 3.3 V | | |
| Encoder rear-left A / B | `PIN_ENC_RL_A`, `PIN_ENC_RL_B` | in | 5 V → 3.3 V | | |
| Encoder front-right A / B | `PIN_ENC_FR_A`, `PIN_ENC_FR_B` | in | 5 V → 3.3 V | | |
| Encoder rear-right A / B | `PIN_ENC_RR_A`, `PIN_ENC_RR_B` | in | 5 V → 3.3 V | | |
| I2C data / clock (ICM-20948, INA219) | `PIN_I2C_SDA`, `PIN_I2C_SCL` | bidirectional | – (3.3 V devices) | | |
| Ultrasonic front-left trigger / echo | `PIN_US_FL_TRIG`, `PIN_US_FL_ECHO` | out / in | echo 5 V → 3.3 V | | |
| Ultrasonic front-centre trigger / echo | `PIN_US_FC_TRIG`, `PIN_US_FC_ECHO` | out / in | echo 5 V → 3.3 V | | |
| Ultrasonic front-right trigger / echo | `PIN_US_FR_TRIG`, `PIN_US_FR_ECHO` | out / in | echo 5 V → 3.3 V | | |
| Ultrasonic rear trigger / echo | `PIN_US_REAR_TRIG`, `PIN_US_REAR_ECHO` | out / in | echo 5 V → 3.3 V | | |
| Pan servo signal | `PIN_SERVO_PAN` | out | – | | |

Also to write down after wiring: the I2C addresses actually seen on the bus (scan), which motor driver type is fitted, and the direction sign of each motor and encoder (first motor test, `firmware/README.md`).
Mounting: glue / tape / zip ties; optional printed parts (display case, MG996R holder, HC-SR04 mounts, Pi 5 case).
