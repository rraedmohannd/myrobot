"""Camera ownership in robot mode (sprint 5 default 13): the page served by robotd never opens
the webcam (visiond owns it). Node runs robotd's guard with a stub getUserMedia."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import robotd

GUARD = Path(robotd.__file__).parent / "ui" / "robot-guard.js"
ROBOT_HTML = Path(__file__).resolve().parents[3] / "backend" / "app" / "static" / "robot.html"

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const calls = [];
const ctx = {console: {warn() {}, log() {}}, Promise, Error};
const gum = c => { calls.push(c); return Promise.resolve("stream"); };
ctx.navigator = {mediaDevices: {getUserMedia: gum}};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);   // twice: wrapped only once
(async () => {
  const out = {robotMode: ctx.SCR_ROBOT_MODE};
  const md = ctx.navigator.mediaDevices;
  try { await md.getUserMedia({video: {width: 320}, audio: false}); out.video = "opened"; }
  catch (e) { out.video = e.name; }
  try { await md.getUserMedia({video: true, audio: true}); out.both = "opened"; }
  catch (e) { out.both = e.name; }
  out.audio = await md.getUserMedia({audio: {channelCount: 1}});
  out.calls = calls;
  console.log = (...a) => process.stdout.write(a.join(" ") + "\n");
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_guard_never_lets_the_page_open_the_camera(tmp_path: Path) -> None:
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run(["node", str(script), str(GUARD)], capture_output=True, text=True,
                         timeout=30, check=True)  # fmt: skip
    out = json.loads(run.stdout)
    assert out["robotMode"] is True
    assert out["video"] == "NotAllowedError" and out["both"] == "NotAllowedError"
    assert out["audio"] == "stream"  # the microphone still works
    assert out["calls"] == [{"audio": {"channelCount": 1}}]  # never called with video


def test_robot_page_respects_robot_mode() -> None:
    html = ROBOT_HTML.read_text(encoding="utf-8")
    assert "const robotMode = !!window.SCR_ROBOT_MODE;" in html
    assert "let cameraWanted = !robotMode && (" in html
    assert 'e.code === "KeyC" && !robotMode' in html
    assert 'params.get("face_start") === "1" && !robotMode) camera.start()' in html
