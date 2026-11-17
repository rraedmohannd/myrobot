"""Camera source and detector glue with OpenCV, on SYNTHETIC pictures only (shapes, no people).

Skipped when OpenCV is not installed. The YuNet test also needs the model file
(`python robot/models/download_models.py`) and is skipped without it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from visiond.camera import CameraSource  # noqa: E402
from visiond.config import (  # noqa: E402
    DEFAULT_LANDMARKS,
    DEFAULT_MODELS_DIR,
    YUNET_FILE,
    Thresholds,
    parse_camera_source,
    source_kind,
)
from visiond.detectors import MODE_MEDIAPIPE, MODE_YUNET, FaceAnalyzer  # noqa: E402
from visiond.geometry import Point  # noqa: E402

YUNET = DEFAULT_MODELS_DIR / YUNET_FILE
needs_yunet = pytest.mark.skipif(not YUNET.is_file(), reason="YuNet model not downloaded")


def shapes_frame(i: int, width: int = 320, height: int = 240):
    frame = np.full((height, width, 3), 70, np.uint8)
    x = 20 + (i * 7) % (width - 100)
    cv2.rectangle(frame, (x, 40), (x + 60, 120), (60, 180, 240), -1)
    cv2.circle(frame, (width - x, 180), 30, (200, 90, 60), -1)
    return frame


def make_video(path: Path, frames: int = 20, fps: int = 20, size=(320, 240)) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    if not writer.isOpened():
        pytest.skip("this OpenCV build cannot write MJPG/AVI")
    for i in range(frames):
        writer.write(shapes_frame(i, *size))
    writer.release()
    return path


def wait_until(predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_source_kinds() -> None:
    assert parse_camera_source("0") == 0 and source_kind(0) == "device"
    assert parse_camera_source(" 2 ") == 2
    assert source_kind(parse_camera_source("http://192.168.1.23:8080/video")) == "url"
    assert source_kind(parse_camera_source("http://192.168.1.23:4747/video")) == "url"
    assert source_kind(parse_camera_source("clips/test.avi")) == "file"


def test_video_file_is_read_then_ends(tmp_path: Path) -> None:
    camera = CameraSource(str(make_video(tmp_path / "shapes.avi")), reconnect_s=0.1)
    assert not camera.ok
    camera.start()
    try:
        assert wait_until(lambda: camera.latest()[1] is not None)
        assert camera.ok
        seq, frame = camera.latest()
        assert seq >= 1 and frame.shape == (240, 320, 3)
        assert wait_until(lambda: camera.eof)  # 20 frames at 20 fps = 1 s
        assert camera.frames_read == 20
        assert not camera.ok
    finally:
        camera.stop()


def test_video_file_can_loop_and_is_scaled_and_mirrored(tmp_path: Path) -> None:
    video = make_video(tmp_path / "wide.avi", frames=6, fps=30, size=(640, 480))
    camera = CameraSource(str(video), process_width=320, loop_file=True, mirror=True)
    camera.start()
    try:
        assert wait_until(lambda: camera.frames_read > 12)  # more than the file holds
        assert camera.ok and not camera.eof
        assert camera.latest()[1].shape == (240, 320, 3)
    finally:
        camera.stop()


def test_missing_source_reports_not_ok_and_keeps_retrying(tmp_path: Path) -> None:
    target = tmp_path / "later.avi"
    camera = CameraSource(str(target), reconnect_s=0.1)
    camera.start()
    try:
        assert wait_until(lambda: camera.reconnects >= 2)
        assert not camera.ok and camera.latest() == (0, None)
        make_video(target)  # the "camera" appears: the next retry picks it up
        assert wait_until(lambda: camera.latest()[1] is not None)
    finally:
        camera.stop()


@needs_yunet
def test_yunet_runs_and_finds_no_face_in_shapes() -> None:
    analyzer = FaceAnalyzer(YUNET, Thresholds(), None, landmark_indices=DEFAULT_LANDMARKS)
    assert analyzer.mode == MODE_YUNET
    for i in range(5):
        assert analyzer.analyze(shapes_frame(i)) == []
    assert analyzer.analyze(shapes_frame(0, 640, 480)) == []  # the input size may change


def test_missing_model_gives_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="download_models.py"):
        FaceAnalyzer(tmp_path / "none.onnx", Thresholds(), None, landmark_indices=DEFAULT_LANDMARKS)


def yunet_row(yaw_shift: float = 0.0, mouth_width: float = 44.0):
    """A YuNet output row (pixels) written by hand: box, 5 points, score. Not a detection."""
    x, y, w, h = 100.0, 60.0, 100.0, 120.0
    cx = x + w / 2
    return np.array(
        [x, y, w, h,
         cx - 22, 105, cx + 22, 105,            # right eye, left eye
         cx + yaw_shift, 128,                   # nose tip
         cx - mouth_width / 2, 150, cx + mouth_width / 2, 150,
         0.95],
        np.float32,
    )  # fmt: skip


class FakeMesh:
    """Stands in for MediaPipe: returns a fixed synthetic 478-point mesh, or nothing."""

    def __init__(self, points: list[Point] | None) -> None:
        self.points = points
        self.crops: list[tuple[int, ...]] = []

    def landmarks(self, rgb_crop):
        self.crops.append(rgb_crop.shape)
        return self.points

    def close(self) -> None: ...


@needs_yunet
def test_five_point_fallback_description() -> None:
    analyzer = FaceAnalyzer(YUNET, Thresholds(), None, landmark_indices=DEFAULT_LANDMARKS)
    frame = shapes_frame(0)
    front = analyzer._describe(frame, yunet_row(), 320, 240)
    assert front.mouth_weak and front.facing
    assert front.yaw_deg == pytest.approx(0.0, abs=0.01)
    assert front.box == pytest.approx((100 / 320, 60 / 240, 100 / 320, 120 / 240))
    assert front.mouth == pytest.approx(1.0)  # 44 px mouth / 44 px between the eyes
    turned = analyzer._describe(frame, yunet_row(yaw_shift=30.0), 320, 240)
    assert turned.yaw_deg > 30 and not turned.facing


@needs_yunet
def test_mesh_path_uses_a_padded_crop_and_falls_back_when_the_mesh_is_missing() -> None:
    from test_geometry import mesh

    fake = FakeMesh(mesh(ratio=0.3, yaw_deg=10.0))
    analyzer = FaceAnalyzer(YUNET, Thresholds(), fake, landmark_indices=DEFAULT_LANDMARKS)
    assert analyzer.mode == MODE_MEDIAPIPE
    seen = analyzer._describe(shapes_frame(0), yunet_row(), 320, 240)
    assert not seen.mouth_weak and seen.facing
    assert seen.mouth == pytest.approx(0.3)
    assert seen.yaw_deg == pytest.approx(10.0, abs=0.5)
    height, width, channels = fake.crops[0]
    assert channels == 3 and width > 100 and height > 120  # larger than the YuNet box
    assert width <= 320 and height <= 240  # and inside the frame

    fake.points = None  # the landmarker found no face in the crop
    weak = analyzer._describe(shapes_frame(0), yunet_row(), 320, 240)
    assert weak.mouth_weak
