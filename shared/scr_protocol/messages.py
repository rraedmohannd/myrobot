"""Robot <-> backend WebSocket messages (protocol version 2), as pydantic v2 models.

This file is the single source of truth: the JSON Schema files in `shared/protocol/` and the
message tables in `docs/PROTOCOL.md` are generated from it (`python -m scr_protocol.generate`),
and tests fail when they drift apart.

Conventions
- Every message is a JSON object with a `type`.
- Robot -> backend models ignore unknown fields, so a newer robot can talk to an older backend.
- Backend -> robot models forbid unknown fields, so the backend cannot send something that is not
  documented here. The backend adds `id` (12 hex chars) and `ts` (Unix ms) to every message.
- The class docstring is the "meaning" column of the documentation table.

Safety (CLAUDE.md rule 4): nothing in this protocol is needed for the robot to stop. Every
`robot.command` is a request; the robot and the ESP32 decide locally and may refuse.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)

Lang = Literal["ar", "en"]
DialogueStateName = Literal[
    "IDLE", "GREETING", "LISTENING", "ANSWERING", "EXPLAINING", "FOLLOW_UP", "CLOSING"
]
DisplayMode = Literal[
    "idle", "attentive", "listening", "processing", "answering", "finished", "unavailable"
]
RobotState = Literal["IDLE", "ROAMING", "ENGAGED", "CONVERSATION", "RETURNING", "FAULT", "PAUSED"]
CommandName = Literal["start_roaming", "pause", "resume", "return_to_start", "set_mode"]
COMMANDS: tuple[str, ...] = ("start_roaming", "pause", "resume", "return_to_start", "set_mode")
ROBOT_STATES: tuple[str, ...] = (
    "IDLE", "ROAMING", "ENGAGED", "CONVERSATION", "RETURNING", "FAULT", "PAUSED",
)  # fmt: skip


class RobotMessage(BaseModel):
    """Base of robot -> backend messages."""

    model_config = ConfigDict(extra="ignore")


class BackendMessage(BaseModel):
    """Base of backend -> robot messages."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="message id, 12 hex chars (added on send)")
    ts: int | None = Field(default=None, description="Unix time in ms (added on send)")


# ====================================================================================
# Robot -> backend
# ====================================================================================
class Hello(RobotMessage):
    """First message after connecting. `protocol_version` 1 (older clients, `/voice`) and 2 are
    accepted; version 2 clients may receive `robot.command`. `audio_stream: ["pcm16"]` = this
    client can play answer speech sent as PCM chunks."""

    type: Literal["hello"] = "hello"
    robot_id: str | None = Field(default=None, max_length=64)
    fw_version: str | None = Field(default=None, max_length=64)
    protocol_version: int = 1
    audio_stream: list[str] = Field(default_factory=list)


class Heartbeat(RobotMessage):
    """Every 5 s. The backend answers `heartbeat` at once, also while it is preparing an
    answer."""

    type: Literal["heartbeat"] = "heartbeat"


class SessionStart(RobotMessage):
    """A visitor is engaging (face detected, or the start button). The backend greets."""

    type: Literal["session.start"] = "session.start"
    lang: Lang | None = None


class AudioChunk(RobotMessage):
    """Mono PCM16 little-endian, about 100 ms per chunk, sent only while the visitor speaks
    (VAD)."""

    type: Literal["audio.chunk"] = "audio.chunk"
    pcm16_b64: str
    sample_rate: int = 16000
    seq: int | None = None


class AudioEnd(RobotMessage):
    """VAD detected the end of speech (`vad_silence_ms` of silence, from `hello.ack`). The
    camera adds the track id of the face whose lips moved and `overlap: true` when two or more
    faces were talking (docs/SPEAKERS.md). No image or landmark is ever sent."""

    type: Literal["audio.end"] = "audio.end"
    face_track_id: int | None = None
    overlap: bool | None = None


class TextInput(RobotMessage):
    """A typed question (testing, or the keyboard fallback). Starts a conversation without the
    greeting if none is active."""

    type: Literal["text.input"] = "text.input"
    text: str


class ListenTimeout(RobotMessage):
    """Nobody spoke for `follow_up_timeout_s` after the robot finished talking. During the pause
    of an explainer this means "go on"; otherwise the robot says goodbye."""

    type: Literal["listen.timeout"] = "listen.timeout"


class SessionEnd(RobotMessage):
    """The visitor left or the robot ended the conversation."""

    type: Literal["session.end"] = "session.end"
    reason: Literal["user_done", "lost_face", "timeout"] | None = None


class ExplainerStartRequest(RobotMessage):
    """A program button was tapped: play that program's approved explainer. Answer: the
    explainer messages, or `error` `no_explainer`."""

    type: Literal["explainer.start"] = "explainer.start"
    program: str = Field(description="program slug")
    lang: Lang | None = None


class ExplainerContinue(RobotMessage):
    """The "continue" button during the pause after segment 3."""

    type: Literal["explainer.continue"] = "explainer.continue"


class ExplainerStop(RobotMessage):
    """The stop button: the client already stopped the audio and emptied its queue."""

    type: Literal["explainer.stop"] = "explainer.stop"


class InterestStart(RobotMessage):
    """The "help me choose" button: start the interest questions (`error` `no_interests` when no
    mapping is approved)."""

    type: Literal["interest.start"] = "interest.start"
    lang: Lang | None = None


class InterestChoice(RobotMessage):
    """An interest button was tapped (`key` from `interest.question.options`; `none` = no second
    interest)."""

    type: Literal["interest.choice"] = "interest.choice"
    key: str


class RunStart(RobotMessage):
    """A robot run begins (roaming, return-to-start); the backend answers `run.started`.
    `is_simulated` is required: every run must say whether it is a simulation."""

    type: Literal["run.start"] = "run.start"
    is_simulated: bool
    odom_config: dict[str, Any] | None = None
    notes: str | None = None


class PoseSampleMessage(BaseModel):
    """One pose sample: `t_ms` since run start, pose in the start frame (docs/ODOMETRY.md)."""

    model_config = ConfigDict(extra="ignore")

    t_ms: int = Field(ge=0)
    x_m: float
    y_m: float
    theta_rad: float
    v_mps: float | None = None
    omega_rps: float | None = None
    enc: dict[str, Any] | None = None
    imu: dict[str, Any] | None = None
    state: str | None = Field(default=None, max_length=32)


class Telemetry(RobotMessage):
    """Batched pose samples (at most 1000 per message; about 10 Hz is enough for the plot)."""

    type: Literal["telemetry"] = "telemetry"
    samples: list[PoseSampleMessage]


class RunEvent(RobotMessage):
    """Something happened during the run: `obstacle`, `return_start`, `return_blocked`,
    `return_done`, `return_failed`, `estop`, `comm_loss`, `slip`, ..."""

    type: Literal["event"] = "event"
    t_ms: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=32)
    data: dict[str, Any] | None = None


class RunEnd(RobotMessage):
    """The run is finished; the backend answers `run.ended`."""

    type: Literal["run.end"] = "run.end"


class Pose(BaseModel):
    """Pose estimate in the start frame of the current run."""

    model_config = ConfigDict(extra="ignore")

    x_m: float
    y_m: float
    theta_rad: float


class RobotStatus(RobotMessage):
    """Live status, about once per second (protocol 2). `sensors` maps a sensor name (encoders,
    imu, ultrasonic, camera, microphone, ...) to `ok` / `degraded` / `fault` / `absent`.
    `is_simulated` is required: a simulator must say so, and the admin shows a SIMULATED label.
    The backend only displays this; it never drives the robot from it."""

    type: Literal["robot.status"] = "robot.status"
    state: RobotState
    is_simulated: bool
    battery_v: float | None = Field(default=None, ge=0, le=60)
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    faults: list[str] = Field(default_factory=list)
    sensors: dict[str, Literal["ok", "degraded", "fault", "absent"]] = Field(default_factory=dict)
    pose: Pose | None = None
    run_id: int | None = None
    mode: str | None = Field(default=None, max_length=32)


class RobotCommandAck(RobotMessage):
    """The robot's answer to `robot.command` with the same `id`. `accepted: false` is a normal
    answer: the robot refuses while it is in a conversation, in a fault state, when the battery
    is low, or whenever its own safety rules say so. `reason` says why."""

    type: Literal["robot.command.ack"] = "robot.command.ack"
    id: str = Field(min_length=1, max_length=64)
    accepted: bool
    reason: str | None = Field(default=None, max_length=200)


# ====================================================================================
# Backend -> robot
# ====================================================================================
class HelloAck(BackendMessage):
    """Settings for the client. `protocol_version` is the version the backend will speak on this
    connection (the lower of the client's and its own). `speech_rate` is a playback rate for all
    robot speech (pitch preserved); `vad_silence_ms` is the silence that ends an utterance;
    `audio_stream` says whether answers will arrive as PCM chunks."""

    type: Literal["hello.ack"] = "hello.ack"
    protocol_version: int
    follow_up_timeout_s: int
    max_utterance_s: int
    speech_rate: float
    vad_silence_ms: int
    audio_stream: bool


class HeartbeatReply(BackendMessage):
    """Reply to `heartbeat`."""

    type: Literal["heartbeat"] = "heartbeat"


class SessionStarted(BackendMessage):
    """A conversation began (stored in `sessions`)."""

    type: Literal["session.started"] = "session.started"
    session_id: int


class SessionEnded(BackendMessage):
    """The conversation ended."""

    type: Literal["session.ended"] = "session.ended"
    session_id: int
    reason: str


class DialogueState(BackendMessage):
    """The robot may listen only in LISTENING / FOLLOW_UP, and only after all queued audio has
    finished playing."""

    type: Literal["dialogue.state"] = "dialogue.state"
    state: DialogueStateName


class DisplayUpdate(BackendMessage):
    """Screen content. `card` (title, lines, sources, media, qr) is described in
    docs/PROTOCOL.md; `speaker_label` = the visitor being answered; `offline` is set when a
    provider is unreachable: `{qr_url, programs[]}` for the "weak internet" screen."""

    type: Literal["display.update"] = "display.update"
    mode: DisplayMode
    subtitle: str | None = None
    card: dict[str, Any] | None = None
    speaker_label: int | None = None
    offline: dict[str, Any] | None = None


class Transcript(BackendMessage):
    """What the backend heard and who said it (anonymous Speaker N)."""

    type: Literal["transcript"] = "transcript"
    text: str
    lang: str
    intent: str
    speaker: dict[str, Any] | None = None


class AnswerSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    title: str | None = None
    url: str | None = None


class AnswerText(BackendMessage):
    """The grounded answer as spoken (subtitles). It is sent when the answer is complete, so it
    can arrive after the first `answer.audio` pieces. `kind` says where the answer came from:
    `rag` (retrieved passages + language model), `faq` (an approved FAQ, no model), `pitch` (an
    approved promotional talking point), `place` (approved directions) or `offline`."""

    type: Literal["answer.text"] = "answer.text"
    text: str
    lang: str
    found: bool
    sources: list[AnswerSource]
    timings: dict[str, int]
    invented_numbers: list[str]
    kind: str = "rag"
    promotional: bool = False


class AnswerAudio(BackendMessage):
    """One piece of speech as a whole mp3; play the pieces in arrival order. `segment` is set for
    explainer segments (show the scene of that segment when this audio starts)."""

    type: Literal["answer.audio"] = "answer.audio"
    seq: int
    last: bool
    text: str
    mp3_b64: str
    segment: int | None = None


class AnswerAudioStart(BackendMessage):
    """A speech piece that is streamed while text-to-speech is still generating it
    (experimental, `TTS_STREAMING`; only for clients that sent `audio_stream` in `hello`)."""

    type: Literal["answer.audio.start"] = "answer.audio.start"
    seq: int
    text: str
    format: Literal["pcm16"]
    sample_rate: int


class AnswerAudioChunk(BackendMessage):
    """Mono PCM16 little-endian, about 100 ms, whole samples."""

    type: Literal["answer.audio.chunk"] = "answer.audio.chunk"
    seq: int
    pcm16_b64: str


class AnswerAudioEnd(BackendMessage):
    """End of a streamed piece (always sent after `answer.audio.start`, also after an error)."""

    type: Literal["answer.audio.end"] = "answer.audio.end"
    seq: int
    last: bool


class TurnTimings(BackendMessage):
    """Latency of the turn for the report: `ack_ms` = end of speech to the first audio of any
    kind (filler); `tts_ms` = `total_ms` = end of speech to the first audio of the answer;
    `llm_first_ms` = LLM request to the first speakable piece."""

    type: Literal["turn.timings"] = "turn.timings"
    stt_ms: int | None = None
    retrieval_ms: int | None = None
    llm_ms: int | None = None
    llm_first_ms: int | None = None
    ack_ms: int | None = None
    tts_ms: int | None = None
    total_ms: int | None = None


class ProgramRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str


class ExplainerStart(BackendMessage):
    """An approved program explainer begins (state EXPLAINING)."""

    type: Literal["explainer.start"] = "explainer.start"
    program: ProgramRef
    lang: str
    total: int


class ExplainerSegment(BackendMessage):
    """The scene of one segment, sent immediately before the `answer.audio` of that segment
    (`segment: index`); show it when that audio starts. Scene types are in docs/PROTOCOL.md."""

    type: Literal["explainer.segment"] = "explainer.segment"
    index: int
    total: int
    scene: dict[str, Any]


class ExplainerPause(BackendMessage):
    """Sent after segment 3 when at least two more follow; the next audio asks whether to go on
    and the state becomes FOLLOW_UP."""

    type: Literal["explainer.pause"] = "explainer.pause"
    next_index: int
    total: int


class ExplainerEnd(BackendMessage):
    """The presentation is over; later audio belongs to the normal conversation."""

    type: Literal["explainer.end"] = "explainer.end"
    reason: Literal["finished", "stopped", "question"]


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str


class InterestQuestion(BackendMessage):
    """"What do you like?": the screen shows the options as buttons; the visitor may also answer
    by voice."""

    type: Literal["interest.question"] = "interest.question"
    step: int
    text: str
    options: list[Option]


class SuggestedProgram(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    line: str | None = None
    has_explainer: bool


class InterestResult(BackendMessage):
    """One or two suggested programs (approved mapping only; may be empty) and what the robot
    says; programs with `has_explainer` are tappable."""

    type: Literal["interest.result"] = "interest.result"
    programs: list[SuggestedProgram]
    text: str


class RunStarted(BackendMessage):
    """Answer to `run.start` (stored in `robot_runs`)."""

    type: Literal["run.started"] = "run.started"
    run_id: int
    is_simulated: bool


class RunEnded(BackendMessage):
    """Answer to `run.end`: how many samples and events were stored."""

    type: Literal["run.ended"] = "run.ended"
    run_id: int
    samples: int
    events: int


class RobotCommand(BackendMessage):
    """A REQUEST from the admin page (protocol 2 only). `id` is the command id; the robot
    answers `robot.command.ack` with the same id. `args` is used by `set_mode`
    (`{"mode": "..."}`). The robot decides locally whether and when to obey, and it must stay
    safe if this message never arrives."""

    type: Literal["robot.command"] = "robot.command"
    id: str = Field(min_length=1, max_length=64)  # the command id (not replaced on send)
    cmd: CommandName
    args: dict[str, Any] | None = None


class ErrorMessage(BackendMessage):
    """`stt_failed`, `tts_failed`, `unknown_type`, `bad_message`, `no_run` (telemetry before
    `run.start`), `no_explainer`, `no_interests`, `internal`."""

    type: Literal["error"] = "error"
    code: str
    message: str


ROBOT_TO_BACKEND: tuple[type[RobotMessage], ...] = (
    Hello, Heartbeat, SessionStart, AudioChunk, AudioEnd, TextInput, ListenTimeout, SessionEnd,
    ExplainerStartRequest, ExplainerContinue, ExplainerStop, InterestStart, InterestChoice,
    RunStart, Telemetry, RunEvent, RunEnd, RobotStatus, RobotCommandAck,
)  # fmt: skip
BACKEND_TO_ROBOT: tuple[type[BackendMessage], ...] = (
    HelloAck, HeartbeatReply, SessionStarted, SessionEnded, DialogueState, DisplayUpdate,
    Transcript, AnswerText, AnswerAudio, AnswerAudioStart, AnswerAudioChunk, AnswerAudioEnd,
    TurnTimings, ExplainerStart, ExplainerSegment, ExplainerPause, ExplainerEnd,
    InterestQuestion, InterestResult, RunStarted, RunEnded, RobotCommand, ErrorMessage,
)  # fmt: skip

RobotToBackend = Annotated[
    Hello | Heartbeat | SessionStart | AudioChunk | AudioEnd | TextInput | ListenTimeout
    | SessionEnd | ExplainerStartRequest | ExplainerContinue | ExplainerStop | InterestStart
    | InterestChoice | RunStart | Telemetry | RunEvent | RunEnd | RobotStatus | RobotCommandAck,
    Field(discriminator="type"),
]
BackendToRobot = Annotated[
    HelloAck | HeartbeatReply | SessionStarted | SessionEnded | DialogueState | DisplayUpdate
    | Transcript | AnswerText | AnswerAudio | AnswerAudioStart | AnswerAudioChunk | AnswerAudioEnd
    | TurnTimings | ExplainerStart | ExplainerSegment | ExplainerPause | ExplainerEnd
    | InterestQuestion | InterestResult | RunStarted | RunEnded | RobotCommand | ErrorMessage,
    Field(discriminator="type"),
]


def message_type(model: type[BaseModel]) -> str:
    return model.model_fields["type"].default
