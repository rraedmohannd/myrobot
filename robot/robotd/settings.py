"""Settings page on the robot screen (sprint 5 S6.1), served by robotd on http://localhost.

Opened by a hidden gesture on the robot page (long press of 3 s on the top-right corner,
robot-local.js) and a PIN from robot.env (`ROBOTD_SETTINGS_PIN`). No PIN set -> disabled.

Endpoints (localhost only; the PIN is checked here, on the server; 5 wrong PINs in 5 minutes ->
locked for the rest of the window):
- `GET  /local/settings`                the page (Arabic first, English second)
- `POST /local/settings/login`          {pin} -> {token} (valid 10 minutes)
- `GET  /local/settings/info`           backend addresses, volume, Wi-Fi + IP, versions, battery,
                                        calibration (header `X-Settings-Token`)
- `POST /local/settings/backend`        {urls: [...]} -> written to the LOCAL OVERRIDE FILE
                                        (never to .env); used at once and at every start
- `POST /local/settings/volume`         {percent} -> `wpctl` (Linux/PipeWire); 501 elsewhere
The speaker test tone and the microphone level meter run in the browser only: nothing is
recorded, stored or sent.
"""

import asyncio
import json
import logging
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

from robotd import __version__
from robotd.config import Config
from robotd.core import Brain

log = logging.getLogger("robotd.settings")

UI_DIR = Path(__file__).parent / "ui"
PI_OVERRIDE = Path("/etc/smart-campus-robot/robotd.override.json")
DEV_OVERRIDE = Path(__file__).resolve().parents[1] / "robotd.override.json"  # git-ignored
TOKEN_S = 600.0
FAIL_WINDOW_S = 300.0
MAX_FAILS = 5
LOCAL = ("127.0.0.1", "::1")


def override_path(cfg: Config) -> Path:
    if cfg.settings_file:
        return Path(cfg.settings_file)
    if sys.platform.startswith("linux") and PI_OVERRIDE.parent.is_dir():
        return PI_OVERRIDE
    return DEV_OVERRIDE


def apply_override(cfg: Config) -> bool:
    """At start: backend addresses saved from the settings page win over robot.env."""
    try:
        data = json.loads(override_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    url = data.get("backend_url") if isinstance(data, dict) else None
    if isinstance(url, str) and url.strip():
        cfg.backend_url, cfg.backend_index = url.strip(), 0
        log.info("backend address(es) from the local override file %s", override_path(cfg))
        return True
    return False


def valid_backend_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("ws", "wss") and bool(p.hostname) and p.path.endswith("/ws/robot") and (
        "?" not in url and "," not in url and " " not in url
    )  # fmt: skip


def _run(args: list[str]) -> str | None:
    exe = shutil.which(args[0])
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def read_volume() -> int | None:
    text = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])  # "Volume: 0.40 [MUTED]"
    try:
        return round(float(text.split()[1]) * 100) if text else None
    except (IndexError, ValueError):
        return None


def set_volume(percent: int) -> bool:
    return _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent / 100:.2f}"]) is not None


def wifi_status() -> dict[str, Any]:
    out: dict[str, Any] = {}
    text = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"])
    if text:
        for line in text.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[0] == "yes":
                out.update(ssid=parts[1], signal=int(parts[2]) if parts[2].isdigit() else None)
    try:  # the address used to reach the network (UDP connect sends no packet)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            out["ip"] = s.getsockname()[0]
    except OSError:
        pass
    return out


class SettingsApi:
    def __init__(
        self, cfg: Config, brain: Brain, clock: Callable[[], float] = time.monotonic,
        volume_get: Callable[[], int | None] = read_volume,
        volume_set: Callable[[int], bool] = set_volume,
        wifi: Callable[[], dict[str, Any]] = wifi_status,
    ) -> None:  # fmt: skip
        self.cfg, self.brain, self._clock = cfg, brain, clock
        self._volume_get, self._volume_set, self._wifi = volume_get, volume_set, wifi
        self._tokens: dict[str, float] = {}
        self._fails: list[float] = []

    def routes(self) -> list[web.RouteDef]:
        return [
            web.get("/local/settings", self._page),
            web.post("/local/settings/login", self._login),
            web.get("/local/settings/info", self._info),
            web.post("/local/settings/backend", self._backend),
            web.post("/local/settings/volume", self._volume),
        ]

    # ---------------- checks ----------------
    @staticmethod
    def _local(request: web.Request) -> None:
        if (request.remote or "") not in LOCAL:
            raise web.HTTPForbidden(text="settings are local only")

    def _enabled(self) -> None:
        if not self.cfg.settings_pin:
            raise web.HTTPForbidden(text="settings disabled (no ROBOTD_SETTINGS_PIN)")

    def _authorised(self, request: web.Request) -> None:
        self._local(request)
        self._enabled()
        now = self._clock()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = request.headers.get("X-Settings-Token", "")
        if token not in self._tokens:
            raise web.HTTPUnauthorized(text="PIN needed")

    # ---------------- handlers ----------------
    async def _page(self, request: web.Request) -> web.Response:
        self._local(request)
        body = (UI_DIR / "settings.html").read_text(encoding="utf-8")
        return web.Response(text=body, content_type="text/html",
                            headers={"Cache-Control": "no-store"})  # fmt: skip

    async def _login(self, request: web.Request) -> web.Response:
        self._local(request)
        self._enabled()
        now = self._clock()
        self._fails = [t for t in self._fails if now - t < FAIL_WINDOW_S]
        if len(self._fails) >= MAX_FAILS:
            raise web.HTTPTooManyRequests(text="too many wrong PINs; wait 5 minutes")
        try:
            pin = str((await request.json()).get("pin", ""))
        except (ValueError, AttributeError):
            pin = ""
        if not secrets.compare_digest(pin.encode(), self.cfg.settings_pin.encode()):
            self._fails.append(now)
            log.warning("settings: wrong PIN (%d in the last 5 minutes)", len(self._fails))
            await asyncio.sleep(0.3)
            raise web.HTTPUnauthorized(text="wrong PIN")
        token = secrets.token_hex(16)
        self._tokens[token] = now + TOKEN_S
        return web.json_response({"token": token, "valid_s": TOKEN_S})

    async def _info(self, request: web.Request) -> web.Response:
        self._authorised(request)
        b = self.brain
        volume, wifi = await asyncio.gather(
            asyncio.to_thread(self._volume_get), asyncio.to_thread(self._wifi)
        )
        firmware = b.firmware_status() or {}
        cal = b.calibration_status()
        return web.json_response({
            "backend_urls": self.cfg.backend_urls, "backend_in_use": self.cfg.current_backend_url,
            "backend_ok": b.backend_ok, "override_file": str(override_path(self.cfg)),
            "volume": volume, "wifi": wifi,
            "versions": {"robotd": __version__, "firmware": firmware.get("version"),
                         "firmware_git": firmware.get("git"),
                         "calibration_updated": cal.get("updated")},
            "battery": b.battery_status(), "calibration": cal,
            "calibration_problem": b.cal_problem, "is_simulated": b.is_simulated,
            "state": b.sup.state, "health": b.health,
        })  # fmt: skip

    async def _backend(self, request: web.Request) -> web.Response:
        self._authorised(request)
        try:
            urls = (await request.json()).get("urls")
        except (ValueError, AttributeError):
            urls = None
        if not isinstance(urls, list) or not urls or len(urls) > 5:
            raise web.HTTPBadRequest(text="urls: a list of 1 to 5 addresses")
        urls = [str(u).strip() for u in urls]
        bad = [u for u in urls if not valid_backend_url(u)]
        if bad:
            raise web.HTTPBadRequest(text=f"not a ws://host:port/ws/robot address: {bad[0]}")
        path = override_path(self.cfg)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"backend_url": ",".join(urls)}, indent=2) + "\n",
                           encoding="utf-8")  # fmt: skip
            tmp.replace(path)
        except OSError as exc:
            raise web.HTTPInternalServerError(text=f"cannot write {path}: {exc}") from exc
        self.cfg.backend_url, self.cfg.backend_index = ",".join(urls), 0
        log.info("settings: backend address(es) changed (%d), saved to %s", len(urls), path)
        return web.json_response({"ok": True, "backend_urls": urls, "file": str(path),
                                  "note": "used from the next reconnect"})  # fmt: skip

    async def _volume(self, request: web.Request) -> web.Response:
        self._authorised(request)
        try:
            percent = int((await request.json()).get("percent"))
        except (ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text="percent: 0..100") from None
        if not 0 <= percent <= 100:
            raise web.HTTPBadRequest(text="percent: 0..100")
        if not await asyncio.to_thread(self._volume_set, percent):
            raise web.HTTPNotImplemented(text="volume control not available (wpctl missing)")
        return web.json_response({"ok": True, "volume": percent})
