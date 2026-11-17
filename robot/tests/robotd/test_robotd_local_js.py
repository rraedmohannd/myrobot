"""The script robotd injects into the robot page (robotd/ui/robot-local.js), run in Node with a
stub page: no browser, so this checks the logic only, not rendering or audio."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import robotd

SCRIPT = Path(robotd.__file__).parent / "ui" / "robot-local.js"

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const sockets = [], handlers = {}, sent = [];
let now = 1000, started = 0;
const badge = {style: {}, hidden: true, textContent: ""};
const ctx = {
  console, setTimeout: () => 0,
  performance: {now: () => now},
  location: {protocol: "http:", host: "localhost:8765"},
  document: {createElement: () => badge, body: {appendChild() {}, dataset: {}}},
  WebSocket: function (url) {
    this.url = url; this.readyState = 1; sockets.push(this);
    this.send = t => sent.push(JSON.parse(t));
  },
  client: {on(name, fn) { (handlers[name] = handlers[name] || []).push(fn); }},
  start: () => { started++; },
};
ctx.window = ctx;
ctx.speakerCamera = {audioEndFields: () => ({face_track_id: 99})};   // the page's own camera
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const ws = sockets[0], emit = (name, arg) => (handlers[name] || []).forEach(fn => fn(arg));
const push = m => ws.onmessage({data: JSON.stringify(m)});
const out = {url: ws.url};
ws.onopen();
out.ownCamera = ctx.speakerCamera.audioEndFields();            // no visiond yet
push({type: "local.state", state: "ROAMING", backend_ok: true, is_simulated: true});
out.badgeSim = badge.textContent; out.simFlag = ctx.document.body.dataset.simulated;
push({type: "local.state", state: "PAUSED", backend_ok: false, is_simulated: false});
out.badgeDown = badge.textContent;
push({type: "local.engage"});
out.started = started;
emit("message", {type: "session.started"});
emit("speechstart");
push({type: "local.vision", face_track_id: 7, overlap: false, engaged: true, faces: 2});
push({type: "local.vision", face_track_id: null, overlap: true, engaged: true, faces: 2});
emit("speechend");
out.audioEnd = ctx.speakerCamera.audioEndFields();
emit("speechstart"); emit("speechend");
out.audioEndSilentFace = ctx.speakerCamera.audioEndFields();
now += 5000;                                                    // visiond went quiet
out.fallback = ctx.speakerCamera.audioEndFields();
emit("message", {type: "session.ended"});
emit("message", {type: "session.started"});
emit("conn", "lost");
ws.onclose();
out.badgeOffline = badge.textContent;
out.sent = sent;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_robot_local_js_logic(tmp_path: Path) -> None:
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run(
        ["node", str(harness), str(SCRIPT)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["url"] == "ws://localhost:8765/ws/ui"
    assert out["ownCamera"] == {"face_track_id": 99}
    # Arabic first, then English; the SIMULATED label is visible
    assert out["badgeSim"].startswith("محاكاة · SIMULATED") and out["simFlag"] == "1"
    assert "الخدمة مش متاحة · service unavailable" in out["badgeDown"]
    assert "SIMULATED" not in out["badgeDown"]
    assert out["started"] == 1  # local.engage = the Start button
    assert out["audioEnd"] == {"overlap": True, "face_track_id": 7}  # visiond's speaker
    assert out["audioEndSilentFace"] == {"overlap": False}
    assert out["fallback"] == {"face_track_id": 99}  # back to the page's camera
    assert "robotd offline" in out["badgeOffline"]
    assert out["sent"] == [
        {"type": "local.conversation", "active": False},
        {"type": "local.conversation", "active": True},
        {"type": "local.vad", "speaking": True},
        {"type": "local.vad", "speaking": False},
        {"type": "local.vad", "speaking": True},
        {"type": "local.vad", "speaking": False},
        {"type": "local.conversation", "active": False},
        {"type": "local.conversation", "active": True},
        {"type": "local.conversation", "active": False},
    ]
