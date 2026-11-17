"""Pure logic of visiond with synthetic sequences: tracking, engagement, speaker, selection."""

from __future__ import annotations

import pytest
from visiond_synth import FRAME_MS, LEFT_X, RIGHT_X, SILENT, face, talking

from visiond.config import Thresholds
from visiond.logic import (
    AssumedVad,
    Box,
    FaceTracker,
    LogicState,
    SpeechObserver,
    VisionLogic,
    iou,
    std,
)

TH = Thresholds()


class Scene:
    """Drives a VisionLogic frame by frame at 15 fps."""

    def __init__(self, logic: VisionLogic | None = None) -> None:
        self.logic = logic or VisionLogic(TH)
        self.i = 0
        self.state: LogicState | None = None

    @property
    def t_ms(self) -> float:
        return self.i * FRAME_MS

    def run(self, seconds: float, make_faces, *, vad: bool = False) -> LogicState:
        """make_faces(i) -> list of observations for frame i."""
        for _ in range(round(seconds * 1000 / FRAME_MS)):
            self.state = self.logic.update(make_faces(self.i), self.t_ms, vad)
            self.i += 1
        assert self.state is not None
        return self.state


def two(left_mouth, right_mouth, *, left_facing: bool = True, right_facing: bool = True):
    """Two people side by side; each mouth is a function of the frame index or a constant."""

    def value(m, i):
        return m(i) if callable(m) else m

    return lambda i: [
        face(LEFT_X, value(left_mouth, i), facing=left_facing),
        face(RIGHT_X, value(right_mouth, i), facing=right_facing),
    ]


# ---------------- geometry helpers (same numbers as the JS test) ----------------
def test_iou_and_std_match_the_prototype() -> None:
    assert iou(Box(0, 0, 1, 1), Box(0, 0, 1, 1)) == 1
    assert iou(Box(0, 0, 1, 1), Box(2, 2, 1, 1)) == 0
    assert iou(Box(0, 0, 2, 1), Box(1, 0, 2, 1)) == pytest.approx(1 / 3)
    assert std([5]) == 0
    assert std([1, 3]) == pytest.approx(1)


# ---------------- tracking ----------------
def test_ids_stay_stable_while_faces_move() -> None:
    tracker = FaceTracker(TH)
    ids: list[tuple[int, ...]] = []
    for i in range(60):
        drift = 0.002 * i  # both people move slowly to the right
        tracks = tracker.update([face(LEFT_X + drift), face(RIGHT_X + drift)], i * FRAME_MS)
        ids.append(tuple(t.id for t in tracks))
    assert set(ids) == {(1, 2)}


def test_detection_order_does_not_swap_ids() -> None:
    tracker = FaceTracker(TH)
    tracker.update([face(LEFT_X), face(RIGHT_X)], 0)
    tracks = tracker.update([face(RIGHT_X), face(LEFT_X)], FRAME_MS)
    by_id = {t.id: t.box.x for t in tracks}
    assert by_id == {1: LEFT_X, 2: RIGHT_X}


def test_brief_occlusion_keeps_the_id_and_a_long_one_does_not() -> None:
    tracker = FaceTracker(TH)
    tracker.update([face(LEFT_X)], 0)
    assert tracker.update([], 300) == []  # hidden: not reported as visible
    back = tracker.update([face(LEFT_X + 0.02)], 600)  # 0.6 s later, nearly the same place
    assert [t.id for t in back] == [1]
    assert tracker.update([], 900) == []
    late = tracker.update([face(LEFT_X)], 600 + TH.max_age_ms + 100)  # unseen for 1.1 s
    assert [t.id for t in late] == [2]


def test_a_new_face_far_away_gets_a_new_id() -> None:
    tracker = FaceTracker(TH)
    tracker.update([face(LEFT_X)], 0)
    tracks = tracker.update([face(LEFT_X), face(RIGHT_X)], FRAME_MS)
    assert sorted(t.id for t in tracks) == [1, 2]


def test_mouth_activity_is_the_std_over_the_window() -> None:
    tracker = FaceTracker(TH)
    for i in range(30):
        (track,) = tracker.update([face(LEFT_X, talking(i))], i * FRAME_MS)
    assert track.activity >= TH.activity_min
    assert all(track.last_seen - t <= TH.window_ms for t, _ in track.history)
    for i in range(30, 60):
        (track,) = tracker.update([face(LEFT_X, SILENT)], i * FRAME_MS)
    assert track.activity < TH.activity_min


def test_only_max_faces_are_tracked() -> None:
    logic = VisionLogic(TH)
    many = [face(0.02 + 0.16 * k, w=0.1) for k in range(6)]
    assert len(logic.update(many, 0).faces) == TH.max_faces


# ---------------- engagement ----------------
def test_engagement_needs_n_seconds_of_facing() -> None:
    scene = Scene()
    state = scene.run(1.4, lambda i: [face(LEFT_X)])
    assert not state.engaged and state.selected_id is None
    state = scene.run(0.3, lambda i: [face(LEFT_X)])  # 1.7 s in total
    assert state.engaged and state.selected_id == 1


def test_engagement_time_is_the_shared_threshold() -> None:
    quick = Scene(VisionLogic(Thresholds(engage_ms=500)))
    assert quick.run(0.7, lambda i: [face(LEFT_X)]).engaged
    assert not Scene().run(0.7, lambda i: [face(LEFT_X)]).engaged


def test_looking_away_ends_engagement_and_the_timer_restarts() -> None:
    scene = Scene()
    assert scene.run(2.0, lambda i: [face(LEFT_X)]).engaged
    state = scene.run(0.2, lambda i: [face(LEFT_X, facing=False)])
    assert not state.engaged
    assert state.selected_id == 1  # still the selected participant, just not engaged
    state = scene.run(1.0, lambda i: [face(LEFT_X)])  # facing again, but only for 1 s
    assert not state.engaged
    assert scene.run(0.7, lambda i: [face(LEFT_X)]).engaged


def test_a_face_that_never_faces_the_camera_is_not_selected() -> None:
    state = Scene().run(3.0, lambda i: [face(LEFT_X, facing=False)])
    assert state.selected_id is None and not state.engaged
    assert len(state.faces) == 1 and not state.faces[0].facing


# ---------------- active speaker ----------------
def test_no_vad_means_no_active_speaker() -> None:
    state = Scene().run(2.0, two(talking, SILENT), vad=False)
    assert state.active_speaker_id is None and not state.overlap
    assert state.faces[0].speaking  # the mouth moves, but nobody is heard


def test_active_speaker_follows_the_moving_mouth_under_vad() -> None:
    scene = Scene()
    scene.run(2.0, two(SILENT, SILENT))
    state = scene.run(2.0, two(talking, SILENT), vad=True)
    assert state.active_speaker_id == 1 and not state.overlap
    state = scene.run(0.5, two(SILENT, SILENT), vad=False)
    assert state.active_speaker_id == 1  # the utterance's result is held after it ends
    scene.run(0.5, two(SILENT, SILENT), vad=False)
    state = scene.run(2.0, two(SILENT, talking), vad=True)
    assert state.active_speaker_id == 2 and not state.overlap
    assert [f.speaking for f in state.faces] == [False, True]


def test_utterance_result_is_the_face_with_most_talking_frames() -> None:
    scene = Scene()
    scene.run(1.0, two(SILENT, SILENT))
    scene.run(3.0, two(talking, SILENT), vad=True)
    scene.run(0.9, two(SILENT, talking), vad=True)  # the other one says a word at the end
    state = scene.run(0.2, two(SILENT, SILENT), vad=False)
    assert state.active_speaker_id == 1


def test_one_silent_face_under_vad_is_the_speaker() -> None:
    state = Scene().run(1.0, lambda i: [face(LEFT_X, SILENT)], vad=True)
    assert state.active_speaker_id == 1


def test_two_silent_faces_under_vad_give_no_speaker() -> None:
    scene = Scene()
    scene.run(1.0, two(SILENT, SILENT), vad=True)
    assert scene.run(0.2, two(SILENT, SILENT), vad=False).active_speaker_id is None


# ---------------- overlap ----------------
def test_overlap_when_both_talk_and_not_when_taking_turns() -> None:
    scene = Scene()
    scene.run(1.0, two(SILENT, SILENT))
    state = scene.run(2.0, two(talking, lambda i: talking(i + 2)), vad=True)
    assert state.overlap
    assert scene.run(0.3, two(SILENT, SILENT), vad=False).overlap  # held after the utterance
    scene.run(1.0, two(SILENT, SILENT), vad=False)
    state = scene.run(2.0, two(talking, SILENT), vad=True)
    assert not state.overlap  # a new utterance starts clean


def test_overlap_share_threshold() -> None:
    observer = SpeechObserver(TH)
    tracker = FaceTracker(TH)
    # Both talk for the first 40 frames, then only the left one for 160 frames: 20 % < 30 %.
    for i in range(200):
        right = talking(i + 2) if i < 40 else SILENT
        observer.frame(tracker.update([face(LEFT_X, talking(i)), face(RIGHT_X, right)], i * 66.7))
    result = observer.result()
    assert result.face_track_id == 1 and not result.overlap
    assert 0 < observer.overlap_frames / observer.frames <= TH.overlap_share


# ---------------- one selected participant, with hysteresis ----------------
def engaged_pair() -> Scene:
    """Left person arrives first and is selected; the right person joins later."""
    scene = Scene()
    scene.run(2.0, lambda i: [face(LEFT_X)])
    state = scene.run(2.5, two(SILENT, SILENT))
    assert state.selected_id == 1
    return scene


def test_the_first_person_facing_is_selected_and_a_second_does_not_steal() -> None:
    scene = engaged_pair()
    assert scene.run(5.0, two(SILENT, SILENT)).selected_id == 1


def test_a_short_remark_from_the_other_person_does_not_switch() -> None:
    scene = engaged_pair()
    state = scene.run(0.5, two(SILENT, talking), vad=True)
    assert state.selected_id == 1
    assert state.active_speaker_id == 2  # the speaker is reported, the selection is stable
    assert scene.run(1.0, two(SILENT, SILENT)).selected_id == 1


def test_sustained_speech_from_the_other_person_switches_once() -> None:
    scene = engaged_pair()
    start_ms = scene.t_ms
    selections: list[int | None] = []
    switched_after_ms: float | None = None
    for _ in range(45):  # 3 s, frame by frame
        state = scene.run(FRAME_MS / 1000, two(SILENT, talking), vad=True)
        selections.append(state.selected_id)
        if state.selected_id == 2 and switched_after_ms is None:
            switched_after_ms = state.t_ms - start_ms
    assert selections[0] == 1 and selections[-1] == 2
    assert sum(1 for a, b in zip(selections, selections[1:], strict=False) if a != b) == 1
    assert switched_after_ms is not None
    assert TH.switch_hold_ms <= switched_after_ms <= TH.switch_hold_ms + 600


def test_talking_at_the_same_time_never_switches() -> None:
    scene = engaged_pair()
    state = scene.run(6.0, two(talking, lambda i: talking(i + 2)), vad=True)
    assert state.selected_id == 1 and state.overlap


def test_fast_alternation_does_not_ping_pong() -> None:
    scene = engaged_pair()
    seen = set()
    for k in range(20):  # 0.4 s each, back and forth for 8 s
        faces = two(talking, SILENT) if k % 2 == 0 else two(SILENT, talking)
        seen.add(scene.run(0.4, faces, vad=True).selected_id)
    assert seen == {1}


def test_no_switch_back_before_the_minimum_dwell() -> None:
    scene = Scene(VisionLogic(Thresholds(min_dwell_ms=4000)))
    scene.run(2.0, lambda i: [face(LEFT_X)])
    assert scene.run(4.5, two(SILENT, SILENT)).selected_id == 1
    assert scene.run(1.5, two(SILENT, talking), vad=True).selected_id == 2  # after about 0.9 s
    scene.run(0.6, two(SILENT, SILENT), vad=True)  # the mouth-activity window empties
    # The first person answers at once and talks for longer than switch_hold_ms, but the
    # switch back has to wait until min_dwell_ms after the previous switch.
    assert scene.run(1.2, two(talking, SILENT), vad=True).selected_id == 2
    assert scene.run(3.0, two(talking, SILENT), vad=True).selected_id == 1


def test_no_switch_without_vad() -> None:
    scene = engaged_pair()
    assert scene.run(5.0, two(SILENT, talking), vad=False).selected_id == 1


def test_selected_survives_a_brief_occlusion_and_is_dropped_when_gone() -> None:
    scene = engaged_pair()
    right_only = lambda i: [face(RIGHT_X)]  # noqa: E731
    assert scene.run(0.5, right_only).selected_id == 1  # hidden for 0.5 s
    state = scene.run(0.5, two(SILENT, SILENT))
    assert state.selected_id == 1 and state.engaged  # back: same id, facing timer kept
    state = scene.run(1.5, right_only)  # now gone for good (> max_age_ms)
    assert state.selected_id == 2  # the other person has been facing all along
    assert state.engaged


def test_engaged_is_false_while_the_selected_face_is_hidden() -> None:
    scene = Scene()
    assert scene.run(2.0, lambda i: [face(LEFT_X)]).engaged
    state = scene.run(0.3, lambda i: [])
    assert state.selected_id == 1 and not state.engaged and state.faces == ()


# ---------------- assumed VAD (--assume-speaking) ----------------
def test_assumed_vad_follows_mouth_motion_with_a_hangover() -> None:
    scene = Scene(VisionLogic(TH, assume_vad=True))
    assert not scene.run(1.0, lambda i: [face(LEFT_X, SILENT)]).vad_speaking
    state = scene.run(1.5, lambda i: [face(LEFT_X, talking(i))])
    assert state.vad_speaking and state.active_speaker_id == 1
    state = scene.run(2.0, lambda i: [face(LEFT_X, SILENT)])
    assert not state.vad_speaking


def test_assumed_vad_unit() -> None:
    vad = AssumedVad(TH)
    assert vad.update([], 0) is False
