"""Camera source with reconnect: a device index, a stream URL or a video file.

    CAMERA_SOURCE=0                                  USB webcam
    CAMERA_SOURCE=http://<phone-ip>:8080/video       Android "IP Webcam" app
    CAMERA_SOURCE=http://<phone-ip>:4747/video       DroidCam
    CAMERA_SOURCE=path/to/test.mp4                   video file (repeatable tests)

A grabber thread always reads the newest frame and keeps only that one, so a slow analysis
never works on old pictures. Frames stay in memory inside this process: nothing here writes
or sends a frame.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

from visiond.config import parse_camera_source, source_kind

STALE_AFTER_S = 2.0  # a live source that delivered nothing for this long is "not ok"
OPEN_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 5000


class CameraSource:
    def __init__(
        self,
        source: str,
        *,
        reconnect_s: float = 2.0,
        process_width: int = 640,
        mirror: bool = False,
        loop_file: bool = False,
    ) -> None:
        self.source = parse_camera_source(source)
        self.kind = source_kind(self.source)
        self.reconnect_s = reconnect_s
        self.process_width = process_width
        self.mirror = mirror
        self.loop_file = loop_file
        self.eof = False  # a video file reached its end (and loop_file is off)
        self.frames_read = 0
        self.reconnects = 0
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._last_frame_at = 0.0
        self._opened = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- public ----
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)  # daemon thread: a blocked open/read dies with us
            self._thread = None

    @property
    def ok(self) -> bool:
        """True while frames are arriving."""
        if not self._opened or self.eof:
            return False
        return time.monotonic() - self._last_frame_at <= STALE_AFTER_S

    def latest(self) -> tuple[int, np.ndarray | None]:
        """(sequence number, newest frame). The number changes with every new frame."""
        with self._lock:
            return self._seq, self._frame

    # ---- grabber thread ----
    def _open(self) -> Any:
        if self.kind == "url":
            try:
                params = [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    OPEN_TIMEOUT_MS,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    READ_TIMEOUT_MS,
                ]
                return cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, params)
            except (cv2.error, AttributeError):
                return cv2.VideoCapture(self.source)
        return cv2.VideoCapture(self.source)

    def _run(self) -> None:
        first = True
        while not self._stop.is_set():
            if not first:
                self.reconnects += 1
                if self._stop.wait(self.reconnect_s):
                    break
            first = False
            capture = self._open()
            try:
                if not capture.isOpened():
                    self._opened = False
                    continue
                self._opened = True
                self._read_until_failure(capture)
            finally:
                capture.release()
                self._opened = False
            if self.eof:
                break

    def _read_until_failure(self, capture: Any) -> None:
        period = 0.0
        if self.kind == "file":
            fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
            period = 1.0 / fps if 1.0 <= fps <= 240.0 else 1.0 / 30.0
        next_at = time.monotonic()
        while not self._stop.is_set():
            got, frame = capture.read()
            if not got or frame is None:
                if self.kind == "file":
                    if self.loop_file and self.frames_read > 0:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    self.eof = True
                return
            frame = self._prepare(frame)
            with self._lock:
                self._frame = frame
                self._seq += 1
            self.frames_read += 1
            self._last_frame_at = time.monotonic()
            if period:  # play a file at its own speed, like a camera would
                next_at += period
                delay = next_at - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_at = time.monotonic()

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if self.process_width and width > self.process_width:
            scale = self.process_width / width
            frame = cv2.resize(
                frame, (self.process_width, max(1, round(height * scale))), cv2.INTER_AREA
            )
        if self.mirror:
            frame = cv2.flip(frame, 1)
        return frame
