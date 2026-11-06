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

Pin assignments: **not defined yet** — fill in after wiring is verified.
Mounting: glue / tape / zip ties; optional printed parts (display case, MG996R holder, HC-SR04 mounts, Pi 5 case).
