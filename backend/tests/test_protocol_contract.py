"""Sprint 3 S1: the shared protocol contract (`shared/scr_protocol`, protocol version 2).

- The JSON Schema files and the tables of docs/PROTOCOL.md are generated from the models and
  must be up to date.
- Every documented robot message is accepted by the backend.
- Every message the backend sends validates against the schema. In the test environment the
  send function of every connection is wrapped with this check, so ALL tests enforce it; here
  one long scenario is validated a second time with an independent JSON Schema validator.
"""

import json

import jsonschema
import pytest
import scr_protocol
from scr_protocol import generate, messages, negotiate_version, parse_robot_message
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.ws.contract import ContractViolation, checked
from app.db.models import Chunk, Explainer, ExplainerSegment, Faculty, Pitch, Program, Source
from app.services.dialogue import DialogueManager
from app.services.robot_hub import RobotHub
from app.telemetry.store import TelemetryRecorder
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

PCM = "AQABAAEAAQA="
ROBOT_EXAMPLES: dict[str, dict] = {
    "hello": {
        "type": "hello",
        "robot_id": "pi-1",
        "fw_version": "0.1",
        "protocol_version": 2,
        "audio_stream": ["pcm16"],
    },
    "heartbeat": {"type": "heartbeat"},
    "session.start": {"type": "session.start", "lang": "ar"},
    "audio.chunk": {"type": "audio.chunk", "pcm16_b64": PCM, "sample_rate": 16000, "seq": 1},
    "audio.end": {"type": "audio.end", "face_track_id": 3, "overlap": False},
    "text.input": {"type": "text.input", "text": "مرحبا"},
    "listen.timeout": {"type": "listen.timeout"},
    "session.end": {"type": "session.end", "reason": "user_done"},
    "explainer.start": {"type": "explainer.start", "program": "no-such-program", "lang": "ar"},
    "explainer.continue": {"type": "explainer.continue"},
    "explainer.stop": {"type": "explainer.stop"},
    "interest.start": {"type": "interest.start", "lang": "ar"},
    "interest.choice": {"type": "interest.choice", "key": "computers_ai"},
    "run.start": {
        "type": "run.start",
        "is_simulated": True,
        "odom_config": {"track_width_m": 0.19},
    },
    "telemetry": {
        "type": "telemetry",
        "samples": [
            {"t_ms": 0, "x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0, "v_mps": 0.2, "state": "roam"}
        ],
    },
    "event": {"type": "event", "t_ms": 100, "kind": "obstacle", "data": {"range_m": 0.4}},
    "run.end": {"type": "run.end"},
    "robot.status": {
        "type": "robot.status",
        "state": "ROAMING",
        "is_simulated": True,
        "battery_v": 7.8,
        "battery_pct": 70,
        "faults": [],
        "sensors": {"imu": "ok", "camera": "absent"},
        "pose": {"x_m": 1.0, "y_m": 2.0, "theta_rad": 0.5},
        "run_id": 1,
        "mode": "demo",
    },
    "robot.command.ack": {
        "type": "robot.command.ack",
        "id": "abc123",
        "accepted": False,
        "reason": "a conversation is in progress",
    },
}


def _schema(direction: str) -> dict:
    return json.loads(
        (generate.SCHEMA_DIR / f"{direction}.schema.json").read_text(encoding="utf-8")
    )


# ---------------- generated files ----------------
def test_schema_files_are_generated_from_the_models() -> None:
    for path, content in generate.schema_files().items():
        assert path.exists(), f"run: python -m scr_protocol.generate ({path.name} is missing)"
        assert path.read_text(encoding="utf-8") == content, f"{path.name} is out of date"
    for direction in ("robot_to_backend", "backend_to_robot"):
        jsonschema.Draft202012Validator.check_schema(_schema(direction))


def test_protocol_doc_tables_match_the_models() -> None:
    assert generate.DOC.exists(), (
        "docs/PROTOCOL.md is not visible to the tests (docker-compose mounts ./docs)"
    )
    text = generate.DOC.read_text(encoding="utf-8")
    assert generate.render_doc(text) == text, (
        "run: python -m scr_protocol.generate (tables are out of date)"
    )
    for model in (*scr_protocol.ROBOT_TO_BACKEND, *scr_protocol.BACKEND_TO_ROBOT):
        assert f"| `{scr_protocol.message_type(model)}` |" in text
        assert (model.__doc__ or "").strip(), (
            f"{model.__name__} needs a docstring (the doc meaning)"
        )


def test_every_message_type_has_one_model_per_direction() -> None:
    robot = [scr_protocol.message_type(m) for m in scr_protocol.ROBOT_TO_BACKEND]
    backend = [scr_protocol.message_type(m) for m in scr_protocol.BACKEND_TO_ROBOT]
    assert len(robot) == len(set(robot)) and len(backend) == len(set(backend))
    assert set(ROBOT_EXAMPLES) == set(robot)  # an example exists for every documented robot message
    assert {"robot.status", "robot.command.ack"} <= set(robot) and "robot.command" in backend
    assert scr_protocol.PROTOCOL_VERSION == 2 and scr_protocol.SUPPORTED_VERSIONS == (1, 2)


# ---------------- robot -> backend ----------------
def test_every_documented_robot_message_validates() -> None:
    validator = jsonschema.Draft202012Validator(_schema("robot_to_backend"))
    for kind, example in ROBOT_EXAMPLES.items():
        assert parse_robot_message(example).type == kind
        validator.validate(example)
        assert (
            parse_robot_message({**example, "future_field": 1}).type == kind
        )  # newer robot, older backend
    with pytest.raises(scr_protocol.ProtocolError):
        parse_robot_message({"type": "robot.status", "state": "IDLE"})  # is_simulated is required
    with pytest.raises(scr_protocol.ProtocolError):
        parse_robot_message({"type": "run.start"})
    with pytest.raises(scr_protocol.ProtocolError):
        parse_robot_message({"type": "launch.rocket"})


def test_backend_accepts_every_documented_robot_message() -> None:
    """Over the real WebSocket: no message of the contract is answered with unknown_type or
    bad_message. (Replies that depend on data, such as no_explainer, are fine.)"""
    from app.db.session import get_sessionmaker
    from app.main import app
    from app.services.providers import get_embedder, get_llm
    from app.services.robot_hub import get_robot_hub
    from app.services.speech import get_stt, get_tts
    from tests.test_robot_control import _NullSession

    class Rows(list):
        def all(self):  # noqa: ANN202
            return []

    class Null(_NullSession):
        """Accepts any session call: reads find nothing, writes get id 1."""

        def add_all(self, rows: list) -> None:
            self.added.extend(rows)

        async def get(self, *a: object, **k: object):  # noqa: ANN201
            return None

        async def scalar(self, *a: object, **k: object):  # noqa: ANN201
            return None

        async def commit(self) -> None:
            for row in self.added:
                if getattr(row, "id", None) is None:
                    row.id = 1
                if hasattr(row, "is_simulated") and row.is_simulated is None:
                    row.is_simulated = True

        def __getattr__(self, name: str):  # noqa: ANN204 - scalars, execute, refresh, flush, delete...
            async def anything(*a: object, **k: object):  # noqa: ANN202
                return Rows()

            return anything

    overrides = {
        get_sessionmaker: lambda: Null(), get_robot_hub: lambda: RobotHub(),
        get_embedder: lambda: FakeEmbedder(), get_llm: lambda: FakeLLM(),
        get_stt: lambda: FakeSTT(["مرحبا"]), get_tts: lambda: FakeTTS(),
    }  # fmt: skip
    app.dependency_overrides.update(overrides)
    order = [
        "hello",
        "heartbeat",
        "robot.status",
        "robot.command.ack",
        "run.start",
        "telemetry",
        "event",
        "run.end",
        "session.start",
        "audio.chunk",
        "audio.end",
        "listen.timeout",
        "explainer.start",
        "explainer.continue",
        "explainer.stop",
        "interest.start",
        "interest.choice",
        "session.end",
    ]
    assert set(order) | {"text.input"} == set(ROBOT_EXAMPLES)
    try:
        with TestClient(app) as tc, tc.websocket_connect("/ws/robot") as ws:
            for kind in order:
                ws.send_json(ROBOT_EXAMPLES[kind])
            ws.send_json({"type": "nonsense.marker"})  # the worker answers this one last
            replies = []
            while True:
                reply = ws.receive_json()
                replies.append(reply)
                if reply["type"] == "error" and "nonsense.marker" in reply["message"]:
                    break
    finally:
        for key in overrides:
            app.dependency_overrides.pop(key, None)
    rejected = [
        r
        for r in replies[:-1]
        if r["type"] == "error" and r["code"] in ("unknown_type", "bad_message", "internal")
    ]
    assert rejected == []
    validator = jsonschema.Draft202012Validator(_schema("backend_to_robot"))
    for reply in replies:
        validator.validate(reply)
    assert {"hello.ack", "heartbeat", "run.started", "run.ended", "session.started"} <= {
        r["type"] for r in replies
    }


def test_version_negotiation_keeps_version_1_clients() -> None:
    assert [negotiate_version(v) for v in (None, 1, 2, 3, "2", "x", 0)] == [1, 1, 2, 2, 2, 1, 1]


async def test_hello_ack_uses_the_negotiated_version(db_session) -> None:
    for client_version, spoken in ((None, 1), (1, 1), (2, 2), (7, 2)):
        sent: list[dict] = []

        async def send(msg: dict, sent: list = sent) -> None:
            sent.append(msg)

        dm = DialogueManager(
            send, OneSession(db_session), FakeEmbedder(), FakeLLM(), FakeSTT([]), FakeTTS()
        )
        hello = {"type": "hello", "robot_id": "t"}
        if client_version is not None:
            hello["protocol_version"] = client_version
        await dm.handle(hello)
        assert sent[-1]["protocol_version"] == spoken and dm.protocol_version == spoken


# ---------------- backend -> robot ----------------
async def test_undocumented_backend_messages_are_caught() -> None:
    async def send(msg: dict) -> None:
        return None

    strict = checked(send)
    await strict({"type": "dialogue.state", "state": "LISTENING"})
    with pytest.raises(ContractViolation):
        await strict({"type": "dialogue.state", "state": "DANCING"})
    with pytest.raises(ContractViolation):
        await strict(
            {
                "type": "answer.audio",
                "seq": 0,
                "last": True,
                "text": "x",
                "mp3_b64": "",
                "volume": 11,
            }
        )
    with pytest.raises(ContractViolation):
        await strict({"type": "secret.message"})


async def test_a_long_conversation_only_sends_contract_messages(db_session) -> None:
    """Greeting, question, explainer with pause, interest flow, telemetry, goodbye: every message
    is validated against the generated JSON Schema, and most message types are exercised."""
    faculty = await db_session.scalar(select(Faculty).limit(1))
    program = Program(
        faculty_id=faculty.id,
        slug="contract-program",
        name_ar="برنامج العقد",
        name_en="Contract Program",
    )
    source = Source(
        url="https://example.test/contract", title="Contract source", review_status="approved"
    )
    db_session.add_all([program, source])
    await db_session.flush()
    db_session.add_all(
        [
            Chunk(
                source_id=source.id,
                program_id=program.id,
                lang="en",
                category="study_plan",
                review_status="approved",
                text="The qontractix program requires 162 credit hours.",
            ),
            Pitch(
                program_id=program.id,
                topic="interest:computers_ai",
                text_ar="سطر",
                review_status="approved",
            ),
        ]
    )
    explainer = Explainer(program_id=program.id, lang="ar", status="approved")
    db_session.add(explainer)
    await db_session.flush()
    for i, (text, scene) in enumerate(
        [
            ("تعريف.", {"type": "title"}),
            ("162 ساعة.", {"type": "stat", "value": "162", "unit": "ساعة"}),
            ("نقاط.", {"type": "keypoints", "points": ["أ", "ب"]}),
            ("مشروع.", {"type": "keypoints", "points": ["ج"]}),
            ("صوّر الكود.", {"type": "qr"}),
        ],
        start=1,
    ):
        db_session.add(
            ExplainerSegment(explainer_id=explainer.id, position=i, text=text, scene=scene)
        )
    await db_session.commit()

    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    maker = OneSession(db_session)
    dm = DialogueManager(
        send, maker, FakeEmbedder(), FakeLLM(), FakeSTT(["qontractix؟", ""]), FakeTTS()
    )
    recorder = TelemetryRecorder(send, maker)
    await dm.handle({"type": "hello", "protocol_version": 2})
    await dm.handle({"type": "heartbeat"})
    await recorder.handle(ROBOT_EXAMPLES["run.start"])
    await recorder.handle(ROBOT_EXAMPLES["telemetry"])
    await recorder.handle(ROBOT_EXAMPLES["run.end"])
    await recorder.handle(ROBOT_EXAMPLES["telemetry"])  # no run: error no_run
    await dm.handle({"type": "session.start"})
    for _ in range(2):  # a question, then an utterance that is not understood
        await dm.handle(ROBOT_EXAMPLES["audio.chunk"] | {"pcm16_b64": "AQAB" * 4000})
        await dm.handle({"type": "audio.end"})
    await dm.handle({"type": "explainer.start", "program": "contract-program", "lang": "ar"})
    await dm.handle({"type": "explainer.continue"})
    await dm.handle({"type": "interest.start"})
    await dm.handle({"type": "interest.choice", "key": "computers_ai"})
    await dm.handle({"type": "interest.choice", "key": "none"})
    await dm.handle({"type": "explainer.stop"})
    await dm.handle({"type": "nonsense"})
    await dm.handle({"type": "text.input", "text": "شكرا"})

    validator = jsonschema.Draft202012Validator(_schema("backend_to_robot"))
    for msg in sent:
        validator.validate(msg)
        scr_protocol.parse_backend_message(msg)
    seen = {m["type"] for m in sent}
    expected = scr_protocol.BACKEND_TYPES - {
        "robot.command",
        "answer.audio.start",
        "answer.audio.chunk",
        "answer.audio.end",  # own tests
    }
    assert expected <= seen, sorted(expected - seen)
    assert isinstance(
        messages.RobotCommand(id="c1", cmd="pause").model_dump(exclude_none=True), dict
    )
