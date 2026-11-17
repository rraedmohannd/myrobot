"""The visiond process: a capture/analysis thread plus an asyncio task for the output.

The worker thread is the only place that touches frames. It hands over a `Snapshot` made of
numbers and flags; the asyncio side turns that into `vision.state` (WebSocket and console).
No OpenCV import at module level, so the logic and the round-trip tests run without a camera.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from visiond.config import Thresholds, VisionConfig, load_landmark_indices, load_thresholds
from visiond.link import RobotdLink
from visiond.logic import EMPTY_STATE, FaceObservation, LogicState, VisionLogic
from visiond.messages import ServoMessage, VadMessage, build_vision_state, console_line
from visiond.servo import PanController, SimulatedServo, bearing_right_deg, error_for_fixed_camera

IDLE_SLEEP_S = 0.005
NO_CAMERA_TICK_S = 0.2


class FrameSource(Protocol):
    eof: bool

    @property
    def ok(self) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> tuple[int, Any]: ...


class Analyzer(Protocol):
    mode: str

    def analyze(self, frame: Any) -> list[FaceObservation]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Snapshot:
    state: LogicState = EMPTY_STATE
    camera_ok: bool = False
    servo_target_deg: float = 0.0
    servo_sim_deg: float | None = None  # SIMULATED servo angle (None when not simulating)
    fps: float = 0.0
    frames: int = 0


class VisionApp:
    def __init__(
        self,
        config: VisionConfig,
        thresholds: Thresholds | None = None,
        *,
        camera: FrameSource | None = None,
        analyzer: Analyzer | None = None,
    ) -> None:
        self.config = config
        self.th = thresholds or load_thresholds()
        self.logic = VisionLogic(self.th, assume_vad=config.assume_speaking)
        self.controller = PanController(config.servo)
        self.sim_servo = (
            SimulatedServo(min_deg=config.servo.min_deg, max_deg=config.servo.max_deg)
            if config.sim_servo
            else None
        )
        self.link = RobotdLink(
            config.robotd_url,
            self._on_inbound,
            retry_s=config.reconnect_s,
            on_disconnect=self._on_disconnect,
        )
        self._camera = camera
        self._analyzer = analyzer
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._vad_speaking = False
        self._servo_actual: float | None = None
        self._stop = threading.Event()
        self._worker_error: BaseException | None = None
        self._t0 = time.monotonic()
        self.published = 0
        self.landmark_mode = ""

    # ---- labels for the console ----
    @property
    def vad_label(self) -> str:
        if self.config.assume_speaking:
            return "vad=ASSUMED(mouth motion)"
        return f"vad={'on' if self._vad_speaking else 'off'}" + (
            "" if self.link.connected else "(no robotd)"
        )

    def _servo_label(self, snap: Snapshot) -> str:
        if self.sim_servo is not None:
            return f"servo SIMULATED at={snap.servo_sim_deg or 0.0:+.1f}"
        return "servo"

    # ---- inbound (asyncio thread) ----
    def _on_inbound(self, message: VadMessage | ServoMessage) -> None:
        with self._lock:
            if isinstance(message, VadMessage):
                self._vad_speaking = message.speaking
            else:
                self._servo_actual = message.pan_deg

    def _on_disconnect(self) -> None:
        with self._lock:  # without robotd nobody tells us about speech or the servo
            self._vad_speaking = False
            self._servo_actual = None

    def now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    # ---- worker thread: capture -> detect -> track -> servo target ----
    def _make_models(self) -> tuple[FrameSource, Analyzer]:
        camera, analyzer = self._camera, self._analyzer
        if camera is None:
            from visiond.camera import CameraSource  # OpenCV is imported only here

            camera = CameraSource(
                self.config.camera_source,
                reconnect_s=self.config.reconnect_s,
                process_width=self.config.process_width,
                mirror=self.config.mirror,
                loop_file=self.config.loop_file,
            )
        if analyzer is None:
            from visiond.detectors import make_analyzer

            analyzer = make_analyzer(self.config, self.th, load_landmark_indices())
        return camera, analyzer

    def _worker(self) -> None:
        try:
            camera, analyzer = self._make_models()
            self.landmark_mode = analyzer.mode
        except BaseException as exc:  # noqa: BLE001 - reported by run()
            self._worker_error = exc
            self._stop.set()
            return
        camera.start()
        min_period = 1.0 / self.config.max_fps if self.config.max_fps > 0 else 0.0
        last_seq, last_run, last_tick = -1, 0.0, 0.0
        fps, frames = 0.0, 0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                seq, frame = camera.latest()
                camera_ok = camera.ok
                if camera_ok and frame is not None and seq != last_seq:
                    if now - last_run < min_period:
                        time.sleep(IDLE_SLEEP_S)
                        continue
                    if last_run:
                        instant = 1.0 / max(1e-6, now - last_run)
                        fps = instant if fps == 0.0 else 0.9 * fps + 0.1 * instant
                    last_seq, last_run, last_tick = seq, now, now
                    faces = analyzer.analyze(frame)
                    frames += 1
                    self._step(faces, True, fps, frames)
                    if self.config.debug_window:
                        self._show_debug(frame)
                elif not camera_ok and now - last_tick >= NO_CAMERA_TICK_S:
                    last_tick = now  # let tracks and the selection age out while blind
                    self._step([], False, 0.0, frames)
                    if camera.eof and self.config.exit_at_eof:
                        self._stop.set()
                else:
                    time.sleep(IDLE_SLEEP_S)
        except BaseException as exc:  # noqa: BLE001 - reported by run()
            self._worker_error = exc
            self._stop.set()
        finally:
            camera.stop()
            analyzer.close()

    def _step(self, faces: list[FaceObservation], camera_ok: bool, fps: float, frames: int) -> None:
        t_ms = self.now_ms()
        with self._lock:
            vad, actual = self._vad_speaking, self._servo_actual
        state = self.logic.update(faces, t_ms, vad)
        selected = state.selected_face()
        error: float | None = None
        if selected is not None:
            error = bearing_right_deg(selected.box.centre_x, self.config.servo.hfov_deg)
            if self.sim_servo is not None:  # SIMULATED: the camera itself does not turn
                error = error_for_fixed_camera(error, self.sim_servo.angle_deg)
        if self.sim_servo is not None:
            self.controller.set_actual(self.sim_servo.angle_deg)
        elif actual is not None:
            self.controller.set_actual(actual)
        target = self.controller.update(error, t_ms)
        sim_deg = self.sim_servo.step(target, t_ms) if self.sim_servo is not None else None
        snap = Snapshot(state, camera_ok, target, sim_deg, fps, frames)
        with self._lock:
            self._snapshot = snap

    def _show_debug(self, frame: Any) -> None:
        """Optional LOCAL window (off by default): boxes and ids drawn on the live picture."""
        import cv2

        snap = self.snapshot()
        view = frame.copy()
        height, width = view.shape[:2]
        for face in snap.state.faces:
            chosen = face.id == snap.state.selected_id
            p0 = (int(face.box.x * width), int(face.box.y * height))
            p1 = (int((face.box.x + face.box.w) * width), int((face.box.y + face.box.h) * height))
            colour = (122, 207, 62) if chosen else (255, 255, 255)
            cv2.rectangle(view, p0, p1, colour, 3 if chosen else 1)
            cv2.putText(view, str(face.id), p0, cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
        cv2.imshow("visiond debug (local only, nothing is saved)", view)
        cv2.waitKey(1)

    # ---- asyncio side ----
    def message(self) -> dict[str, Any]:
        snap = self.snapshot()
        return build_vision_state(
            snap.state, camera_ok=snap.camera_ok, servo_target_deg=snap.servo_target_deg
        )

    async def _publish_loop(self) -> None:
        period = 1.0 / self.config.publish_hz
        last_key: tuple[Any, ...] | None = None
        last_print = 0.0
        while not self._stop.is_set():
            snap = self.snapshot()
            message = build_vision_state(
                snap.state, camera_ok=snap.camera_ok, servo_target_deg=snap.servo_target_deg
            )
            message["t_ms"] = int(self.now_ms())
            if await self.link.send(message):
                self.published += 1
            if self.config.console:
                key = (
                    message["camera_ok"],
                    tuple((f["id"], f["facing"], f["speaking"]) for f in message["faces"]),
                    message["selected_id"],
                    message["engaged"],
                    message["active_speaker_id"],
                    message["overlap"],
                    round(message["servo_target_deg"] / 2),
                    snap.state.vad_speaking,
                )
                now = time.monotonic()
                if key != last_key or now - last_print >= 1.0:
                    last_key, last_print = key, now
                    vad = self.vad_label
                    if self.config.assume_speaking:
                        vad += "=on" if snap.state.vad_speaking else "=off"
                    line = console_line(
                        message, vad_label=vad, servo_label=self._servo_label(snap), fps=snap.fps
                    )
                    print(f"{self.now_ms() / 1000:7.1f}s {line}", flush=True)
            await asyncio.sleep(period)

    async def run(self) -> None:
        """Run until stopped (Ctrl+C), `max_seconds` passed, or the file ended (exit_at_eof)."""
        worker = threading.Thread(target=self._worker, name="vision", daemon=True)
        worker.start()
        link_task = asyncio.create_task(self.link.run())
        publish_task = asyncio.create_task(self._publish_loop())
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
                limit = self.config.max_seconds
                if limit and time.monotonic() - self._t0 >= limit:
                    break
        finally:
            self._stop.set()
            for task in (publish_task, link_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await asyncio.to_thread(worker.join, 8.0)
        if self._worker_error is not None:
            raise self._worker_error

    def stop(self) -> None:
        self._stop.set()
