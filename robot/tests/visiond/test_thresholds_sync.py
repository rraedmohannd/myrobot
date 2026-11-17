"""shared/vision_thresholds.json must equal the constants in the browser prototype.

The prototype (backend/app/static/speaker-camera.js) keeps its numbers in the code. This test
reads them from the JS text and fails when the JSON, the JS or visiond's defaults drift apart.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields

import pytest

from visiond.config import (
    DEFAULT_LANDMARKS,
    DEFAULT_THRESHOLDS_PATH,
    REPO_DIR,
    Thresholds,
    load_landmark_indices,
    load_thresholds,
)

JS_PATH = REPO_DIR / "backend" / "app" / "static" / "speaker-camera.js"

# JSON key -> regular expressions that must each find the value in the JS source.
JS_PATTERNS: dict[str, list[str]] = {
    "iou_min": [r"iouMin:\s*([\d.]+)"],
    "max_age_ms": [r"maxAgeMs:\s*([\d.]+)"],
    "window_ms": [r"windowMs:\s*([\d.]+)"],
    # once as the SpeechObserver default and once as the SpeakerCamera default
    "activity_min": [
        r"Object\.assign\(\{\s*activityMin:\s*([\d.]+),\s*overlapShare",
        r"Object\.assign\(\{\s*activityMin:\s*([\d.]+),\s*faceStart",
    ],
    "overlap_share": [r"overlapShare:\s*([\d.]+)"],
    "engage_ms": [r"faceStartMs:\s*([\d.]+)"],
    "rearm_no_face_ms": [r"now - this\.noFaceSince >\s*([\d.]+)"],
    "looking_pos_min": [r"return pos >\s*([\d.]+)"],
    "looking_pos_max": [r"&& pos <\s*([\d.]+)"],
    "max_faces": [r"maxFaces:\s*([\d.]+)"],
}


def js_constants(source: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for key, patterns in JS_PATTERNS.items():
        values = set()
        for pattern in patterns:
            match = re.search(pattern, source)
            assert match, f"speaker-camera.js: constant for '{key}' not found ({pattern})"
            values.add(float(match.group(1)))
        assert len(values) == 1, f"speaker-camera.js uses different values for '{key}': {values}"
        found[key] = values.pop()
    return found


def js_landmarks(source: str) -> dict[str, int]:
    match = re.search(r"const LM = \{([^}]*)\}", source)
    assert match, "speaker-camera.js: the LM landmark table was not found"
    return {name: int(value) for name, value in re.findall(r"(\w+):\s*(\d+)", match.group(1))}


def mismatches(source: str, prototype: dict) -> list[str]:
    problems = []
    for key, value in js_constants(source).items():
        if prototype.get(key) != pytest.approx(value):
            problems.append(f"{key}: JS {value} != JSON {prototype.get(key)}")
    if js_landmarks(source) != prototype.get("landmarks"):
        problems.append("landmarks differ")
    return problems


@pytest.fixture(scope="module")
def js_source() -> str:
    return JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def shared() -> dict:
    return json.loads(DEFAULT_THRESHOLDS_PATH.read_text(encoding="utf-8"))


def test_json_matches_the_js_prototype(js_source: str, shared: dict) -> None:
    assert mismatches(js_source, shared["prototype"]) == []


def test_every_prototype_key_is_checked_against_the_js(shared: dict) -> None:
    assert set(shared["prototype"]) == set(JS_PATTERNS) | {"landmarks"}


def test_the_check_notices_drift(js_source: str, shared: dict) -> None:
    changed = js_source.replace("iouMin: 0.25", "iouMin: 0.35")
    assert changed != js_source
    assert mismatches(changed, shared["prototype"]) == ["iou_min: JS 0.35 != JSON 0.25"]
    drifted = dict(shared["prototype"], engage_ms=2000)
    assert mismatches(js_source, drifted) == ["engage_ms: JS 1500.0 != JSON 2000"]


def test_visiond_defaults_equal_the_json(shared: dict) -> None:
    """Built-in defaults (used if the JSON is missing) must not drift either."""
    defaults = Thresholds()
    merged = {**shared["prototype"], **shared["visiond"]}
    merged.pop("landmarks")
    assert set(merged) == {f.name for f in fields(Thresholds)}
    for key, value in merged.items():
        assert getattr(defaults, key) == pytest.approx(value), key
    assert load_thresholds() == defaults
    assert shared["prototype"]["landmarks"] == DEFAULT_LANDMARKS == load_landmark_indices()


def test_json_overrides_are_loaded(tmp_path) -> None:
    custom = tmp_path / "t.json"
    custom.write_text(
        json.dumps({"prototype": {"engage_ms": 900}, "visiond": {"yaw_max_deg": 12}}),
        encoding="utf-8",
    )
    loaded = load_thresholds(custom)
    assert loaded.engage_ms == 900 and loaded.yaw_max_deg == 12
    assert loaded.iou_min == Thresholds().iou_min
    assert load_thresholds(tmp_path / "missing.json") == Thresholds()
