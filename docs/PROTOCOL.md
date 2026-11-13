# Robot ↔ backend protocol (v1)

Transport: WebSocket `ws://<laptop>:8000/ws/robot` (add `?token=...` if `ROBOT_TOKEN` is set).
Every message is a JSON object with `type`. Backend messages also carry `id` (12 hex chars) and `ts` (Unix ms).
Messages are processed in order, with one conversation at a time per connection.
The reference client is `backend/app/static/robot-client.js`, shared by the browser pages `/voice` (test bench) and `/robot` (the robot's screen, standing in for the 7" HDMI display). The Raspberry Pi client must behave the same way.

**Safety:** nothing here is needed to stop the robot. Motor stop, obstacles and command timeouts stay on the ESP32 and the robot (CLAUDE.md rule 4). If the backend stops answering heartbeats, the robot shows its "service unavailable" face and keeps working locally.

## Robot → backend
| type | fields | meaning |
|---|---|---|
| `hello` | `robot_id`, `fw_version`, `protocol_version`, `audio_stream?` | first message after connecting. `audio_stream: ["pcm16"]` = this client can play answer speech sent as PCM chunks (see `answer.audio.start`); without it the backend sends whole mp3 pieces only |
| `heartbeat` | – | every 5 s; backend answers `heartbeat` (allow ≥ 15 s before declaring the backend lost, because answers can take a few seconds) |
| `session.start` | `lang?` (`ar` default) | a visitor is engaging (face detected); backend greets |
| `audio.chunk` | `pcm16_b64`, `sample_rate` (16000), `seq` | mono PCM16 little-endian, about 100 ms per chunk, sent only while the visitor speaks (VAD) |
| `audio.end` | `face_track_id?`, `overlap?` | VAD detected the end of speech (`vad_silence_ms` of silence, from `hello.ack`; 700 ms by default). The camera adds the track id of the face whose lips moved (an integer that only means "the same face as before"), and `overlap: true` if two or more faces were talking in more than about 30 % of the voiced frames (docs/SPEAKERS.md). Without a camera both fields are simply absent. No image or landmark is ever sent |
| `text.input` | `text` | typed question (testing); starts a session without a greeting if none is active |
| `listen.timeout` | – | nobody spoke for `follow_up_timeout_s` after the robot finished talking. During the pause of an explainer this means "go on" (see below); otherwise the robot says goodbye |
| `explainer.start` | `program` (slug), `lang?` | a program button was tapped on the screen: play that program's approved explainer. Starts a conversation without the greeting if none is active. Answer: the explainer messages below, or `error` `no_explainer` |
| `explainer.continue` | – | the "كمّل" button during the pause after segment 3 |
| `explainer.stop` | – | the stop button: the client has already stopped the audio and emptied its queue; the backend forgets the rest and asks "عندك سؤال ثاني؟" |
| `interest.start` | `lang?` | the "ساعدني أختار" button: start the interest questions (`error` `no_interests` when no mapping is approved) |
| `interest.choice` | `key` | an interest button was tapped (`key` from `interest.question.options`; `none` = no second interest) |
| `session.end` | `reason`: `user_done` / `lost_face` / `timeout` | visitor left or the robot ended the session |
| `run.start` | `is_simulated` (**required**, true/false), `odom_config?`, `notes?` | a robot run begins (roaming, return-to-start); backend answers `run.started`. One run per connection; a new `run.start` closes the previous run |
| `telemetry` | `samples[]`: `{t_ms, x_m, y_m, theta_rad, v_mps?, omega_rps?, enc?, imu?, state?}` | batched pose samples (at most 1000 per message; about 10 Hz is enough for the plot). `t_ms` = ms since run start, pose in the start frame (docs/ODOMETRY.md) |
| `event` | `t_ms`, `kind`, `data?` | `obstacle`, `return_start`, `return_blocked`, `return_done`, `return_failed`, `estop`, `comm_loss`, `slip`, … |
| `run.end` | – | the run is finished; backend answers `run.ended` |

## Backend → robot
| type | fields | meaning |
|---|---|---|
| `hello.ack` | `protocol_version`, `follow_up_timeout_s`, `max_utterance_s`, `speech_rate`, `vad_silence_ms`, `audio_stream` | settings for the client. `speech_rate` (SPEECH_RATE, default 1.0) is the playback rate for all robot speech, applied with the pitch preserved; `vad_silence_ms` (VAD_SILENCE_MS) is the silence that ends an utterance; `audio_stream` says whether answers will arrive as PCM chunks |
| `session.started` / `session.ended` | `session_id`, `reason` | conversation boundaries (stored in `sessions`) |
| `dialogue.state` | `state`: IDLE, GREETING, LISTENING, ANSWERING, EXPLAINING, FOLLOW_UP, CLOSING | **the robot may listen only in LISTENING / FOLLOW_UP, and only after all queued audio has finished playing** |
| `display.update` | `mode` (idle, attentive, listening, processing, answering, finished, unavailable), `subtitle`, `card?`, `speaker_label?` | screen content; `speaker_label` = the visitor being answered. `card` is described below |
| `transcript` | `text`, `lang`, `intent`, `speaker{label, is_new, confidence, source, sims}` | what the backend heard and who said it (anonymous Speaker N; source = voice / face / voice+face / last / first) |
| `answer.text` | `text`, `lang`, `found`, `sources[]`, `timings`, `invented_numbers` | the grounded answer as spoken (subtitles). It is sent when the model has finished, so **it can arrive after the first `answer.audio` pieces** |
| `answer.audio` | `seq`, `last`, `text`, `mp3_b64`, `segment?` | speech, sent piece by piece **while the model is still writing** (a short first piece, then one piece per sentence); play the pieces in arrival order |
| `answer.audio.start` / `.chunk` / `.end` | `seq`, `text`, `format` (`pcm16`), `sample_rate` (24000) / `seq`, `pcm16_b64` / `seq`, `last` | the same speech pieces as `answer.audio`, but **streamed while text-to-speech is still generating them**. Experimental and off by default (`TTS_STREAMING=false`, see DECISIONS 2026-11-13); when on, only for clients that sent `audio_stream` in `hello`, and only for fresh answers (cached phrases stay `answer.audio`). Chunks are mono PCM16 little-endian, about 100 ms each, whole samples. Start playing after about 0.2 s of audio. A started piece is always closed by `.end`, also after an error. Pieces of both kinds share one playback queue, in arrival order |
| `turn.timings` | `stt_ms`, `retrieval_ms`, `llm_ms`, `llm_first_ms`, `ack_ms`, `tts_ms`, `total_ms` | latency for the report: `ack_ms` runs from end of speech to the first audio of any kind (cached filler or greeting); `tts_ms` = `total_ms` runs from end of speech to the first audio of the **answer**; `llm_first_ms` runs from the LLM request to the first speakable piece, `llm_ms` to the end of the LLM answer |
| `explainer.start` | `program {slug, name}`, `lang`, `total` | an approved program explainer begins (state EXPLAINING) |
| `explainer.segment` | `index` (1-based), `total`, `scene` | the scene of one segment. It is sent **immediately before** the `answer.audio` of that segment, which carries `segment: index`; the screen shows the scene **when that audio starts playing**, so scenes stay in sync with the speech |
| `explainer.pause` | `next_index`, `total` | sent after segment 3 when at least two more segments follow; the next audio is "بدك أكمل ولا عندك سؤال؟" and the state becomes FOLLOW_UP |
| `explainer.end` | `reason`: `finished` / `stopped` / `question` | the presentation is over; later audio belongs to the normal conversation |
| `interest.question` | `step` (1 or 2), `text`, `options[] {key, label}` | "what do you like?": the screen shows the options as buttons; the visitor may also answer by voice |
| `interest.result` | `programs[] {slug, name, line, has_explainer}`, `text` | one or two suggested programs (approved mapping only; may be empty) and what the robot says; programs with `has_explainer` are tappable |
| `run.started` / `run.ended` | `run_id`, `is_simulated` / `run_id`, `samples`, `events` | run boundaries (stored in `robot_runs`); `run.ended` reports how many samples and events were stored |
| `heartbeat` | – | reply to heartbeat |
| `error` | `code`, `message` | `stt_failed`, `tts_failed`, `unknown_type`, `bad_message`, `no_run` (telemetry before `run.start`), `no_explainer`, `no_interests`, `internal` |

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
