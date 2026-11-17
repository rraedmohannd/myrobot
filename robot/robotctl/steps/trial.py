"""return-trial: a physical return-to-start trial, stored for that run in the backend.

robotd does the roaming and the return; robotctl asks for them through the backend's existing
admin API (`/api/admin/robot/command`, the same requests as the admin's buttons) and stores the
tape measurement with `/api/admin/return-trials/{id}` (pending trial created by the return) or
`/api/admin/runs/{run_id}/return-trials`. Simulated runs are refused here, as the backend does.
The admin token is typed by the operator (`--admin-token` or `SCR_ADMIN_TOKEN`), never stored.
"""

import argparse
import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from robotctl.calstore import CalStore
from robotctl.operator import Operator, Refused
from robotctl.session import StepError

FLOOR_ROAM = "floor clear for roaming"


class Backend:
    def __init__(self, base: str, token: str, timeout: float = 15.0) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"X-Admin-Token": self.token, "Content-Type": "application/json"},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                text = resp.read().decode() or "{}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise StepError(f"backend {method} {path}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise StepError(f"backend {self.base} not reachable: {exc}") from exc
        return json.loads(text)  # type: ignore[no-any-return]

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None
                   ) -> dict[str, Any]:  # fmt: skip
        return await asyncio.to_thread(self._call, method, path, body)


def _state(snapshot: dict[str, Any]) -> str:
    robot = snapshot.get("robot") or {}
    return str((robot.get("status") or {}).get("state") or "?")


async def run_return_trial(
    args: argparse.Namespace, op: Operator, store: CalStore, out: Callable[[str], None] = print
) -> int:
    token = args.admin_token or os.environ.get("SCR_ADMIN_TOKEN", "")
    if not token:
        raise Refused("needs the admin token: --admin-token or the SCR_ADMIN_TOKEN variable")
    roam_s, poll = float(args.roam_secs), float(args.poll_s)
    if not 5 <= roam_s <= 900:
        raise Refused("--roam-secs 5..900")
    be = Backend(args.backend, token)
    snap = await be.call("GET", "/api/admin/robot")
    robot = snap.get("robot")
    if not snap.get("online") or not robot:
        raise Refused("no robot is connected to the backend (is robotd running and online?)")
    if robot.get("is_simulated"):
        raise Refused(
            "the connected robot is SIMULATED: return trials are physical tape measurements of "
            "real runs (CLAUDE.md rule 5); nothing was started"
        )
    if _state(snap) != "IDLE":
        raise Refused(f"robotd must be IDLE to start a trial (it is {_state(snap)})")
    out(
        "return-trial: put the robot on a taped START mark (mark the front edge and one side).\n"
        f"  It roams for {roam_s:g} s, then returns by itself. Stay close, hand near the stop."
    )
    await op.confirm(FLOOR_ROAM, "The robot will drive by itself.")
    started = await be.call("POST", "/api/admin/robot/command", {"cmd": "start_roaming"})
    if started.get("accepted") is not True:
        raise StepError(f"start_roaming not accepted: {started.get('reason') or started}")
    run_id = started.get("run_id")
    end = time.monotonic() + roam_s
    while time.monotonic() < end:
        await asyncio.sleep(min(poll, max(0.0, end - time.monotonic())))
        snap = await be.call("GET", "/api/admin/robot")
        run_id = run_id or (snap.get("robot") or {}).get("run_id")
        out(f"  roaming... state {_state(snap)}")
        if _state(snap) in ("FAULT", "IDLE"):
            raise StepError(f"the robot left ROAMING by itself ({_state(snap)}); trial aborted")
    ret = await be.call("POST", "/api/admin/robot/command", {"cmd": "return_to_start"})
    if ret.get("accepted") is not True:
        raise StepError(f"return_to_start not accepted: {ret.get('reason') or ret}")
    trial_id = ret.get("return_trial_id")
    run_id = ret.get("run_id") or run_id
    end = time.monotonic() + float(args.return_timeout)
    final = "?"
    while time.monotonic() < end:
        await asyncio.sleep(poll)
        snap = await be.call("GET", "/api/admin/robot")
        final = _state(snap)
        run_id = run_id or (snap.get("robot") or {}).get("run_id")
        if final in ("IDLE", "FAULT", "PAUSED"):
            break
        out(f"  returning... state {final}")
    out(f"  the robot reports {final}")
    if run_id is None:
        raise StepError("the backend did not report a run id for this trial")
    err = await op.number("Distance from the START mark to the same point now (tape)",
                          args.measured, 0.0, 20.0, "m")  # fmt: skip
    heading = args.heading_err_deg
    tolerance = float(args.tolerance)
    success = (err <= tolerance and final == "IDLE") if args.success is None else (
        args.success == "yes")  # fmt: skip
    body = {
        "measured_err_m": err, "measured_heading_err_deg": heading, "success": success,
        "notes": f"robotctl return-trial: roamed {roam_s:g} s, robot ended {final}",
    }  # fmt: skip
    if trial_id is not None:
        saved = await be.call("PUT", f"/api/admin/return-trials/{trial_id}", body)
    else:
        saved = await be.call("POST", f"/api/admin/runs/{run_id}/return-trials", body)
    out(f"Saved return trial {saved.get('id')} for run {run_id}: {err:.3f} m, "
        f"{'success' if success else 'not a success'} (tolerance {tolerance:g} m)")  # fmt: skip
    store.log(False, "return-trial", {"run_id": run_id, "trial": saved, **body,
                                      "tolerance_m": tolerance, "end_state": final})  # fmt: skip
    return 0
