# Robot ↔ backend protocol (v1)

Transport: WebSocket `ws://<laptop>:8000/ws/robot` (add `?token=...` if `ROBOT_TOKEN` is set).
Every message is a JSON object with `type`. Backend messages also carry `id` (12 hex chars) and `ts` (Unix ms).
Messages are processed in order, with one conversation at a time per connection.
The reference client is the browser page `/voice`. The Raspberry Pi client must behave the same way.

**Safety:** nothing here is needed to stop the robot. Motor stop, obstacles and command timeouts stay on the ESP32 and the robot (CLAUDE.md rule 4). If the backend stops answering heartbeats, the robot shows its "service unavailable" face and keeps working locally.

## Robot → backend
| type | fields | meaning |
|---|---|---|
| `hello` | `robot_id`, `fw_version`, `protocol_version` | first message after connecting |
| `heartbeat` | – | every 5 s; backend answers `heartbeat` (allow ≥ 15 s before declaring the backend lost, because answers can take a few seconds) |
| `session.start` | `lang?` (`ar` default) | a visitor is engaging (face detected); backend greets |
| `audio.chunk` | `pcm16_b64`, `sample_rate` (16000), `seq` | mono PCM16 little-endian, about 100 ms per chunk, sent only while the visitor speaks (VAD) |
| `audio.end` | `face_track_id?`, `overlap?` | VAD detected the end of speech (about 0.9 s of silence). The camera adds the track id of the face whose lips moved, and `overlap: true` if two or more people spoke at once (docs/SPEAKERS.md) |
| `text.input` | `text` | typed question (testing); starts a session without a greeting if none is active |
| `listen.timeout` | – | nobody spoke for `follow_up_timeout_s` after the robot finished talking |
| `session.end` | `reason`: `user_done` / `lost_face` / `timeout` | visitor left or the robot ended the session |

## Backend → robot
| type | fields | meaning |
|---|---|---|
| `hello.ack` | `protocol_version`, `follow_up_timeout_s`, `max_utterance_s` | settings for the client |
| `session.started` / `session.ended` | `session_id`, `reason` | conversation boundaries (stored in `sessions`) |
| `dialogue.state` | `state`: IDLE, GREETING, LISTENING, ANSWERING, FOLLOW_UP, CLOSING | **the robot may listen only in LISTENING / FOLLOW_UP, and only after all queued audio has finished playing** |
| `display.update` | `mode` (idle, attentive, listening, processing, answering, finished, unavailable), `subtitle`, `card?`, `speaker_label?` | screen content; `speaker_label` = the visitor being answered |
| `transcript` | `text`, `lang`, `intent`, `speaker{label, is_new, confidence, source}` | what the backend heard and who said it (anonymous Speaker N; source = voice / face / voice+face / last / first) |
| `answer.text` | `text`, `lang`, `found`, `sources[]`, `timings`, `invented_numbers` | the grounded answer (subtitles) |
| `answer.audio` | `seq`, `last`, `text`, `mp3_b64` | speech, sent sentence by sentence (the first sentence is sent first so playback can start early) |
| `turn.timings` | `stt_ms`, `retrieval_ms`, `llm_ms`, `tts_ms`, `total_ms` | latency for the report; `total_ms` runs from end of speech to the first audio chunk |
| `heartbeat` | – | reply to heartbeat |
| `error` | `code`, `message` | `stt_failed`, `tts_failed`, `unknown_type`, `bad_message`, `internal` |

## Conversation
```
session.start → greeting audio → LISTENING
  speech → audio.chunk… audio.end → transcript → (question) answer.text + answer.audio → "عندك سؤال ثاني؟" → FOLLOW_UP
  "لا شكراً" / "شكراً"            → goodbye → session.ended(user_done) → IDLE
  silence (listen.timeout)         → polite goodbye → session.ended(timeout) → IDLE
  "عيدها"                          → previous answer again
  unclear / empty                  → "عذراً، ما سمعتك منيح. ممكن تعيد؟"
  new visitor (Speaker 2+)         → "أهلين فيك كمان!" then the answer, using that visitor's own history
  overlap (camera)                 → "لو سمحتوا واحد واحد…" and no answer
```
Turn-taking: the robot mutes its microphone while it is speaking or thinking, and interruption is not supported yet (a future option with echo cancellation).
