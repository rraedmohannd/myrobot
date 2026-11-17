"""SIMULATED end-to-end check of the robot software on the laptop (sprint 4 R6). No hardware.

*** Simulation. The ESP32 is the simulated one (`robotd --sim`), the camera is the scripted
stand-in of `scripts/fake_vision.py`, the robot's screen is a small stand-in for the robot page
(no browser, no microphone, no sound is played), and the backend must be the PREVIEW backend with
FAKE AI providers (`backend/tests/preview_app.py`). A PASS here says that the processes and
messages fit together. It says nothing about the real robot, a real camera or real answers. ***

What it does, in order (one PASS/FAIL line each):
  1. starts robotd with the simulated ESP32 in this process and waits for the backend link;
  2. asks `start_roaming` through the admin API and checks that the simulated robot moves;
  3. plays the scripted visitor (fake vision): the robot must stop and reach ENGAGED, and SERVO
     commands must reach the simulated ESP32;
  4. page stand-in: on `local.engage` it opens the conversation on the backend (`session.start`),
     tells robotd (`local.conversation`), asks one typed question (`text.input`), ends the session;
  5. checks that the robot resumes ROAMING after the conversation;
  6. asks `return_to_start` and waits for RETURNING -> `return_done` -> IDLE;
  7. reads the run back through the admin API: stored, marked SIMULATED, with samples and events.

Start a preview backend on a scratch database with a throwaway admin token first
(docs/HOW_TO_START.md), then:

    cd robot
    set E2E_ADMIN_TOKEN=<the throwaway token>          (PowerShell: $env:E2E_ADMIN_TOKEN = "...")
    .venv\\Scripts\\python scripts\\e2e_sim.py --backend http://localhost:8001

Tokens come from the environment or the command line only (E2E_ADMIN_TOKEN, ROBOT_TOKEN); the
repository's .env is never read. The script refuses a backend that does not answer
`/preview-info` with fake providers, so it cannot spend money or write to the real database by
mistake. The browser part (the real robot page in a real browser) is NOT covered here.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import aiohttp

ROBOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROBOT_DIR))
sys.path.insert(0, str(ROBOT_DIR.parent / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_vision  # noqa: E402

from robotd.app import RobotApp  # noqa: E402
from robotd.config import load_config  # noqa: E402

HEADER = "SIMULATED end-to-end (simulated ESP32, scripted vision, page stand-in, FAKE AI providers)"
QUESTION = "شو تخصصات كلية الهندسة؟"


class Failed(Exception):
    pass


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []
        self.t0 = time.monotonic()

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(
            f"[{time.monotonic() - self.t0:6.1f}s] {mark}  {name}"
            + (f" - {detail}" if detail else ""),
            flush=True,
        )
        return ok

    def need(self, name: str, ok: bool, detail: str = "") -> None:
        if not self.add(name, ok, detail):
            raise Failed(name)


async def wait_until(check: Any, timeout_s: float, step_s: float = 0.05) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if check():
            return True
        await asyncio.sleep(step_s)
    return bool(check())


class PageStandIn:
    """Stand-in for the robot page: /ws/ui to robotd, /ws/robot to the backend. No audio."""

    def __init__(self, ui_url: str, backend_ws: str, token: str) -> None:
        self.ui_url = ui_url
        self.backend_ws = backend_ws + (f"?token={token}" if token else "")
        self.engaged = asyncio.Event()
        self.local_states: list[str] = []
        self.backend_types: list[str] = []
        self.answer: dict[str, Any] | None = None
        self.ended_reason: str | None = None
        self._ui: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)
        )
        self._ui = await self._session.ws_connect(self.ui_url)
        await self._ui.send_str(json.dumps({"type": "local.conversation", "active": False}))
        self._task = asyncio.create_task(self._read_ui())

    async def _read_ui(self) -> None:
        assert self._ui is not None
        async for raw in self._ui:
            if raw.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = json.loads(raw.data)
            if msg.get("type") == "local.engage":
                self.engaged.set()
            elif (
                msg.get("type") == "local.state" and msg.get("state") not in self.local_states[-1:]
            ):
                self.local_states.append(msg["state"])

    async def _tell_robotd(self, active: bool) -> None:
        assert self._ui is not None
        await self._ui.send_str(json.dumps({"type": "local.conversation", "active": active}))

    async def converse(self, question: str, before_end: Any) -> None:
        """session.start -> greeting -> typed question -> answer -> session.end."""
        assert self._session is not None
        async with self._session.ws_connect(self.backend_ws, max_msg_size=16 << 20) as ws:

            async def until(
                kind: str, state: str | None = None, timeout_s: float = 30.0
            ) -> dict[str, Any]:
                async with asyncio.timeout(timeout_s):
                    async for raw in ws:
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            continue
                        msg = json.loads(raw.data)
                        self.backend_types.append(msg.get("type", "?"))
                        if msg.get("type") == "error":
                            raise Failed(f"backend error {msg.get('code')}: {msg.get('message')}")
                        if msg.get("type") == kind and (state is None or msg.get("state") == state):
                            return msg
                raise Failed(f"the backend closed the connection while waiting for {kind}")

            await ws.send_str(
                json.dumps({"type": "hello", "robot_id": "e2e-page-standin", "protocol_version": 1})
            )
            await until("hello.ack")
            await ws.send_str(json.dumps({"type": "session.start", "lang": "ar"}))
            await until("session.started")
            await self._tell_robotd(True)
            await until("dialogue.state", "LISTENING")
            await ws.send_str(json.dumps({"type": "text.input", "text": question}))
            self.answer = await until("answer.text")
            await until("dialogue.state", "FOLLOW_UP")
            await before_end()
            await ws.send_str(json.dumps({"type": "session.end", "reason": "user_done"}))
            self.ended_reason = (await until("session.ended")).get("reason")
            await self._tell_robotd(False)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._ui is not None:
            await self._ui.close()
        if self._session is not None:
            await self._session.close()


async def run(args: argparse.Namespace, report: Report) -> None:
    http = args.backend.rstrip("/")
    ws_base = ("wss://" if http.startswith("https://") else "ws://") + http.split("://", 1)[1]
    headers = {"X-Admin-Token": args.admin_token, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=15, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as api:

        async def get(path: str) -> tuple[int, Any]:
            async with api.get(http + path, headers=headers) as resp:
                body = await resp.json(content_type=None) if resp.status == 200 else None
                return resp.status, body

        async def command(cmd: str) -> dict[str, Any]:
            async with api.post(
                http + "/api/admin/robot/command",
                headers=headers,
                data=json.dumps({"cmd": cmd, "robot_id": args.robot_id}),
            ) as resp:
                return {"http": resp.status, **(await resp.json(content_type=None) or {})}

        # ---- 0. the backend must be the preview backend with fake providers ----
        try:
            code, info = await get("/preview-info")
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise Failed(f"backend not reachable at {http}: {exc}") from exc
        report.need(
            "0 backend is the PREVIEW backend (fake AI providers)",
            code == 200 and bool(info and info.get("fake_providers")),
            "refusing to run against a real backend" if code != 200 else "",
        )
        code, _ = await get("/api/admin/robot")
        report.need("0 admin API accepts the token", code == 200, f"HTTP {code}")

        # ---- 1. robotd with the simulated ESP32, in this process ----
        cache = tempfile.TemporaryDirectory(prefix="robotd-e2e-")
        cfg = load_config(
            env={},
            sim=True,
            backend_url=ws_base + "/ws/robot",
            robot_token=args.robot_token,
            robot_id=args.robot_id,
            ui_port=args.ui_port,
            sim_port=args.sim_port,
            cache_dir=cache.name,
        )
        app = RobotApp(cfg)
        brain = app.brain
        await app.start()
        page: PageStandIn | None = None
        try:
            ok = await wait_until(
                lambda: brain.backend_ok and brain.sup.state == "IDLE" and not brain.sup.faults, 20
            )
            report.need(
                "1 robotd --sim started, backend link up, state IDLE",
                ok,
                f"state {brain.sup.state}, backend_ok {brain.backend_ok}, faults "
                f"{brain.sup.faults}, is_simulated {brain.is_simulated}; "
                f"{app.backend.last_error}",
            )
            report.need("1 robotd reports itself as SIMULATED", brain.is_simulated is True)
            sim = app.sim_server.sim  # type: ignore[union-attr]
            page = PageStandIn(
                f"ws://127.0.0.1:{app.ui.port}/ws/ui", ws_base + "/ws/robot", args.robot_token
            )
            await page.start()

            # ---- 2. roam ----
            ack = await command("start_roaming")
            report.need(
                "2 start_roaming accepted by the robot",
                ack.get("accepted") is True,
                f"HTTP {ack['http']}, reason {ack.get('reason')}",
            )
            await asyncio.sleep(6)
            travelled = brain.odom.travelled_m
            report.need(
                "2 ROAMING and moving",
                brain.sup.state == "ROAMING" and travelled > 0.5,
                f"state {brain.sup.state}, {travelled:.2f} m in 6 s (simulated odometry)",
            )
            await wait_until(lambda: brain.run_id is not None, 5)
            run_id = brain.run_id

            # ---- 3. scripted visitor ----
            leave = asyncio.Event()
            servo_seen: set[float] = set()
            states_seen: list[str] = []

            async def watch() -> None:
                while True:
                    servo_seen.add(round(sim.servo_deg, 1))
                    if brain.sup.state not in states_seen[-1:]:
                        states_seen.append(brain.sup.state)
                    await asyncio.sleep(0.02)

            watcher = asyncio.create_task(watch())
            vision = asyncio.create_task(
                fake_vision.play(f"ws://127.0.0.1:{app.ui.port}/ws/vision", args.hold, stop=leave)
            )
            t_script = time.monotonic()
            ok = await wait_until(lambda: "ENGAGED" in states_seen, 6)
            t_engaged = time.monotonic() - t_script
            report.need(
                "3 scripted visitor -> ENGAGED",
                ok,
                f"{t_engaged:.1f} s after the script started (script: engaged after "
                f"{fake_vision.APPEAR_S + fake_vision.FACING_S:.1f} s); states {states_seen}",
            )
            report.need(
                "3 page stand-in received local.engage", await wait_until(page.engaged.is_set, 3)
            )
            stop_pose = brain.odom.pose

            # ---- 4. conversation (page stand-in; no browser, no audio) ----
            async def visitor_leaves() -> None:
                await asyncio.sleep(max(0.0, 9.0 - (time.monotonic() - t_script - t_engaged)))
                leave.set()
                await vision

            talk = asyncio.create_task(page.converse(QUESTION, visitor_leaves))
            ok = await wait_until(lambda: brain.sup.state == "CONVERSATION" or talk.done(), 15)
            if talk.done() and talk.exception():
                raise Failed(f"conversation stand-in: {talk.exception()}")
            report.need(
                "4 session started -> robotd state CONVERSATION",
                ok and brain.sup.state == "CONVERSATION",
            )
            refused = await command("return_to_start")
            report.add(
                "4 return_to_start is refused during the conversation",
                refused.get("accepted") is False,
                str(refused.get("reason")),
            )
            try:
                await asyncio.wait_for(talk, 60)
            except (Failed, TimeoutError, aiohttp.ClientError) as exc:
                raise Failed(f"conversation stand-in: {type(exc).__name__}: {exc}") from exc
            answer = page.answer or {}
            report.need(
                "4 typed question answered (FAKE provider), session ended",
                bool(answer),
                f"found={answer.get('found')} kind={answer.get('kind')}, "
                f"session.ended reason {page.ended_reason}; text starts: "
                f"{str(answer.get('text'))[:60]!r}",
            )
            pose = brain.odom.pose
            drift = math.hypot(pose.x - stop_pose.x, pose.y - stop_pose.y)
            result = vision.result()
            angles = sorted(servo_seen)
            report.need(
                "3 robot stayed stopped from ENGAGED to the end of the conversation",
                drift < 0.05,
                f"moved {drift:.3f} m",
            )
            report.need(
                "3 SERVO commands reached the simulated ESP32 and followed the visitor",
                len(angles) >= 5 and angles[-1] - angles[0] >= 10,
                f"simulated servo angle went {angles[0]:+.1f} .. {angles[-1]:+.1f} deg "
                f"({len(angles)} distinct values); robotd echoed {len(result['servo'])} "
                f"servo messages for {result['sent']} vision.state",
            )

            # ---- 5. resume ----
            ok = await wait_until(lambda: brain.sup.state == "ROAMING", 5)
            before = brain.odom.travelled_m
            await asyncio.sleep(5)
            report.need(
                "5 conversation over -> ROAMING again and moving",
                ok and brain.odom.travelled_m - before > 0.3,
                f"state {brain.sup.state}, {brain.odom.travelled_m - before:.2f} m in 5 s",
            )

            # ---- 6. return to start ----
            ack = await command("return_to_start")
            report.need(
                "6 return_to_start accepted",
                ack.get("accepted") is True,
                f"reason {ack.get('reason')}",
            )
            t_ret = time.monotonic()
            await wait_until(lambda: "RETURNING" in states_seen, 3)
            ok = await wait_until(lambda: brain.sup.state == "IDLE", cfg.return_timeout_s + 20, 0.2)
            watcher.cancel()
            done = [d for k, d in brain.events if k == "return_done"]
            failed = [d for k, d in brain.events if k == "return_failed"]
            pose = brain.odom.pose
            report.need(
                "6 RETURNING -> return_done -> IDLE",
                ok and "RETURNING" in states_seen and bool(done),
                f"{time.monotonic() - t_ret:.1f} s; odometry end ({pose.x:.2f}, {pose.y:.2f}) m; "
                f"states {states_seen}" + (f"; return_failed {failed[-1]}" if failed else ""),
            )

            # ---- 7. the run in the admin API ----
            await wait_until(lambda: not brain.outbox, 10)
            await asyncio.sleep(1.5)
            code, run = await get(f"/api/admin/runs/{run_id}")
            run = run or {}
            kinds = [e["kind"] for e in run.get("events", [])]
            report.need(
                "7 run stored and marked SIMULATED in the admin API",
                code == 200
                and run.get("is_simulated") is True
                and run.get("samples", 0) > 50
                and "return_done" in kinds
                and run.get("can_add_return_trial") is False,
                f"run {run_id}: is_simulated {run.get('is_simulated')}, "
                f"{run.get('samples')} samples, {len(kinds)} events {sorted(set(kinds))}",
            )
        finally:
            if page is not None:
                await page.stop()
            await app.stop()
            with contextlib.suppress(OSError):
                cache.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument(
        "--backend",
        default="http://localhost:8001",
        help="PREVIEW backend (fake AI providers), e.g. http://localhost:8001",
    )
    parser.add_argument(
        "--admin-token",
        default=os.environ.get("E2E_ADMIN_TOKEN", ""),
        help="throwaway admin token of the preview backend [env E2E_ADMIN_TOKEN]",
    )
    parser.add_argument(
        "--robot-token",
        default=os.environ.get("ROBOT_TOKEN", ""),
        help="ROBOT_TOKEN of the preview backend, if it has one [env ROBOT_TOKEN]",
    )
    parser.add_argument("--robot-id", default="robotd-e2e")
    parser.add_argument("--ui-port", type=int, default=0, help="0 = any free port")
    parser.add_argument("--sim-port", type=int, default=0, help="0 = any free port")
    parser.add_argument(
        "--hold",
        type=float,
        default=30.0,
        help="longest time the scripted visitor stays (it leaves earlier)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show robotd's log")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"==== {HEADER} ====")
    print("Not a physical test. Not the real page in a browser: a page stand-in, no audio played.")
    if not args.admin_token:
        print("FAIL  no admin token: set E2E_ADMIN_TOKEN (the preview backend's throwaway token)")
        return 2
    report = Report()
    try:
        asyncio.run(run(args, report))
    except Failed as exc:
        if not report.rows or report.rows[-1][1]:
            report.add("stopped", False, str(exc))
    passed = sum(1 for _, ok, _ in report.rows if ok)
    all_ok = passed == len(report.rows)
    print(
        f"==== SIMULATED result: {passed}/{len(report.rows)} checks passed - "
        f"{'PASS' if all_ok else 'FAIL'} (simulation only, nothing here is a measurement) ===="
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
