# robot — Raspberry Pi 5 runtime

Two processes (architecture: `docs/ROBOT_SOFTWARE.md`):
- **`robotd`** — serial link to the ESP32 (`docs/SERIAL_PROTOCOL.md`), odometry, roaming, return-to-start, the supervisor state machine, the backend link, and the local page for the screen (`http://localhost:8765`).
- **`visiond`** — camera → faces → engagement / active speaker → pan-servo target. Frames never leave it and nothing is stored.

> **Status (sprint 5, 2026-11-17): written before the hardware arrived.** Everything was developed and tested on a Windows laptop against a **simulated ESP32** and synthetic camera data. Nothing here has run on a Raspberry Pi, with a real ESP32, a real serial port, a real camera or a real face. `robot/deploy/setup.sh` has run only in its `--container-test` form, inside an **emulated arm64 Debian container** (see "Install test without a Pi"); that is not a Raspberry Pi. Expect to fix things on first contact, and write down what you changed.

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
Raspberry Pi OS 64-bit **with desktop** (the release of 2026-10-06 is based on Debian 13 "trixie" with Python 3.13; the dependency lock is made for exactly that), connected to the same network as the laptop that runs the backend, or to the laptop's hotspot (docs/DEPLOY.md).
1. Get the code: `git clone <repository> ~/smart-campus-robot && cd ~/smart-campus-robot && git checkout sprint-4` (or the branch your supervisor has approved).
2. Run the setup script once: `bash robot/deploy/setup.sh` (add `--i2s-amp` only if the MAX98357 amplifier is fitted). It installs the apt packages, creates `robot/.venv`, installs **every Python package from `robot/requirements.lock`** (pinned versions, sha256 hashes checked; nothing is resolved on the Pi), downloads the two models, installs the udev rule, the two systemd services, and the kiosk (`deploy/kiosk-setup.sh`: labwc autostart with an X11 fallback, invisible cursor, touch mapping, screen blanking off). If the system Python is not 3.13, it installs a pinned Python 3.13 with `uv` for the virtualenv (this path was not run).
3. Set the backend address and the token: `sudo nano /etc/smart-campus-robot/robot.env`

   | Setting | Meaning |
   |---|---|
   | `BACKEND_URL` | `ws://<laptop address>:8000/ws/robot` — the laptop's fixed address (docs/DEPLOY.md). Several addresses may be listed, separated by commas (no spaces), tried in order: e.g. the laptop's hotspot first, then the site network. No `?token=` in them. Server mode: `wss://<domain>/ws/robot` (docs/DEPLOY.md "Server mode"), certificate always verified |
   | `ROBOT_TOKEN` | the same value as `ROBOT_TOKEN` in the backend's `.env`; required because the Pi is not the backend's own machine. Typed on the Pi only, never committed |
   | `ROBOTD_SERIAL_PORT` | `/dev/esp32` (the udev rule's stable name) |
   | `ROBOTD_UI_PORT` | `8765`; the kiosk opens `http://localhost:8765/` |
   | `CAMERA_SOURCE` | `0` for the USB webcam (a URL for a phone camera during development) |
4. On the laptop: open the firewall for port 8000 and start the backend (docs/DEPLOY.md).
5. Plug in the screen (HDMI + USB touch). If setup said "no touch screen found", run `bash robot/deploy/kiosk-setup.sh` again now. `sudo reboot`. After the desktop starts, Chromium opens the robot page full screen.
6. Choose the speaker and microphone once: `bash robot/deploy/audio-select.sh`.
7. Check:
   ```bash
   systemctl status robotd visiond
   journalctl -u robotd -f              # live log; "SIMULATED" must NOT appear on the real robot
   curl -s http://localhost:8765/local/status
   ls -l /dev/esp32                     # exists when the ESP32 is plugged in (see 99-esp32.rules if not)
   ```
   Without the ESP32, `robotd` runs, reports a fault (`sensors.esp32_link: fault`) and commands nothing. The page and the conversation still work.

The ESP32 firmware, the pins and the first motor test are a separate procedure: `firmware/README.md` and the step-by-step bring-up in `docs/BRINGUP.md`. Flashing from the Pi: "Flashing the ESP32 from the Pi" below.

### The screen (kiosk)
Raspberry Pi OS runs **Wayland with the labwc compositor** on all models by default (official documentation, "Switch between Wayland and X11"). X11 is still selectable (`sudo raspi-config` -> 6 Advanced Options -> A7 Wayland -> W1 X11, not recommended). `deploy/kiosk-setup.sh` prepares both:

| | labwc (default) | X11 fallback |
|---|---|---|
| start | `~/.config/labwc/autostart` runs `kiosk.sh` | `~/.config/autostart/robot-kiosk.desktop` runs `kiosk.sh` |
| no cursor | invisible cursor theme `scr-invisible` (`deploy/make_invisible_cursor.py`) selected by `XCURSOR_THEME` in `~/.config/labwc/environment`; labwc has no "hide cursor" option | `unclutter` |
| no blanking | `sudo raspi-config nonint do_blanking 1` (removes the `swayidle` line that raspi-config manages in `~/.config/labwc/autostart`) | the same command (installs `10-blanking.conf`), plus `xset s off -dpms` in `kiosk.sh` |
| touch -> screen | `<touch deviceName="..." mapToOutput="HDMI-A-1" mouseEmulation="no" />` in `~/.config/labwc/rc.xml` | `xinput map-to-output` with `TOUCH_DEVICE` from `robot.env` |

`kiosk.sh` takes a lock, so Chromium starts once even if both autostart methods run. **Not checked (no Pi and no screen yet):** that this labwc session reads the user `autostart` and `environment` files together with the system ones; the touch device name; the output name `HDMI-A-1`; the 1024x600 mode (if wrong: Control Centre -> Screens); that Chromium on Wayland shows no cursor with the blank theme. Overrides: `KIOSK_OUTPUT=HDMI-A-2 TOUCH_DEVICE="<name>" bash robot/deploy/kiosk-setup.sh`; device names are in `cat /proc/bus/input/devices`. With a single HDMI screen the touch covers that screen even without the mapping.

## What is in `deploy/`
| File | Purpose |
|---|---|
| `setup.sh` | one-time setup of the Pi (see above) |
| `robot.env.example` | settings template → `/etc/smart-campus-robot/robot.env` |
| `99-esp32.rules` | udev rule: stable name `/dev/esp32` for the DevKit's USB-UART bridge (check the chip with `lsusb`) |
| `robotd.service`, `visiond.service` | systemd units, restart on failure. The motors never depend on a restart: the ESP32 stops by itself 300 ms after `robotd` goes silent |
| `kiosk.sh`, `robot-kiosk.desktop` | Chromium kiosk on the 7" screen, started with the desktop session; touch enabled; the microphone is granted to the local page without a prompt |
| `kiosk-setup.sh`, `make_invisible_cursor.py` | kiosk autostart for labwc and X11, invisible cursor, touch mapping, blanking off (called by `setup.sh`) |
| `update.sh` | pull, reinstall the locked dependencies if the lock changed, restart the services, offer to flash the ESP32 if its version differs |
| `flash_esp32.sh` | flash a firmware release built on the laptop, then check the version through `DIAG` |
| `fw_version.sh` | prints the firmware version of the checkout (from `firmware/lib/app/scr_version.h`) |
| `lock.sh` | regenerate `robot/requirements.lock` (on any machine with `uv`) |
| `audio-select.sh` | choose the default speaker (I2S amplifier or USB) and microphone with `wpctl` |

Logs: `journalctl -u robotd` and `journalctl -u visiond` (systemd journal). `/var/log/smart-campus-robot/` is created for files the services may write. `visiond` never writes frames or face data anywhere.

Screen: the Waveshare 7" HDMI (C) is 1024×600 with USB touch. If the desktop does not pick the right mode by itself, follow the manufacturer's page for Raspberry Pi OS Bookworm; this was not tried.

## Updating the code on the Pi
```bash
cd ~/smart-campus-robot
bash robot/deploy/update.sh       # options: --firmware-dir DIR (default ~/firmware-release), --no-firmware-check
```
It pulls (fast-forward only), reinstalls the locked dependencies only if `robot/requirements.lock` changed since the last install, restarts `robotd` and `visiond`, then compares the ESP32's firmware version (`python -m robotctl diag --expect-version <version in firmware/lib/app/scr_version.h>`, read by `deploy/fw_version.sh`) with the repository's. If they differ it **offers** to flash the release in `~/firmware-release` (asks first, default no; never flashes without a terminal).
The page is copied from the backend when `robotd` starts (and cached for starts without the network), so a backend update reaches the screen after `sudo systemctl restart robotd`.

## Dependencies: the lock file
`robot/requirements.lock` pins **every** package, direct and transitive (52, including mediapipe, OpenCV, numpy, esptool, pytest), with sha256 hashes, for linux aarch64 and Python 3.13. `setup.sh` and `update.sh` install it with `pip install --require-hashes`, then add `shared/` and `robot/` as editable packages without dependencies. To change a version: edit the range in `robot/pyproject.toml`, `pip install "uv==0.13.9"`, `bash robot/deploy/lock.sh` (works on the Windows laptop too), then run the container test below.

## Install test without a Pi (emulated arm64 container)
```bash
bash robot/scripts/container_test.sh      # Git Bash on the laptop, Docker Desktop running
```
It starts `debian:trixie` for `linux/arm64` (QEMU emulation in Docker Desktop) with the repository mounted read-only, copies `robot/`, `shared/`, `firmware/` and `backend/app/static/` inside (some robot tests read the last two), runs `setup.sh --container-test` (apt packages, virtualenv, the lock, the models; no udev, systemd, kiosk, sudo or hardware), then the robot tests, `robotd --sim` for 20 s (`/local/status` must answer and the log must say SIMULATED), visiond on a synthetic video, and `import mediapipe, cv2`, and prints the time of each phase. **An emulated container is not a Raspberry Pi**: it proves that the packages install and import on arm64 trixie; it says nothing about the camera, the serial port, the screen or the speed of a Pi.

## Flashing the ESP32 from the Pi
1. **Build on the laptop** (Docker; the real firmware needs the confirmed pins, `firmware/include/pins_generated.h`, see `firmware/README.md`), in Git Bash from the repository root (in PowerShell `bash` would start WSL):
   ```bash
   bash firmware/tools/build_release.sh
   ```
   It runs `pio run -e esp32dev` in the firmware container and copies `bootloader.bin`, `partitions.bin`, `boot_app0.bin` (from the Arduino framework in the `scr-pio-cache` volume), `firmware.bin`, `SHA256SUMS` and `VERSION` (the version of `firmware/lib/app/scr_version.h`) into `firmware/release/` (git-ignored). Never flash a build of `esp32-compile-check`: it is not a robot firmware.
2. **Copy it to the Pi**: `scp -r firmware/release <user>@<pi address>:firmware-release`
3. **Flash on the Pi** (robot standing still, tracks off the ground):
   ```bash
   bash robot/deploy/flash_esp32.sh ~/firmware-release           # asks first; --yes for scripts
   ```
   It checks that the four files are listed in `SHA256SUMS` and match it (nothing is flashed otherwise), asks, stops `robotd` (it owns the serial port; the ESP32 stops the motors by itself), runs `esptool` (from the lock) `write-flash` at the standard offsets of an Arduino-ESP32 build for the classic ESP32 — **0x1000** bootloader, **0x8000** partition table, **0xe000** boot_app0, **0x10000** app (PlatformIO platform espressif32 6.x, `builder/frameworks/arduino.py`) — resets the board, checks the version with `python -m robotctl diag --expect-version <version>` — the version comes from `--expect-version`, else from `firmware/lib/app/scr_version.h` of the Pi's checkout (2.0.0 today), so build the release from the same commit — (skipped with a clear message if `robotctl` is not installed), and starts `robotd` again, also when something failed. `write-flash` keeps the parameters saved in NVS; the script never erases the flash (`erase-flash` would wipe them).

## Settings reference
`robotd`: `robot/robotd/config.py` (every field can come from a JSON file with `--config`, or from the environment: `BACKEND_URL`, `ROBOT_TOKEN`, `ROBOT_ID`, `BACKEND_CA_FILE` (only for a test or self-hosted CA; `wss://` is always verified), `ROBOTD_SERIAL_PORT`, `ROBOTD_UI_PORT`, `ROBOTD_UI_HOST`, `ROBOTD_SIM_PORT`, `ROBOTD_ASSETS_DIR`, `ROBOTD_CACHE_DIR`, `ROBOTD_PAGE_BACKEND`). `metres_per_tick`, `track_width_m`, the battery limits and the navigation distances are **placeholders** until the calibration of `docs/ODOMETRY.md` §5; `metres_per_tick` must be the same number as `METRES_PER_TICK` in `firmware/include/config.h`.

`visiond`: `robot/visiond/config.py` and `shared/vision_thresholds.json` (`CAMERA_SOURCE`, `ROBOTD_VISION_URL`, `VISION_LANDMARKS`, `CAMERA_HFOV_DEG`, `CAMERA_MIRROR`, `SERVO_*`). The field of view and the servo limits are placeholders until the real webcam and servo are mounted.
