"""Run the real visiond loop on a SYNTHETIC video (moving shapes, no people, made locally).

    cd robot
    .venv\\Scripts\\python scripts\\synthetic_video_test.py

It proves that capture -> detect -> track -> output runs end to end and reports zero faces on
a picture without faces. It says NOTHING about detection quality on real faces. The fps number
is for the machine it ran on only (a laptop number is not a Raspberry Pi number).
The video is written to a temporary folder and deleted at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visiond.app import VisionApp  # noqa: E402
from visiond.config import config_from_env, load_landmark_indices, load_thresholds  # noqa: E402
from visiond.detectors import make_analyzer  # noqa: E402


def make_video(path: Path, seconds: float = 6.0, fps: int = 30, size=(640, 480)) -> int:
    """Moving rectangle, circle and triangle on a grey gradient. Returns the frame count."""
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open a video writer (MJPG/AVI)")
    background = np.tile(np.linspace(40, 120, width, dtype=np.uint8), (height, 1))
    background = cv2.cvtColor(background, cv2.COLOR_GRAY2BGR)
    total = int(seconds * fps)
    for i in range(total):
        frame = background.copy()
        x = int(40 + (width - 200) * (0.5 + 0.5 * np.sin(i / 25)))
        cv2.rectangle(frame, (x, 80), (x + 120, 200), (60, 180, 240), -1)
        cv2.circle(frame, (width - x, 330), 60, (200, 90, 60), -1)
        tri = np.array([[x + 60, 260], [x, 400], [x + 120, 400]], np.int32)
        cv2.fillPoly(frame, [tri], (90, 200, 90))
        writer.write(frame)
    writer.release()
    return total


def analyzer_timing(video: Path, landmarks: str) -> tuple[int, int, float, float, str]:
    """Analyse every frame as fast as possible: (frames, faces, median ms, p95 ms, mode)."""
    config = replace(config_from_env(), landmarks=landmarks)
    analyzer = make_analyzer(config, load_thresholds(), load_landmark_indices())
    capture = cv2.VideoCapture(str(video))
    times: list[float] = []
    faces = 0
    while True:
        got, frame = capture.read()
        if not got:
            break
        start = time.perf_counter()
        faces += len(analyzer.analyze(frame))
        times.append((time.perf_counter() - start) * 1000)
    capture.release()
    analyzer.close()
    ordered = sorted(times)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    return len(times), faces, statistics.median(times), p95, analyzer.mode


async def run_app(video: Path, landmarks: str) -> tuple[int, float, int, bool]:
    """The whole process on the file in real time: (frames analysed, fps, faces seen, cam ok)."""
    config = replace(
        config_from_env(),
        camera_source=str(video),
        landmarks=landmarks,
        console=True,
        sim_servo=True,
        exit_at_eof=True,
        max_seconds=30.0,
        max_fps=0.0,  # no rate limit: analyse every frame the file delivers
    )
    app = VisionApp(config, load_thresholds())
    faces_seen = 0
    was_ok = False

    async def watch() -> None:
        nonlocal faces_seen, was_ok
        while True:
            snap = app.snapshot()
            faces_seen = max(faces_seen, len(snap.state.faces))
            was_ok = was_ok or snap.camera_ok
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch())
    started = time.monotonic()
    await app.run()
    elapsed = time.monotonic() - started
    watcher.cancel()
    frames = app.snapshot().frames
    return frames, frames / elapsed if elapsed else 0.0, faces_seen, was_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--landmarks", choices=["auto", "mediapipe", "yunet"], default="auto")
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="visiond_synth_") as folder:
        video = Path(folder) / "shapes.avi"
        total = make_video(video, seconds=args.seconds)
        print(f"SYNTHETIC video: {total} frames of moving shapes, 640x480, 30 fps (no people)")
        n, faces, median, p95, mode = analyzer_timing(video, args.landmarks)
        print(
            f"analyzer alone ({mode}): {n} frames, {faces} faces, median {median:.1f} ms, "
            f"p95 {p95:.1f} ms per frame (= {1000 / median:.0f} fps upper bound, zero faces)"
        )
        frames, fps, faces_seen, was_ok = asyncio.run(run_app(video, args.landmarks))
        print(
            f"visiond loop on the file in real time: {frames} of {total} frames analysed, "
            f"{fps:.1f} fps overall, camera_ok seen={was_ok}, most faces in a frame={faces_seen}"
        )
    return 0 if faces == 0 and faces_seen == 0 and frames > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
