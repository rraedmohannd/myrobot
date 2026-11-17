# robot — Raspberry Pi 5 runtime

Two processes (architecture: `docs/ROBOT_SOFTWARE.md`):
- **`robotd`** — serial link to the ESP32 (`docs/SERIAL_PROTOCOL.md`), odometry, roaming, return-to-start, the supervisor state machine, the backend link, and the local page for the screen (`http://localhost:8765`).
- **`visiond`** — camera → faces → engagement / active speaker → pan-servo target. Frames never leave it and nothing is stored.

> **Status (sprint 4, 2026-11-17): written before the hardware arrived.** Everything was developed and tested on a Windows laptop against a **simulated ESP32** and synthetic camera data. Nothing here has run on a Raspberry Pi, with a real ESP32, a real serial port, a real camera or a real face. `robot/deploy/setup.sh` has never been executed. Expect to fix things on first contact, and write down what you changed.

## On the laptop (development, no hardware)
```powershell
cd robot
py -m venv .venv
.venv\Scripts\python -m pip install -e ..\shared -e ".[dev]"
.venv\Scripts\python -m pytest -q                      # robotd, visiond and serial-protocol tests

# simulated robot against the backend on this laptop (docker compose up -d)
.venv\Scripts\python -m robotd --sim                   # page: http://localhost:8765/   status: /local/status

# vision (extra packages and models, once)
.venv\Scripts\python -m pip install -e ".[vision]"
.venv\Scripts\python models\download_models.py
.venv\Scripts\python scripts\phone_camera_test.py <phone-ip>      # Android "IP Webcam" app; steps in docs/SPRINT_4_LOG.md
```
`--sim` is labelled everywhere: "SIMULATED" in the log, `is_simulated: true` in `robot.status` and in the stored run, a "محاكاة · SIMULATED" badge on the page and a SIMULATED banner in the admin. A simulated run is never a physical result (CLAUDE.md rule 5).

## First boot on the Raspberry Pi
Raspberry Pi OS Bookworm 64-bit, desktop image, connected to the same network as the laptop that runs the backend.
1. Get the code: `git clone <repository> ~/smart-campus-robot && cd ~/smart-campus-robot && git checkout sprint-4` (or the branch your supervisor has approved).
2. Run the setup script once: `bash robot/deploy/setup.sh` (add `--i2s-amp` only if the MAX98357 amplifier is fitted). It installs packages, creates `robot/.venv`, installs OpenCV / MediaPipe, downloads the two models, installs the udev rule, the two systemd services and the kiosk autostart.
3. Set the backend address and the token: `sudo nano /etc/smart-campus-robot/robot.env`

   | Setting | Meaning |
   |---|---|
   | `BACKEND_URL` | `ws://<laptop address>:8000/ws/robot` — the laptop's fixed address (docs/DEPLOY.md) |
   | `ROBOT_TOKEN` | the same value as `ROBOT_TOKEN` in the backend's `.env`; required because the Pi is not the backend's own machine. Typed on the Pi only, never committed |
   | `ROBOTD_SERIAL_PORT` | `/dev/esp32` (the udev rule's stable name) |
   | `ROBOTD_UI_PORT` | `8765`; the kiosk opens `http://localhost:8765/` |
   | `CAMERA_SOURCE` | `0` for the USB webcam (a URL for a phone camera during development) |
4. On the laptop: open the firewall for port 8000 and start the backend (docs/DEPLOY.md).
5. `sudo reboot`. After the desktop starts, Chromium opens the robot page full screen.
6. Choose the speaker and microphone once: `bash robot/deploy/audio-select.sh`.
7. Check:
   ```bash
   systemctl status robotd visiond
   journalctl -u robotd -f              # live log; "SIMULATED" must NOT appear on the real robot
   curl -s http://localhost:8765/local/status
   ls -l /dev/esp32                     # exists when the ESP32 is plugged in (see 99-esp32.rules if not)
   ```
   Without the ESP32, `robotd` runs, reports a fault (`sensors.esp32_link: fault`) and commands nothing. The page and the conversation still work.

The ESP32 firmware, the pins and the first motor test are a separate procedure: `firmware/README.md` and the checklist at the end of `docs/SPRINT_4_LOG.md`.

## What is in `deploy/`
| File | Purpose |
|---|---|
| `setup.sh` | one-time setup of the Pi (see above) |
| `robot.env.example` | settings template → `/etc/smart-campus-robot/robot.env` |
| `99-esp32.rules` | udev rule: stable name `/dev/esp32` for the DevKit's USB-UART bridge (check the chip with `lsusb`) |
| `robotd.service`, `visiond.service` | systemd units, restart on failure. The motors never depend on a restart: the ESP32 stops by itself 300 ms after `robotd` goes silent |
| `kiosk.sh`, `robot-kiosk.desktop` | Chromium kiosk on the 7" screen, started with the desktop session; touch enabled; the microphone is granted to the local page without a prompt |
| `audio-select.sh` | choose the default speaker (I2S amplifier or USB) and microphone with `wpctl` |

Logs: `journalctl -u robotd` and `journalctl -u visiond` (systemd journal). `/var/log/smart-campus-robot/` is created for files the services may write. `visiond` never writes frames or face data anywhere.

Screen: the Waveshare 7" HDMI (C) is 1024×600 with USB touch. If the desktop does not pick the right mode by itself, follow the manufacturer's page for Raspberry Pi OS Bookworm; this was not tried.

## Updating the code on the Pi
```bash
cd ~/smart-campus-robot
git pull
robot/.venv/bin/pip install -e shared -e robot      # only needed when dependencies changed
sudo systemctl restart robotd visiond
```
The page is copied from the backend when `robotd` starts (and cached for starts without the network), so a backend update reaches the screen after `sudo systemctl restart robotd`.

## Settings reference
`robotd`: `robot/robotd/config.py` (every field can come from a JSON file with `--config`, or from the environment: `BACKEND_URL`, `ROBOT_TOKEN`, `ROBOT_ID`, `ROBOTD_SERIAL_PORT`, `ROBOTD_UI_PORT`, `ROBOTD_UI_HOST`, `ROBOTD_SIM_PORT`, `ROBOTD_ASSETS_DIR`, `ROBOTD_CACHE_DIR`, `ROBOTD_PAGE_BACKEND`). `metres_per_tick`, `track_width_m`, the battery limits and the navigation distances are **placeholders** until the calibration of `docs/ODOMETRY.md` §5; `metres_per_tick` must be the same number as `METRES_PER_TICK` in `firmware/include/config.h`.

`visiond`: `robot/visiond/config.py` and `shared/vision_thresholds.json` (`CAMERA_SOURCE`, `ROBOTD_VISION_URL`, `VISION_LANDMARKS`, `CAMERA_HFOV_DEG`, `CAMERA_MIRROR`, `SERVO_*`). The field of view and the servo limits are placeholders until the real webcam and servo are mounted.
