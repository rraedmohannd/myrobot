# Robot ↔ backend protocol (v2)

Transport: WebSocket `ws://<laptop>:8000/ws/robot` (add `?token=...` if `ROBOT_TOKEN` is set and the robot is not on the backend's own machine, see docs/DEPLOY.md).
Every message is a JSON object with `type`. Backend messages also carry `id` (12 hex chars) and `ts` (Unix ms).

**The contract is code:** `shared/scr_protocol/messages.py` (pydantic models) is the single source of truth. The JSON Schema files in `shared/protocol/` and the two message tables below are **generated** from it (`python -m scr_protocol.generate`), and the tests fail when they differ or when the backend sends anything that is not in the contract. The Raspberry Pi installs the same package (`pip install ./shared`).

**Versions.** `hello.protocol_version` 1 and 2 are accepted. Version 1 (sprints 1 and 2: conversation, telemetry, explainers, interests) is what `/voice` speaks. Version 2 adds the live robot status and command requests; the backend sends `robot.command` only to version 2 connections. `hello.ack.protocol_version` is the version used on the connection.

**Two lanes per connection.** Conversation and telemetry messages are processed in order, one at a time. `heartbeat`, `robot.status` and `robot.command.ack` are handled at once, also while the backend is preparing an answer.

The reference client is `backend/app/static/robot-client.js`, shared by the browser pages `/voice` (test bench) and `/robot` (the robot's screen, standing in for the 7" HDMI display).

**Safety:** nothing here is needed to stop the robot. Motor stop, obstacles and command timeouts stay on the ESP32 and the robot (CLAUDE.md rule 4). Every `robot.command` is a **request**: the robot decides locally, may refuse, and must behave safely if the backend disappears. If the backend stops answering heartbeats, the robot shows its "service unavailable" face and keeps working locally.

## Robot → backend
<!-- BEGIN GENERATED robot-to-backend -->
| type | fields | meaning |
|---|---|---|
| `hello` | `robot_id?`, `fw_version?`, `protocol_version?`, `audio_stream?` | First message after connecting. `protocol_version` 1 (older clients, `/voice`) and 2 are accepted; version 2 clients may receive `robot.command`. `audio_stream: ["pcm16"]` = this client can play answer speech sent as PCM chunks. |
| `heartbeat` | – | Every 5 s. The backend answers `heartbeat` at once, also while it is preparing an answer. |
| `session.start` | `lang?` | A visitor is engaging (face detected, or the start button). The backend greets. |
| `audio.chunk` | `pcm16_b64`, `sample_rate?`, `seq?` | Mono PCM16 little-endian, about 100 ms per chunk, sent only while the visitor speaks (VAD). |
| `audio.end` | `face_track_id?`, `overlap?` | VAD detected the end of speech (`vad_silence_ms` of silence, from `hello.ack`). The camera adds the track id of the face whose lips moved and `overlap: true` when two or more faces were talking (docs/SPEAKERS.md). No image or landmark is ever sent. |
| `text.input` | `text` | A typed question (testing, or the keyboard fallback). Starts a conversation without the greeting if none is active. |
| `listen.timeout` | – | Nobody spoke for `follow_up_timeout_s` after the robot finished talking. During the pause of an explainer this means "go on"; otherwise the robot says goodbye. |
| `session.end` | `reason?` | The visitor left or the robot ended the conversation. |
| `explainer.start` | `program`, `lang?` | A program button was tapped: play that program's approved explainer. Answer: the explainer messages, or `error` `no_explainer`. |
| `explainer.continue` | – | The "continue" button during the pause after segment 3. |
| `explainer.stop` | – | The stop button: the client already stopped the audio and emptied its queue. |
| `interest.start` | `lang?` | The "help me choose" button: start the interest questions (`error` `no_interests` when no mapping is approved). |
| `interest.choice` | `key` | An interest button was tapped (`key` from `interest.question.options`; `none` = no second interest). |
| `run.start` | `is_simulated`, `odom_config?`, `notes?` | A robot run begins (roaming, return-to-start); the backend answers `run.started`. `is_simulated` is required: every run must say whether it is a simulation. |
| `telemetry` | `samples` | Batched pose samples (at most 1000 per message; about 10 Hz is enough for the plot). |
| `event` | `t_ms`, `kind`, `data?` | Something happened during the run: `obstacle`, `return_start`, `return_blocked`, `return_done`, `return_failed`, `estop`, `comm_loss`, `slip`, ... |
| `run.end` | – | The run is finished; the backend answers `run.ended`. |
| `robot.status` | `state`, `is_simulated`, `battery_v?`, `battery_pct?`, `faults?`, `sensors?`, `pose?`, `run_id?`, `mode?` | Live status, about once per second (protocol 2). `sensors` maps a sensor name (encoders, imu, ultrasonic, camera, microphone, ...) to `ok` / `degraded` / `fault` / `absent`. `is_simulated` is required: a simulator must say so, and the admin shows a SIMULATED label. The backend only displays this; it never drives the robot from it. |
| `robot.command.ack` | `id`, `accepted`, `reason?` | The robot's answer to `robot.command` with the same `id`. `accepted: false` is a normal answer: the robot refuses while it is in a conversation, in a fault state, when the battery is low, or whenever its own safety rules say so. `reason` says why. |
<!-- END GENERATED robot-to-backend -->

## Backend → robot
<!-- BEGIN GENERATED backend-to-robot -->
| type | fields | meaning |
|---|---|---|
| `hello.ack` | `protocol_version`, `follow_up_timeout_s`, `max_utterance_s`, `speech_rate`, `vad_silence_ms`, `audio_stream` | Settings for the client. `protocol_version` is the version the backend will speak on this connection (the lower of the client's and its own). `speech_rate` is a playback rate for all robot speech (pitch preserved); `vad_silence_ms` is the silence that ends an utterance; `audio_stream` says whether answers will arrive as PCM chunks. |
| `heartbeat` | – | Reply to `heartbeat`. |
| `session.started` | `session_id` | A conversation began (stored in `sessions`). |
| `session.ended` | `session_id`, `reason` | The conversation ended. |
| `dialogue.state` | `state` | The robot may listen only in LISTENING / FOLLOW_UP, and only after all queued audio has finished playing. |
| `display.update` | `mode`, `subtitle?`, `card?`, `speaker_label?`, `offline?` | Screen content. `card` (title, lines, sources, media, qr) is described in docs/PROTOCOL.md; `speaker_label` = the visitor being answered; `offline` is set when a provider is unreachable: `{qr_url, programs[]}` for the "weak internet" screen. |
| `transcript` | `text`, `lang`, `intent`, `speaker?` | What the backend heard and who said it (anonymous Speaker N). |
| `answer.text` | `text`, `lang`, `found`, `sources`, `timings`, `invented_numbers`, `kind?`, `promotional?` | The grounded answer as spoken (subtitles). It is sent when the answer is complete, so it can arrive after the first `answer.audio` pieces. `kind` says where the answer came from: `rag` (retrieved passages + language model), `faq` (an approved FAQ, no model), `pitch` (an approved promotional talking point), `place` (approved directions) or `offline`. |
| `answer.audio` | `seq`, `last`, `text`, `mp3_b64`, `segment?` | One piece of speech as a whole mp3; play the pieces in arrival order. `segment` is set for explainer segments (show the scene of that segment when this audio starts). |
| `answer.audio.start` | `seq`, `text`, `format`, `sample_rate` | A speech piece that is streamed while text-to-speech is still generating it (experimental, `TTS_STREAMING`; only for clients that sent `audio_stream` in `hello`). |
| `answer.audio.chunk` | `seq`, `pcm16_b64` | Mono PCM16 little-endian, about 100 ms, whole samples. |
| `answer.audio.end` | `seq`, `last` | End of a streamed piece (always sent after `answer.audio.start`, also after an error). |
| `turn.timings` | `stt_ms?`, `retrieval_ms?`, `llm_ms?`, `llm_first_ms?`, `ack_ms?`, `tts_ms?`, `total_ms?` | Latency of the turn for the report: `ack_ms` = end of speech to the first audio of any kind (filler); `tts_ms` = `total_ms` = end of speech to the first audio of the answer; `llm_first_ms` = LLM request to the first speakable piece. |
| `explainer.start` | `program`, `lang`, `total` | An approved program explainer begins (state EXPLAINING). |
| `explainer.segment` | `index`, `total`, `scene` | The scene of one segment, sent immediately before the `answer.audio` of that segment (`segment: index`); show it when that audio starts. Scene types are in docs/PROTOCOL.md. |
| `explainer.pause` | `next_index`, `total` | Sent after segment 3 when at least two more follow; the next audio asks whether to go on and the state becomes FOLLOW_UP. |
| `explainer.end` | `reason` | The presentation is over; later audio belongs to the normal conversation. |
| `interest.question` | `step`, `text`, `options` | "What do you like?": the screen shows the options as buttons; the visitor may also answer by voice. |
| `interest.result` | `programs`, `text` | One or two suggested programs (approved mapping only; may be empty) and what the robot says; programs with `has_explainer` are tappable. |
| `run.started` | `run_id`, `is_simulated` | Answer to `run.start` (stored in `robot_runs`). |
| `run.ended` | `run_id`, `samples`, `events` | Answer to `run.end`: how many samples and events were stored. |
| `robot.command` | `id`, `cmd`, `args?` | A REQUEST from the admin page (protocol 2 only). `id` is the command id; the robot answers `robot.command.ack` with the same id. `args` is used by `set_mode` (`{"mode": "..."}`). The robot decides locally whether and when to obey, and it must stay safe if this message never arrives. |
| `error` | `code`, `message` | `stt_failed`, `tts_failed`, `unknown_type`, `bad_message`, `no_run` (telemetry before `run.start`), `no_explainer`, `no_interests`, `internal`. |
<!-- END GENERATED backend-to-robot -->

### Robot status and commands (version 2)
```
robot  → robot.status {state, battery_v, faults, sensors, pose, is_simulated, …}   about 1 Hz
admin  → POST /api/admin/robot/command {cmd}          (button on the admin "Robot" tab, after a confirmation)
backend→ robot.command {id, cmd, args?}               stored as event `command`
robot  → robot.command.ack {id, accepted, reason?}    stored as event `command_ack`
```
- `cmd`: `start_roaming`, `pause`, `resume`, `return_to_start`, `set_mode` (with `args.mode`).
- **A command is a request.** The robot refuses (`accepted: false` with a `reason`) whenever its own rules say so: a conversation is in progress, a fault is active, the battery is low, an obstacle blocks the way. The backend never assumes a command was carried out; the admin page shows the ack and then the state reported by `robot.status`.
- No ack within `ROBOT_COMMAND_TIMEOUT_S` (5 s) is shown as "no answer"; the backend then knows nothing about what the robot did.
- No robot connected (no `robot.status` for `ROBOT_OFFLINE_AFTER_S`, 5 s): nothing is sent and the admin shows "robot offline".
- `robot.status.is_simulated` is required. A simulator (`simulate-robot`) is shown with a **SIMULATED** label everywhere.
- An accepted `return_to_start` on a **real** run opens a return trial in the `pending` state. Its error is typed in by hand after measuring on the floor; simulated runs never get a return trial (rule 5).
- `robot.status.state`: IDLE, ROAMING, ENGAGED (a visitor is detected), CONVERSATION, RETURNING, FAULT, PAUSED. `sensors` maps a sensor name to `ok`, `degraded`, `fault` or `absent`.

### `display.update.card` (optional; sent with found answers)
| field | meaning |
|---|---|
| `title` | name of the program the answer's sources belong to, or the faculty name when they are mixed or general |
| `lines[]` | at most 3 short lines; they are sentences of the spoken answer (cut with "…" after about 90 characters) |
| `sources[]` | titles of the sources the answer used (this field existed before; older clients that only read `sources` keep working) |
| `media?` | `{kind: "image" or "video", url, alt}`: one **approved** file of the media library, sent only when the answer's sources clearly belong to one program that has approved media. `url` is a path on the backend (`/media/{id}`). The screen shows the image, or plays the video **muted and looping**, next to the subtitles. Several files of a program take turns |

`GET /media/{id}` serves a file only while its review status is `approved`; pending and rejected files answer 404. Files are uploaded and reviewed in `/admin` → Media and are stored locally in `backend/data/media/` (not in git).

The card never contains facts that are not in the grounded answer. Answers with `found=false` and error answers have no card. Clients must ignore card fields they do not know.

### Program explainers (sprint 2)
An explainer is a 60–90 second presentation of one program, written from the program's approved sources, **approved by a human** in `/admin` → Explainers, with its speech generated ahead of time (cached mp3), so it starts without any LLM wait. The robot never plays a pending or rejected explainer, and it stops playing an approved one if one of its numbers is no longer in the approved sources.

```
"احكيلي عن تخصص X" / "شو هو X" / "ليش أدرس X" / "tell me about X", or explainer.start (button)
  approved explainer in the visitor's language?  no → the normal grounded answer (RAG)
  yes → dialogue.state EXPLAINING, explainer.start
        explainer.segment 1 + answer.audio(segment 1) … segment 3
        explainer.pause + "بدك أكمل ولا عندك سؤال؟" → FOLLOW_UP
            "اه" / "كمّل" / explainer.continue / silence (listen.timeout) → segments 4 … N
            a question                                                   → explainer.end(question), normal answer
        explainer.end(finished) + "عندك سؤال ثاني؟" → FOLLOW_UP
```
With four segments or fewer there is no pause. The voice cannot interrupt (the microphone is deaf while the robot talks), so the screen has **Next** (skip the segment that is playing) and **Stop** (`explainer.stop`) buttons.

`scene` always carries `type` and `program {slug, name}`; the client picks the program's line drawing from the slug. Types and fields:

| `type` | fields | what the screen shows |
|---|---|---|
| `title` | `tagline?` | program name and its line drawing, which draws itself |
| `keypoints` | `title?`, `points[]` (2–4) | short phrases written word by word in time with the speech |
| `stat` | `value`, `unit`, `caption?` | a big number that counts up to `value` |
| `timeline` | `title?`, `steps[] {label, items[]}` | steps of the plan appearing one after the other |
| `courses` | `title?`, `courses[]` | chips with real course names from the plan |
| `diagram` | `title?`, `nodes[]` (3–4) | boxes joined by arrows |
| `media` | `caption?`, `media` (`{kind, url, alt}` or null) | an **approved** photo or video of the program (video muted, looping); without one, the drawing |
| `qr` | `caption?`, `url` (or null) | a QR code drawn locally from `url`. The backend takes `url` from the records only: `programs.study_plan_url`, else the web address of an approved study-plan source. No web address → no code is shown |

Every number in a segment or scene, and every course name on screen, must appear in the program's approved sources; the admin refuses to approve an explainer otherwise. The address of the QR code and the media never come from the drafted text.

### Interest matching (sprint 2)
```
"مش عارف شو أدرس" / "ساعدني أختار" / interest.start (button), and an approved mapping exists
  interest.question(step 1) "شو أكثر إشي بتحبه؟"   ← voice ("بحب الكمبيوتر") or interest.choice
  interest.question(step 2) "وفي إشي ثاني بتحبه؟"  ← voice, interest.choice, or "لا" / key "none"
  interest.result: 1–2 programs + "بدك أحكيلك عن X؟"  ← "اه" or explainer.start → the explainer of X
```
The interest → program mapping is curated promotional data in the `pitches` table (topic `interest:<key>`), used only when approved. No approved mapping → the request is answered like any other question. A real question at any step leaves the flow and is answered normally.

### Idle screen (`GET /api/robot/screen`, public, approved content only)
`{programs[] {slug, name_ar, name_en, langs[]}, attract[] (title and stat scenes), interests (bool)}`: the programs that have a playable explainer (touch buttons), the scenes the idle screen cycles through silently (attract mode, with "قرّب واحكي معي 👋"), and whether "ساعدني أختار" can be offered. The attract mode stops as soon as a conversation starts.

### Client behaviour for the screen (`/robot`)
- Display modes arrive faster than audio plays. The face follows the mode that was current when each audio piece was queued, and the subtitle shows the piece being spoken (`answer.audio.text`). When the queue is empty, the latest `mode` applies.
- If the WebSocket closes, or no message arrives for 40 s while heartbeats are sent every 5 s (the backend answers heartbeats only between messages, so a slow answer must not look like a lost backend), the client shows the "service unavailable" face and reconnects every 2 s. This is display only; safety never depends on it.

### Telemetry rules
- Telemetry is for recording and plotting only. The robot never waits for the backend to move or to stop (rule 4), and it keeps its own buffer if the connection drops.
- `is_simulated` has no default: every run must say whether it is a simulation. Simulated runs are shown with a "SIMULATED — not a physical result" banner and can never receive return trials (rule 5).
- Conversations started while a run is open are linked to it (`sessions.robot_run_id`).
- Return trials are **not** protocol messages: they are tape measurements typed into `/admin` → Robot runs.

## Conversation
```
session.start → greeting audio → LISTENING
  speech → audio.chunk… audio.end → transcript → (question) filler "لحظة بس…" → answer.text + answer.audio → "عندك سؤال ثاني؟" → FOLLOW_UP
  "لا شكراً" / "شكراً"            → goodbye → session.ended(user_done) → IDLE
  silence (listen.timeout)         → polite goodbye → session.ended(timeout) → IDLE
  "عيدها"                          → previous answer again
  unclear / empty                  → "عذراً، ما سمعتك منيح. ممكن تعيد؟"
  new visitor (Speaker 2+)         → "أهلين فيك كمان!" then the answer, using that visitor's own history
  overlap (camera)                 → "لو سمحتوا واحد واحد…" and no answer
```
Streaming and safety (2026-11-13): every speech piece is checked before text-to-speech. A piece that contains a number that is not in the retrieved passages is never spoken or shown; the robot says once "ما عندي الرقم الدقيق لهاي المعلومة، بنصحك تسأل دائرة القبول والتسجيل." instead, and `answer.text.invented_numbers` lists the dropped numbers. `LLM_STREAMING=false` returns to "whole answer first".

Speaking pace: the backend asks text-to-speech for a slightly faster voice (`TTS_SPEED`, default 1.12) and a lively style; the client only applies `speech_rate` on top (1.0 by default). The TTS cache key contains the model, voice, instructions and speed, so changed settings never replay old audio (`prepare-phrases` regenerates the fixed phrases).

Turn-taking: the robot mutes its microphone while it is speaking or thinking, and interruption is not supported yet (a future option with echo cancellation).
