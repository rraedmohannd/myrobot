"""Pure "who is there / who is engaged / who is speaking" logic (no OpenCV import).

FaceTracker and SpeechObserver are line-by-line ports of the classes with the same names in
backend/app/static/speaker-camera.js, with the same thresholds (shared/vision_thresholds.json).
ParticipantSelector and VisionLogic exist only on the robot.

Everything here takes plain numbers and a time in milliseconds, so it is tested with synthetic
sequences (robot/tests/visiond).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from visiond.config import Thresholds


class Box(NamedTuple):
    """Top-left corner and size as fractions (0..1) of the frame."""

    x: float
    y: float
    w: float
    h: float

    @property
    def centre_x(self) -> float:
        return self.x + self.w / 2

    @property
    def area(self) -> float:
        return self.w * self.h


def iou(a: Box, b: Box) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def std(values: Sequence[float]) -> float:
    """Population standard deviation (0 for fewer than two values), as in the prototype."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


@dataclass(frozen=True)
class FaceObservation:
    """One face in one frame, as produced by visiond.detectors."""

    box: Box
    mouth: float  # mouth-opening ratio (or the weak YuNet proxy when mouth_weak is set)
    facing: bool
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    mouth_weak: bool = False  # True: `mouth` is the YuNet proxy, not the real lip opening


@dataclass
class Track:
    id: int
    box: Box
    last_seen: float
    history: list[tuple[float, float]] = field(default_factory=list)
    facing_since: float | None = None
    facing: bool = False
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    mouth: float = 0.0
    mouth_weak: bool = False
    activity: float = 0.0
    visible: bool = True

    def facing_ms(self, t_ms: float) -> float:
        return 0.0 if self.facing_since is None else t_ms - self.facing_since


class FaceTracker:
    """Keeps a stable id per face while it stays in view (greedy IoU matching).

    A face that is hidden for up to `max_age_ms` keeps its id when it reappears near the same
    place; after that it gets a new id.
    """

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.th = thresholds or Thresholds()
        self.tracks: list[Track] = []
        self.next_id = 1

    def update(self, faces: Sequence[FaceObservation], t_ms: float) -> list[Track]:
        """Feed the faces of one frame; returns the tracks visible in this frame."""
        # Expired tracks go first, so a gap in the frames (camera stall) cannot revive an old
        # id. With frames arriving continuously this is the same as the prototype.
        self.tracks = [t for t in self.tracks if t_ms - t.last_seen <= self.th.max_age_ms]
        pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(self.tracks):
            for fi, face in enumerate(faces):
                value = iou(track.box, face.box)
                if value >= self.th.iou_min:
                    pairs.append((value, ti, fi))
        pairs.sort(key=lambda p: -p[0])
        used_t: set[int] = set()
        used_f: set[int] = set()
        for _, ti, fi in pairs:
            if ti in used_t or fi in used_f:
                continue
            used_t.add(ti)
            used_f.add(fi)
            self._feed(self.tracks[ti], faces[fi], t_ms)
        for fi, face in enumerate(faces):
            if fi in used_f:
                continue
            track = Track(id=self.next_id, box=face.box, last_seen=t_ms)
            self.next_id += 1
            self._feed(track, face, t_ms)
            self.tracks.append(track)
        for track in self.tracks:
            if track.last_seen != t_ms:
                track.visible = False
        self.tracks = [t for t in self.tracks if t_ms - t.last_seen <= self.th.max_age_ms]
        return [t for t in self.tracks if t.visible]

    def _feed(self, track: Track, face: FaceObservation, t_ms: float) -> None:
        track.box = face.box
        track.mouth = face.mouth
        track.mouth_weak = face.mouth_weak
        track.facing = face.facing
        track.yaw_deg = face.yaw_deg
        track.pitch_deg = face.pitch_deg
        track.last_seen = t_ms
        track.visible = True
        track.history.append((t_ms, face.mouth))
        while track.history and t_ms - track.history[0][0] > self.th.window_ms:
            track.history.pop(0)
        track.activity = std([h[1] for h in track.history])
        if face.facing:
            if track.facing_since is None:
                track.facing_since = t_ms
        else:
            track.facing_since = None


def is_talking(track: Track, th: Thresholds) -> bool:
    """Mouth moving enough in this frame (says nothing about the VAD)."""
    limit = th.fallback_activity_min if track.mouth_weak else th.activity_min
    return track.activity >= limit


@dataclass(frozen=True)
class SpeechResult:
    face_track_id: int | None
    overlap: bool
    frames: int


class SpeechObserver:
    """Collects the frames of ONE utterance (while the VAD says "speech"): who spoke, overlap."""

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.th = thresholds or Thresholds()
        self.reset()

    def reset(self) -> None:
        self.frames = 0
        self.overlap_frames = 0
        self.counts: dict[int, int] = {}
        self.sums: dict[int, float] = {}
        self.seen: set[int] = set()

    def frame(self, tracks: Iterable[Track]) -> None:
        self.frames += 1
        active = 0
        for track in tracks:
            self.seen.add(track.id)
            if not is_talking(track, self.th):
                continue
            active += 1
            self.counts[track.id] = self.counts.get(track.id, 0) + 1
            self.sums[track.id] = self.sums.get(track.id, 0.0) + track.activity
        if active >= 2:
            self.overlap_frames += 1

    def result(self) -> SpeechResult:
        if self.frames == 0:
            return SpeechResult(None, False, 0)
        best: int | None = None
        for track_id, n in self.counts.items():
            if (
                best is None
                or n > self.counts[best]
                or (n == self.counts[best] and self.sums[track_id] > self.sums[best])
            ):
                best = track_id
        # Nobody's lips moved enough, but exactly one face was there: it can only be that one.
        if best is None and len(self.seen) == 1:
            best = next(iter(self.seen))
        overlap = self.overlap_frames / self.frames > self.th.overlap_share
        return SpeechResult(best, overlap, self.frames)


class ParticipantSelector:
    """ONE selected participant, with hysteresis.

    * Nobody selected: the face that has been facing the camera the longest, once it has done
      so for `engage_ms`.
    * Somebody selected: they stay selected while their track exists (a short occlusion does
      not drop them, because the tracker keeps the id for `max_age_ms`).
    * A switch happens only while the VAD says speech, when ANOTHER face has been the talking
      one for `switch_hold_ms` without the selected face talking, and not sooner than
      `min_dwell_ms` after the previous selection. Two people talking at once therefore never
      cause a switch, and a nod or one word from the second person does not either.
    """

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.th = thresholds or Thresholds()
        self.selected_id: int | None = None
        self.selected_at: float = 0.0
        self.challenger_id: int | None = None
        self.challenge_ms: float = 0.0
        self._last_t: float | None = None

    def update(
        self, alive: Sequence[Track], visible: Sequence[Track], t_ms: float, vad_speaking: bool
    ) -> int | None:
        dt = 0.0 if self._last_t is None else max(0.0, min(200.0, t_ms - self._last_t))
        self._last_t = t_ms
        if self.selected_id is not None and all(t.id != self.selected_id for t in alive):
            self.selected_id = None
            self._clear_challenge()
        if self.selected_id is None:
            ready = [t for t in visible if t.facing_ms(t_ms) >= self.th.engage_ms]
            if ready:
                first = min(ready, key=lambda t: (t.facing_since, -t.box.area))
                self._select(first.id, t_ms)
            return self.selected_id
        if not vad_speaking:
            self._clear_challenge()
            return self.selected_id
        talkers = [t for t in visible if is_talking(t, self.th)]
        if not talkers or any(t.id == self.selected_id for t in talkers):
            self._clear_challenge()
            return self.selected_id
        top = max(talkers, key=lambda t: t.activity)
        if top.id == self.challenger_id:
            self.challenge_ms += dt
        else:
            self.challenger_id, self.challenge_ms = top.id, 0.0
        if (
            self.challenge_ms >= self.th.switch_hold_ms
            and t_ms - self.selected_at >= self.th.min_dwell_ms
        ):
            self._select(top.id, t_ms)
        return self.selected_id

    def _select(self, track_id: int, t_ms: float) -> None:
        self.selected_id = track_id
        self.selected_at = t_ms
        self._clear_challenge()

    def _clear_challenge(self) -> None:
        self.challenger_id = None
        self.challenge_ms = 0.0


class AssumedVad:
    """ASSUMED voice activity for tests without a microphone (--assume-speaking).

    "Somebody is speaking" = at least one visible mouth is moving; the utterance ends after
    `assumed_vad_hangover_ms` without mouth motion. This is NOT a real VAD: a silent yawn
    counts as speech and a ventriloquist does not. It is labelled ASSUMED wherever shown.
    """

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.th = thresholds or Thresholds()
        self._last_motion: float | None = None

    def update(self, visible: Sequence[Track], t_ms: float) -> bool:
        if any(is_talking(t, self.th) for t in visible):
            self._last_motion = t_ms
        if self._last_motion is None:
            return False
        return t_ms - self._last_motion <= self.th.assumed_vad_hangover_ms


@dataclass(frozen=True)
class FaceState:
    id: int
    box: Box
    facing: bool
    yaw_deg: float
    pitch_deg: float
    mouth: float
    speaking: bool  # the mouth is moving in this frame (mouth activity above the threshold)
    facing_ms: float


@dataclass(frozen=True)
class LogicState:
    t_ms: float
    faces: tuple[FaceState, ...]
    selected_id: int | None
    engaged: bool
    active_speaker_id: int | None
    overlap: bool
    vad_speaking: bool

    def selected_face(self) -> FaceState | None:
        for face in self.faces:
            if face.id == self.selected_id:
                return face
        return None


EMPTY_STATE = LogicState(0.0, (), None, False, None, False, False)
MIN_FRAMES_FOR_LIVE_OVERLAP = 5


class VisionLogic:
    """Tracker + engagement + active speaker + overlap + selected participant, per frame.

    active_speaker_id: while the VAD says speech it is the face whose mouth is moving most
    right now (it keeps the last one when no mouth moves); when the utterance ends it becomes
    the utterance's result (the face with the most talking frames, as the prototype sends with
    `audio.end`) and stays until the next utterance starts.
    overlap: live during the utterance (after a few frames), final value held after it.
    """

    def __init__(self, thresholds: Thresholds | None = None, *, assume_vad: bool = False) -> None:
        self.th = thresholds or Thresholds()
        self.tracker = FaceTracker(self.th)
        self.observer = SpeechObserver(self.th)
        self.selector = ParticipantSelector(self.th)
        self.assumed_vad = AssumedVad(self.th) if assume_vad else None
        self._was_speaking = False
        self._active_id: int | None = None
        self._overlap = False

    def update(
        self, faces: Sequence[FaceObservation], t_ms: float, vad_speaking: bool = False
    ) -> LogicState:
        visible = self.tracker.update(list(faces)[: self.th.max_faces], t_ms)
        if self.assumed_vad is not None:
            vad_speaking = self.assumed_vad.update(visible, t_ms)
        if vad_speaking and not self._was_speaking:
            self.observer.reset()
            self._active_id = None
            self._overlap = False
        if vad_speaking:
            self.observer.frame(visible)
            talkers = [t for t in visible if is_talking(t, self.th)]
            if talkers:
                self._active_id = max(talkers, key=lambda t: t.activity).id
            elif self._active_id is None and len(visible) == 1:
                self._active_id = visible[0].id
            if self.observer.frames >= MIN_FRAMES_FOR_LIVE_OVERLAP:
                self._overlap = self.observer.result().overlap
        elif self._was_speaking:
            result = self.observer.result()
            self._active_id = result.face_track_id
            self._overlap = result.overlap
        self._was_speaking = vad_speaking

        selected_id = self.selector.update(self.tracker.tracks, visible, t_ms, vad_speaking)
        engaged = any(
            t.id == selected_id and t.facing_ms(t_ms) >= self.th.engage_ms for t in visible
        )
        states = tuple(
            FaceState(
                id=t.id,
                box=t.box,
                facing=t.facing,
                yaw_deg=t.yaw_deg,
                pitch_deg=t.pitch_deg,
                mouth=t.mouth,
                speaking=is_talking(t, self.th),
                facing_ms=t.facing_ms(t_ms),
            )
            for t in visible
        )
        return LogicState(
            t_ms=t_ms,
            faces=states,
            selected_id=selected_id,
            engaged=engaged,
            active_speaker_id=self._active_id,
            overlap=self._overlap,
            vad_speaking=vad_speaking,
        )
