"""Local UI server: the robot page on http://localhost (a secure context for the microphone).

- `GET /` and `/robot`: the backend's robot page, with two additions injected by robotd:
  `window.SCR_BACKEND` / `window.SCR_ROBOT_TOKEN` (so the page opens its conversation WebSocket
  on the backend, on another machine) and the local script `/local/robot-local.js`.
- `GET /static/...`: the page's assets. From a local folder (`assets_dir`), else fetched from the
  backend and kept in a cache folder that is used when the backend is unreachable.
- `GET /api/robot/...`, `/media/...`: passed on to the backend (read-only).
- `WS /ws/ui`: robotd <-> page (local.engage / local.vision / local.state; local.vad /
  local.conversation).
- `WS /ws/vision`: visiond connects here (vision.state in; vad / servo out). No image data.
- `WS /ws/ctl`: robotctl (robotd.ctl, localhost only).

The server listens on 127.0.0.1 by default: the injected token never leaves the robot.
"""

import asyncio
import contextlib
import json
import logging
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from robotd.config import Config
from robotd.core import Brain

if TYPE_CHECKING:
    from robotd.ctl import CtlHub

log = logging.getLogger("robotd.ui")

LOCAL_DIR = Path(__file__).parent / "ui"
_TYPES = {".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
          ".task": "application/octet-stream", ".html": "text/html"}  # fmt: skip
_FALLBACK_PAGE = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<title>الروبوت</title><body style="font:28px system-ui;text-align:center;padding-top:20vh">
<p>عذراً، الخدمة مش متاحة هلأ.</p><p style="font-size:18px">Service unavailable — the robot page
could not be loaded from the backend and no cached copy exists. Retrying…</p>
<script>setTimeout(() => location.reload(), 5000)</script>
<script src="/local/robot-local.js"></script></body></html>"""


def inject(html: str, backend_host: str, token: str) -> str:
    """Add the backend address (+ token) before the page's scripts, the local script at the end."""
    head = (
        # robot mode: the page never opens the camera (visiond owns it); runs before page scripts
        '<script src="/local/robot-guard.js"></script>\n'
        f"<script>window.SCR_BACKEND = {json.dumps(backend_host)};"
        f"window.SCR_ROBOT_TOKEN = {json.dumps(token or None)};</script>\n"
    )
    tail = '<script src="/local/robot-local.js"></script>\n'
    at = html.find("<script")
    html = html[:at] + head + html[at:] if at >= 0 else head + html
    end = html.rfind("</body>")
    return html[:end] + tail + html[end:] if end >= 0 else html + tail


def _safe(rel: str) -> str | None:
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts or any(p in (".", "..") or ":" in p for p in parts):
        return None
    return "/".join(parts)


class UiServer:
    def __init__(
        self, cfg: Config, brain: Brain, clock: Callable[[], float],
        ctl: "CtlHub | None" = None, app: Any = None,
    ) -> None:  # fmt: skip
        self.cfg, self.brain, self._clock = cfg, brain, clock
        self.ctl, self.robot_app = ctl, app
        self.ui_clients: set[web.WebSocketResponse] = set()
        self.vision_clients: set[web.WebSocketResponse] = set()
        self.port = cfg.ui_port
        self._runner: web.AppRunner | None = None
        self._http: aiohttp.ClientSession | None = None
        app = web.Application()
        app.add_routes([
            web.get("/", self._page), web.get("/robot", self._page),
            web.get("/local/status", self._status),
            web.get("/local/{name}", self._local),
            web.get("/static/{tail:.*}", self._static),
            web.get("/api/robot/{tail:.*}", self._proxy), web.get("/media/{tail:.*}", self._proxy),
            web.get("/ws/ui", self._ws_ui), web.get("/ws/vision", self._ws_vision),
        ])  # fmt: skip
        if ctl is not None:
            app.router.add_get("/ws/ctl", ctl.handle)
        self.app = app

    # ---------------- lifecycle ----------------
    async def start(self) -> int:
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20, sock_connect=3))
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.cfg.ui_host, self.cfg.ui_port)
        await site.start()
        self.port = self._runner.addresses[0][1]
        log.info(
            "robot page: http://localhost:%d/  (backend for the page: %s)",
            self.port,
            self.cfg.page_backend_host,
        )
        return self.port

    async def stop(self) -> None:
        for ws in [*self.ui_clients, *self.vision_clients]:
            with contextlib.suppress(Exception):
                await ws.close()
        if self._runner:
            await self._runner.cleanup()
        if self._http:
            await self._http.close()

    async def sync_assets(self) -> list[str]:
        """Copy the page and its scripts from the backend into the cache (startup, best effort)."""
        if self.cfg.assets_dir:
            return []
        done: list[str] = []
        html = await self._fetch("/robot", "robot.html")
        if html is None:
            log.warning("backend not reachable for the robot page; using the cached copy if any")
            return done
        done.append("robot.html")
        for src in re.findall(r'<script src="/static/([^"]+)"', html.decode("utf-8", "replace")):
            if await self._fetch(f"/static/{src}", f"static/{src}") is not None:
                done.append(src)
        return done

    # ---------------- outgoing local messages ----------------
    async def flush(self) -> None:
        """Send what the brain queued for the page and for visiond."""
        ui, self.brain.ui_out = self.brain.ui_out, []
        vision, self.brain.vision_out = self.brain.vision_out, []
        await self._broadcast(self.ui_clients, ui)
        await self._broadcast(self.vision_clients, vision)

    @staticmethod
    async def _broadcast(clients: set[web.WebSocketResponse], msgs: list[dict[str, Any]]) -> None:
        for msg in msgs:
            text = json.dumps(msg)
            for ws in list(clients):
                try:
                    await ws.send_str(text)
                except (ConnectionError, RuntimeError):
                    clients.discard(ws)

    # ---------------- assets ----------------
    async def _fetch(self, url_path: str, cache_rel: str) -> bytes | None:
        """GET from the backend; a good answer is written to the cache."""
        assert self._http is not None
        try:
            async with self._http.get(self.cfg.backend_http + url_path) as resp:
                if resp.status != 200:
                    return None
                body = await resp.read()
        except (aiohttp.ClientError, OSError, TimeoutError):
            return None
        target = self.cfg.cache_path / cache_rel
        try:
            await asyncio.to_thread(self._write, target, body)
        except OSError as exc:
            log.warning("cannot write the asset cache %s: %s", target, exc)
        return body

    @staticmethod
    def _write(target: Path, body: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            return path.read_bytes() if path.is_file() else None
        except OSError:
            return None

    async def _asset(self, rel: str, url_path: str) -> bytes | None:
        """Local folder first; else the backend (refreshing the cache); else the cache."""
        if self.cfg.assets_dir:
            return await asyncio.to_thread(self._read, Path(self.cfg.assets_dir) / rel)
        cache_rel = rel if rel == "robot.html" else f"static/{rel}"
        cached = self.cfg.cache_path / cache_rel
        if rel.startswith("vendor/"):  # large third-party files that do not change: cache first
            body = await asyncio.to_thread(self._read, cached)
            if body is not None:
                return body
        body = await self._fetch(url_path, cache_rel)
        if body is None:
            body = await asyncio.to_thread(self._read, cached)
        return body

    async def _page(self, request: web.Request) -> web.Response:
        body = await self._asset("robot.html", "/robot")
        html = body.decode("utf-8", "replace") if body is not None else _FALLBACK_PAGE
        html = inject(html, self.cfg.page_backend_host, self.cfg.robot_token)
        return web.Response(
            text=html, content_type="text/html", headers={"Cache-Control": "no-store"}
        )

    async def _static(self, request: web.Request) -> web.Response:
        rel = _safe(request.match_info["tail"])
        if rel is None:
            raise web.HTTPNotFound()
        body = await self._asset(rel, f"/static/{rel}")
        if body is None:
            raise web.HTTPNotFound()
        ctype = _TYPES.get(Path(rel).suffix.lower()) or mimetypes.guess_type(rel)[0]
        return web.Response(body=body, content_type=ctype or "application/octet-stream",
                            headers={"Cache-Control": "no-cache"})  # fmt: skip

    async def _local(self, request: web.Request) -> web.Response:
        name = _safe(request.match_info["name"])
        body = self._read(LOCAL_DIR / name) if name else None
        if body is None:
            raise web.HTTPNotFound()
        return web.Response(
            body=body, content_type="text/javascript", headers={"Cache-Control": "no-store"}
        )

    async def _status(self, request: web.Request) -> web.Response:
        out = self.brain.status(self._clock())
        out["backend_ok"] = self.brain.backend_ok
        out["link"] = self.brain.link.stats()
        out["label"] = "SIMULATED - not a physical robot" if self.brain.is_simulated else ""
        return web.json_response(out)

    async def _proxy(self, request: web.Request) -> web.Response:
        """Read-only pass-through for the page's relative requests (idle screen, media)."""
        assert self._http is not None
        try:
            async with self._http.get(self.cfg.backend_http + request.rel_url.path_qs) as resp:
                body = await resp.read()
                return web.Response(body=body, status=resp.status, content_type=resp.content_type,
                                    headers={"Cache-Control": "no-store"})  # fmt: skip
        except (aiohttp.ClientError, OSError, TimeoutError):
            return web.json_response({"detail": "backend unavailable"}, status=503)

    # ---------------- local WebSockets ----------------
    async def _ws_ui(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.ui_clients.add(ws)
        brain = self.brain
        try:
            await ws.send_str(json.dumps({
                "type": "local.state", "state": brain.sup.state, "backend_ok": brain.backend_ok,
                "is_simulated": brain.is_simulated,
            }))  # fmt: skip
            async for raw in ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    msg = self._json(raw.data)
                    if msg is not None:
                        brain.on_ui(msg)
        except (ConnectionError, RuntimeError):
            pass
        finally:
            self.ui_clients.discard(ws)
            if not self.ui_clients:
                brain.ui_disconnected()
        return ws

    async def _ws_vision(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.vision_clients.add(ws)
        self.brain.vision_connected = True
        log.info("visiond connected")
        try:
            async for raw in ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    msg = self._json(raw.data)
                    if msg is not None:
                        self.brain.on_vision(msg, self._clock())
        except (ConnectionError, RuntimeError):
            pass
        finally:
            self.vision_clients.discard(ws)
            if not self.vision_clients:
                self.brain.vision_connected = False
                self.brain.vision = None
                log.info("visiond disconnected")
        return ws

    @staticmethod
    def _json(text: str) -> dict[str, Any] | None:
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
