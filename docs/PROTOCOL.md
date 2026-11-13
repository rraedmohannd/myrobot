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
| `listen.timeout` | – | nobody spoke for `follow_up_timeout_s` after the robot finished talking |
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
| `dialogue.state` | `state`: IDLE, GREETING, LISTENING, ANSWERING, FOLLOW_UP, CLOSING | **the robot may listen only in LISTENING / FOLLOW_UP, and only after all queued audio has finished playing** |
| `display.update` | `mode` (idle, attentive, listening, processing, answering, finished, unavailable), `subtitle`, `card?`, `speaker_label?` | screen content; `speaker_label` = the visitor being answered. `card` is described below |
| `transcript` | `text`, `lang`, `intent`, `speaker{label, is_new, confidence, source, sims}` | what the backend heard and who said it (anonymous Speaker N; source = voice / face / voice+face / last / first) |
| `answer.text` | `text`, `lang`, `found`, `sources[]`, `timings`, `invented_numbers` | the grounded answer as spoken (subtitles). It is sent when the model has finished, so **it can arrive after the first `answer.audio` pieces** |
| `answer.audio` | `seq`, `last`, `text`, `mp3_b64` | speech, sent piece by piece **while the model is still writing** (a short first piece, then one piece per sentence); play the pieces in arrival order |
| `answer.audio.start` / `.chunk` / `.end` | `seq`, `text`, `format` (`pcm16`), `sample_rate` (24000) / `seq`, `pcm16_b64` / `seq`, `last` | the same speech pieces as `answer.audio`, but **streamed while text-to-speech is still generating them** (only for clients that sent `audio_stream` in `hello`, and only for fresh answers; cached phrases stay `answer.audio`). Chunks are mono PCM16 little-endian, about 100 ms each, whole samples. Start playing after about 0.2 s of audio. A started piece is always closed by `.end`, also after an error. Pieces of both kinds share one playback queue, in arrival order |
| `turn.timings` | `stt_ms`, `retrieval_ms`, `llm_ms`, `llm_first_ms`, `ack_ms`, `tts_ms`, `total_ms` | latency for the report: `ack_ms` runs from end of speech to the first audio of any kind (cached filler or greeting); `tts_ms` = `total_ms` runs from end of speech to the first audio of the **answer**; `llm_first_ms` runs from the LLM request to the first speakable piece, `llm_ms` to the end of the LLM answer |
| `run.started` / `run.ended` | `run_id`, `is_simulated` / `run_id`, `samples`, `events` | run boundaries (stored in `robot_runs`); `run.ended` reports how many samples and events were stored |
| `heartbeat` | – | reply to heartbeat |
| `error` | `code`, `message` | `stt_failed`, `tts_failed`, `unknown_type`, `bad_message`, `no_run` (telemetry before `run.start`), `internal` |

### `display.update.card` (optional; sent with found answers)
| field | meaning |
|---|---|
| `title` | name of the program the answer's sources belong to, or the faculty name when they are mixed or general |
| `lines[]` | at most 3 short lines; they are sentences of the spoken answer (cut with "…" after about 90 characters) |
| `sources[]` | titles of the sources the answer used (this field existed before; older clients that only read `sources` keep working) |
| `media?` | `{kind: "image" or "video", url, alt}`: one **approved** file of the media library, sent only when the answer's sources clearly belong to one program that has approved media. `url` is a path on the backend (`/media/{id}`). The screen shows the image, or plays the video **muted and looping**, next to the subtitles. Several files of a program take turns |

`GET /media/{id}` serves a file only while its review status is `approved`; pending and rejected files answer 404. Files are uploaded and reviewed in `/admin` → Media and are stored locally in `backend/data/media/` (not in git).

The card never contains facts that are not in the grounded answer. Answers with `found=false` and error answers have no card. Clients must ignore card fields they do not know.

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
