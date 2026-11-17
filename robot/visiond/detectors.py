"""Local CPU models: YuNet face detection, MediaPipe Face Landmarker, 5-point fallback.

Per frame: YuNet finds the faces (boxes + 5 points). For each face, a padded crop goes to the
MediaPipe Face Landmarker (478 points) for the real mouth-opening ratio and the head pose.
When MediaPipe is not installed, its model file is missing, or it finds no face in the crop,
the face is described from YuNet's 5 points instead (the WEAK path: see geometry.py).

Frames and crops live only in memory inside this function call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from visiond.config import LANDMARKER_FILE, YUNET_FILE, Thresholds, VisionConfig
from visiond.geometry import (
    FivePoints,
    Point,
    head_pose_five,
    head_pose_mesh,
    is_facing,
    mouth_proxy_five,
    mouth_ratio,
    nose_position,
    nose_position_mesh,
)
from visiond.logic import Box, FaceObservation

CROP_MARGIN = 0.35  # the crop given to the landmarker is the YuNet box grown by this share
MODE_MEDIAPIPE = "mediapipe"
MODE_YUNET = "yunet-5pt (WEAK: no lip opening)"


class LandmarkModel(Protocol):
    """Anything that turns an RGB face crop into MediaPipe-style landmarks (or None)."""

    def landmarks(self, rgb_crop: np.ndarray) -> list[Point] | None: ...

    def close(self) -> None: ...


class MediaPipeLandmarks:
    """MediaPipe Face Landmarker (Tasks API), one face per crop, IMAGE mode."""

    def __init__(self, model_path: Path) -> None:
        import mediapipe as mp  # optional dependency: imported only when this path is used
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def landmarks(self, rgb_crop: np.ndarray) -> list[Point] | None:
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb_crop)
        )
        result = self._landmarker.detect(image)
        if not result.face_landmarks:
            return None
        return [Point(p.x, p.y, p.z) for p in result.face_landmarks[0]]

    def close(self) -> None:
        self._landmarker.close()


class FaceAnalyzer:
    """frame (BGR) -> list[FaceObservation]."""

    def __init__(
        self,
        yunet_path: Path,
        thresholds: Thresholds,
        landmark_model: LandmarkModel | None,
        *,
        landmark_indices: dict[str, int],
        score_min: float = 0.7,
    ) -> None:
        if not yunet_path.is_file():
            raise FileNotFoundError(
                f"{yunet_path} is missing: run `python robot/models/download_models.py`"
            )
        self.th = thresholds
        self.lm = landmark_indices
        self.landmark_model = landmark_model
        self.mode = MODE_MEDIAPIPE if landmark_model is not None else MODE_YUNET
        self._detector = cv2.FaceDetectorYN.create(
            str(yunet_path), "", (320, 320), score_min, 0.3, 50
        )
        self._size = (0, 0)

    def close(self) -> None:
        if self.landmark_model is not None:
            self.landmark_model.close()

    def analyze(self, frame: np.ndarray) -> list[FaceObservation]:
        height, width = frame.shape[:2]
        if (width, height) != self._size:
            self._detector.setInputSize((width, height))
            self._size = (width, height)
        _, rows = self._detector.detect(frame)
        if rows is None:
            return []
        ordered = sorted(rows, key=lambda r: -float(r[2] * r[3]))[: self.th.max_faces]
        return [self._describe(frame, row, width, height) for row in ordered]

    # ---- one face ----
    def _describe(
        self, frame: np.ndarray, row: np.ndarray, width: int, height: int
    ) -> FaceObservation:
        box = Box(
            float(row[0]) / width, float(row[1]) / height,
            float(row[2]) / width, float(row[3]) / height,
        )  # fmt: skip
        if self.landmark_model is not None:
            observation = self._with_mesh(frame, row, box)
            if observation is not None:
                return observation
        return self._with_five_points(row, box)

    def _with_mesh(self, frame: np.ndarray, row: np.ndarray, box: Box) -> FaceObservation | None:
        height, width = frame.shape[:2]
        x, y, w, h = (float(v) for v in row[:4])
        side = max(w, h) * (1 + 2 * CROP_MARGIN)
        cx, cy = x + w / 2, y + h / 2
        x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x1, y1 = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        crop = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        assert self.landmark_model is not None
        points = self.landmark_model.landmarks(crop)
        if points is None:
            return None
        yaw, pitch = head_pose_mesh(points, x1 - x0, y1 - y0, self.lm)
        facing = is_facing(
            nose_position_mesh(points, self.lm),
            yaw,
            pitch,
            pos_min=self.th.looking_pos_min,
            pos_max=self.th.looking_pos_max,
            yaw_max_deg=self.th.yaw_max_deg,
            pitch_max_deg=self.th.pitch_max_deg,
        )
        return FaceObservation(
            box=box, mouth=mouth_ratio(points, self.lm), facing=facing, yaw_deg=yaw, pitch_deg=pitch
        )

    def _with_five_points(self, row: np.ndarray, box: Box) -> FaceObservation:
        pts = [Point(float(row[i]), float(row[i + 1])) for i in range(4, 14, 2)]
        five = FivePoints(*pts)
        yaw, pitch = head_pose_five(five)
        x, w = float(row[0]), float(row[2])
        facing = is_facing(
            nose_position(x, x + w, five.nose.x),
            yaw,
            pitch,
            pos_min=self.th.looking_pos_min,
            pos_max=self.th.looking_pos_max,
            yaw_max_deg=self.th.yaw_max_deg,
            pitch_max_deg=self.th.pitch_max_deg,
        )
        return FaceObservation(
            box=box,
            mouth=mouth_proxy_five(five),
            facing=facing,
            yaw_deg=yaw,
            pitch_deg=pitch,
            mouth_weak=True,
        )


def make_analyzer(config: VisionConfig, thresholds: Thresholds, lm: dict[str, int]) -> FaceAnalyzer:
    """Build the analyzer for `config.landmarks`: auto | mediapipe | yunet."""
    model: LandmarkModel | None = None
    if config.landmarks in ("auto", "mediapipe"):
        path = config.models_dir / LANDMARKER_FILE
        try:
            if not path.is_file():
                raise FileNotFoundError(f"{path} is missing")
            model = MediaPipeLandmarks(path)
        except Exception as exc:  # noqa: BLE001 - any import/model problem means "fall back"
            if config.landmarks == "mediapipe":
                raise
            print(f"[visiond] MediaPipe landmarks unavailable ({exc}); using {MODE_YUNET}")
    return FaceAnalyzer(
        config.models_dir / YUNET_FILE,
        thresholds,
        model,
        landmark_indices=lm,
        score_min=config.detect_score_min,
    )
