"""Server mode (sprint 6 P0.7): robotd over wss:// with certificate verification, and the robot
token kept out of every log line.

A throw-away CA and a server certificate for "localhost" are made with the `openssl` command for
each test session (skipped when openssl is not installed). The FAKE backend of test_robotd_app
is served over TLS with that certificate. The ESP32 is SIMULATED (CLAUDE.md rule 5); nothing
here touches the real backend or the internet.
"""

import asyncio
import json
import logging
import shutil
import ssl
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from test_robotd_app import FakeBackend, wait_for

from robotd.app import RobotApp
from robotd.config import Config, load_config
from robotd.tls import RedactFilter, client_ssl_context, redact, register_secret
from robotd.ui_server import inject

TOKEN = "tls-test-token-7f3a9c"
GIT_OPENSSL = Path("C:/Program Files/Git/mingw64/bin/openssl.exe")


def _openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    return str(GIT_OPENSSL) if GIT_OPENSSL.is_file() else None


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """ca.pem (test CA) and server.pem/server.key for DNS:localhost only (not 127.0.0.1)."""
    exe = _openssl()
    if exe is None:
        pytest.skip("openssl not installed")
    d = tmp_path_factory.mktemp("pki")

    def run(*args: str) -> None:
        subprocess.run([exe, *args], cwd=d, check=True, capture_output=True, timeout=60)

    ec = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    run("req", "-x509", *ec, "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=SCR throw-away test CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash")  # fmt: skip
    run("req", "-new", *ec, "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid:always\n",
        encoding="ascii",
    )
    run("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", "server.pem", "-days", "2", "-extfile", "ext.cnf")  # fmt: skip
    return {"ca": d / "ca.pem", "cert": d / "server.pem", "key": d / "server.key"}


class TlsBackend(FakeBackend):
    """The fake /ws/robot backend, over TLS (https + wss) with the throw-away certificate."""

    def __init__(self, cert: Path, key: Path) -> None:
        super().__init__()
        self._ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self._ctx.load_cert_chain(cert, key)
        self.http_auth: list[str] = []  # Authorization of robotd's GETs (page, /api/robot)

    async def _page(self, request: web.Request) -> web.Response:
        self.http_auth.append(request.headers.get("Authorization", ""))
        return await super()._page(request)

    async def _screen(self, request: web.Request) -> web.Response:
        self.http_auth.append(request.headers.get("Authorization", ""))
        return await super()._screen(request)

    async def start(self) -> None:
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=self._ctx)
        await site.start()
        self.port = self._runner.addresses[0][1]


@pytest.fixture
async def tls_backend(pki: dict[str, Path]) -> AsyncIterator[TlsBackend]:
    fake = TlsBackend(pki["cert"], pki["key"])
    await fake.start()
    yield fake
    await fake.stop()


def _cfg(tmp_path: Path, url: str, ca_file: str = "") -> Config:
    return Config(
        sim=True, sim_port=0, ui_port=0, cache_dir=str(tmp_path / "cache"),
        backend_url=url, robot_token=TOKEN, backend_ca_file=ca_file,
        backend_reconnect_s=0.2, status_period_s=0.2, fault_clear_s=0.2, reconnect_s=0.1,
        log_dir=str(tmp_path / "logs"),
    )  # fmt: skip


@pytest.fixture
async def make_app() -> AsyncIterator:
    apps: list[RobotApp] = []

    async def make(cfg: Config) -> RobotApp:
        robot = RobotApp(cfg)
        await robot.start()
        apps.append(robot)
        return robot

    yield make
    for robot in apps:
        await robot.stop()


# ---------------------------------------------------------------- (a) trusted CA: connects
async def test_wss_with_trusted_ca_connects_and_sends_the_token(
    tls_backend: TlsBackend, pki: dict[str, Path], make_app, tmp_path: Path
) -> None:
    url = f"wss://localhost:{tls_backend.port}/ws/robot"
    robot = await make_app(_cfg(tmp_path, url, str(pki["ca"])))
    assert await wait_for(lambda: robot.backend.connected and len(tls_backend.of("robot.status")))
    assert tls_backend.urls[0] == f"/ws/robot?token={TOKEN}"  # the way robotd always sent it
    hello = tls_backend.of("hello")[0]
    assert hello["protocol_version"] == 2
    assert tls_backend.invalid == []
    assert robot.cfg.backend_http == f"https://localhost:{tls_backend.port}"
    # the page's read-only proxy goes to the backend over verified https as well
    async with aiohttp.ClientSession() as http:
        async with http.get(f"http://127.0.0.1:{robot.ui.port}/api/robot/screen") as resp:
            assert resp.status == 200
            assert (await resp.json())["programs"] == []
        async with http.get(f"http://127.0.0.1:{robot.ui.port}/robot") as resp:
            page = await resp.text()
    assert f'window.SCR_BACKEND = "localhost:{tls_backend.port}"' in page
    assert "window.SCR_BACKEND_SECURE = true;" in page
    # production gates /robot and /api/robot behind the token: sent as a header, not in the URL
    assert tls_backend.http_auth and set(tls_backend.http_auth) == {f"Bearer {TOKEN}"}


# ------------------------------------------- (b) untrusted certificate: refused, robot unharmed
async def test_wss_with_untrusted_certificate_is_refused_and_robot_keeps_working(
    tls_backend: TlsBackend, make_app, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    url = f"wss://localhost:{tls_backend.port}/ws/robot"
    robot = await make_app(_cfg(tmp_path, url))  # no BACKEND_CA_FILE: the system store only
    assert await wait_for(lambda: "Certificate" in robot.backend.last_error)
    assert "certificate verify failed" in robot.backend.last_error
    received = robot.brain.link.stats()["received"]
    await asyncio.sleep(1.0)  # several reconnect attempts (every 0.2 s)
    assert tls_backend.urls == []  # the handshake never completed: no token reached it
    assert robot.backend.connects == 0 and not robot.backend.connected
    assert not robot.brain.backend_ok
    backend_task = next(t for t in robot._tasks if t.get_name() == "backend-link")
    assert not backend_task.done()  # still retrying, not crashed
    # the local side (simulated ESP32 link, supervisor) is untouched: safety is local (rule 4)
    assert robot.brain.link.stats()["received"] > received
    assert robot.brain.sup.state == "IDLE"
    # the page proxy refuses the untrusted certificate too
    async with aiohttp.ClientSession() as http:
        async with http.get(f"http://127.0.0.1:{robot.ui.port}/api/robot/screen") as resp:
            assert resp.status == 503
    assert TOKEN not in caplog.text


# ------------------------------------------------------------- (c) hostname mismatch: refused
async def test_wss_hostname_mismatch_is_refused(
    tls_backend: TlsBackend, pki: dict[str, Path], make_app, tmp_path: Path
) -> None:
    # trusted CA, but the certificate names "localhost", not 127.0.0.1
    url = f"wss://127.0.0.1:{tls_backend.port}/ws/robot"
    robot = await make_app(_cfg(tmp_path, url, str(pki["ca"])))
    assert await wait_for(lambda: "Certificate" in robot.backend.last_error)
    assert "mismatch" in robot.backend.last_error.lower()
    await asyncio.sleep(0.6)
    assert tls_backend.urls == [] and robot.backend.connects == 0


async def test_unreadable_ca_file_keeps_retrying_without_crashing(make_app, tmp_path: Path) -> None:
    url = "wss://localhost:9/ws/robot"
    robot = await make_app(_cfg(tmp_path, url, str(tmp_path / "missing-ca.pem")))
    assert await wait_for(lambda: robot.backend.last_error != "")
    backend_task = next(t for t in robot._tasks if t.get_name() == "backend-link")
    assert not backend_task.done() and robot.backend.connects == 0
    assert await wait_for(lambda: robot.brain.sup.state == "IDLE")


# ------------------------------------------------------------- (d) the token never in the logs
async def test_token_redacted_when_the_handshake_fails(
    make_app, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A proxy answering 502/503 during a backend restart makes aiohttp raise an error whose text
    holds the full URL, query included. Before sprint 6 that text went to the log as is."""
    caplog.set_level(logging.DEBUG)
    fake = FakeBackend()
    await fake.start()
    fake.down = True  # 503 on /ws/robot
    try:
        robot = await make_app(_cfg(tmp_path, f"ws://127.0.0.1:{fake.port}/ws/robot"))
        assert await wait_for(lambda: "503" in robot.backend.last_error)
        assert TOKEN not in robot.backend.last_error
        assert "token=***" in robot.backend.last_error  # the URL is still there, redacted
        warnings = [r.getMessage() for r in caplog.records if r.name == "robotd.backend"]
        assert any("503" in m for m in warnings)
        # every robotd-side line (the fake SERVER's own access log is not robotd's and is skipped)
        robot_side = [r.getMessage() for r in caplog.records if r.name != "aiohttp.access"]
        assert robot_side and not [m for m in robot_side if TOKEN in m]
    finally:
        await fake.stop()


def test_redact_and_log_filter() -> None:
    register_secret(TOKEN)
    assert redact(f"url='wss://x.sslip.io/ws/robot?token={TOKEN}'") == (
        "url='wss://x.sslip.io/ws/robot?token=***'"
    )
    query = f"wss://h/ws/robot?a=1&token={TOKEN}&b=2"
    assert redact(query) == "wss://h/ws/robot?a=1&token=***&b=2"
    assert redact(f"the token is {TOKEN}") == "the token is ***"
    url = f"wss://h/ws/robot?token={TOKEN}"
    record = logging.LogRecord(
        "aiohttp.client", logging.DEBUG, __file__, 1, "connecting to %s", (url,), None
    )
    assert RedactFilter().filter(record) is True
    assert record.getMessage() == "connecting to wss://h/ws/robot?token=***"


def test_ssl_context_always_verifies(pki: dict[str, Path]) -> None:
    for ca in ("", str(pki["ca"])):
        ctx = client_ssl_context(Config(backend_ca_file=ca))
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    urls = "wss://a.example/ws/robot,ws://192.168.1.20:8000/ws/robot"  # server first, laptop next
    cfg = load_config(env={"BACKEND_CA_FILE": str(pki["ca"]), "BACKEND_URL": urls})
    assert cfg.backend_ca_file == str(pki["ca"])
    assert cfg.backend_urls[0] == "wss://a.example/ws/robot"
    assert cfg.backend_http == "https://a.example"
    # no setting can switch verification off
    assert not [f for f in vars(Config()) if "verify" in f or "insecure" in f]


def test_inject_marks_a_secure_backend() -> None:
    html = inject("<body><script></script></body>", "robot.example.org", "", secure=True)
    assert "window.SCR_BACKEND_SECURE = true;" in html
    assert "SCR_BACKEND_SECURE = false" in inject("<body></body>", "h:8000", "")


GUARD_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
function run(secure) {
  const opened = [];
  const ctx = {console: {warn() {}, log() {}}, Promise, Error};
  ctx.WebSocket = function (url) { opened.push(url); this.url = url; };
  ctx.WebSocket.OPEN = 1;
  ctx.window = ctx;
  vm.createContext(ctx);
  const src = fs.readFileSync(process.argv[2], "utf8");
  vm.runInContext(src, ctx);
  vm.runInContext(src, ctx);                       // twice: wrapped only once
  ctx.SCR_BACKEND = "robot.example.org";           // set after the guard, like inject()
  ctx.SCR_BACKEND_SECURE = secure;
  const a = new ctx.WebSocket("ws://robot.example.org/ws/robot?token=t");
  new ctx.WebSocket("ws://localhost:8765/ws/ui");
  new ctx.WebSocket("ws://robot.example.org.evil/ws/robot");
  return {opened, instance: a instanceof ctx.WebSocket, open: ctx.WebSocket.OPEN};
}
process.stdout.write(JSON.stringify({secure: run(true), plain: run(false)}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_guard_upgrades_only_the_backend_socket_to_wss(tmp_path: Path) -> None:
    import robotd

    guard = Path(robotd.__file__).parent / "ui" / "robot-guard.js"
    script = tmp_path / "harness.js"
    script.write_text(GUARD_HARNESS, encoding="utf-8")
    run = subprocess.run(["node", str(script), str(guard)], capture_output=True, text=True,
                         timeout=30, check=True)  # fmt: skip
    out = json.loads(run.stdout)
    assert out["secure"]["opened"] == [
        "wss://robot.example.org/ws/robot?token=t",
        "ws://localhost:8765/ws/ui",
        "ws://robot.example.org.evil/ws/robot",
    ]
    assert out["plain"]["opened"][0] == "ws://robot.example.org/ws/robot?token=t"
    assert out["secure"]["instance"] is True and out["secure"]["open"] == 1
