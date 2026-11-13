# Multi-speaker conversation — "who is speaking now" (voice + camera)

Requested by the supervisor. Decision on 2026-11-06: **Level 2**.

| Level | Behaviour | Status |
|---|---|---|
| 1 | Tell visitors apart in one conversation (Speaker 1/2/3) by voice; separate follow-up context per speaker | part of Level 2 |
| **2** | **Know which visible person is speaking now (voice + camera lip motion), turn the camera toward that person and answer them** | **adopted** |
| 3 | Separate two or three people talking at the same moment | **out of scope** with one low-cost microphone; the robot asks politely for one speaker at a time |

## Privacy (also stated in the report)
- Speakers are **anonymous**: "Speaker 1/2/3" and a camera track id. There are no names and no identity recognition, which matches the project exclusion "personal identity recognition".
- Voice embeddings and face tracks exist **only in memory during one conversation**. They are discarded when the session ends and never written to the database. The database stores only the speaker number for each turn.

## Pipeline
```
Robot / browser                                   Backend (laptop)
camera ── face detect + track ids                 
       └─ lip landmarks → mouth activity per face  
mic ──── VAD (speech start/end)                    
during speech: face with the highest mouth activity
               = active speaker (+ overlap flag)  ──audio.end{face_track_id, overlap}──►  STT ‖ voice embedding (parallel)
camera pan servo → active speaker's face                                                   SpeakerTracker: voice + face → Speaker N
                                                  ◄──transcript{speaker{label,new,source}}── answer with Speaker N's own history
```

### Voice part (backend, done first)
- Embedding model: **3D-Speaker CAM++ (VoxCeleb, 16 kHz)**, about 30 MB ONNX, run through **sherpa-onnx** (CPU, also runs on the Raspberry Pi). It is downloaded when the Docker image is built.
- The embedding is computed **in parallel with STT**, so it adds no latency.
- `SpeakerTracker` compares the utterance with the session's speakers by cosine similarity, with **hysteresis**: a similarity of **0.30 or more** goes to the closest speaker (0.45 or more counts as confident), and only a similarity **below 0.30** creates a new speaker (at most 4). This came from the first live test (2026-11-06): with a single 0.5 threshold, one weak first sample split one person into two speakers. Utterances shorter than 1 s are not embedded, and the face hint or the last speaker is used instead. The similarities are shown in the /voice log so the thresholds can be calibrated on real voices.
- Voice and face together: once a face track id has been heard with a speaker, they are linked. When the voice is ambiguous, the face decides; when the voice is clearly a different known person, the voice wins.

### Camera part (robot; prototype first in the /voice page with the laptop webcam)
- Face detection and tracking (YuNet on the Pi, reused from the earlier code; MediaPipe in the browser prototype). Each face keeps a track id while it stays in view.
- Lip landmarks → **mouth opening ratio** per face → *mouth activity* = how much it changes over the last ~0.5 s.
- While the VAD says someone is speaking, the face with the highest mouth activity for most of the voiced frames is the **active speaker**. If two or more faces stay above the threshold for more than ~30 % of the voiced frames, the client sends **overlap**.
- The pan servo turns the camera toward the active speaker. The screen shows who is being answered.
- Optional hardware: a USB microphone array with direction-of-arrival (DOA) would add a third cue. Its availability in Jordan still needs to be checked; it is not required.

#### Browser prototype (2026-11-13, demo sprint M7) — built, **not yet validated with people**
- Page: `http://localhost:8000/robot?camera=1` (or press `C` on `/robot`). The camera is **off by default**, so the normal demo does not depend on it.
- Code: `backend/app/static/speaker-camera.js`. MediaPipe Face Landmarker (wasm + model) is vendored in `static/vendor/mediapipe/` with its Apache-2.0 licence; nothing is loaded from the internet.
- Tracking: greedy IoU matching (≥ 0.25) gives each face a track id; a face that disappears for more than 1 s gets a new id when it returns.
- Mouth opening ratio = inner-lip distance ÷ mouth width (landmarks 13, 14, 78, 308). Mouth activity = standard deviation of the ratio over the last 0.5 s. A face is "talking" in a frame when its activity is ≥ 0.025 (`?mouth_thr=`; **this threshold is a first guess and must be calibrated on real faces**).
- While the VAD says someone is speaking: the face with the most "talking" frames is the active speaker → `audio.end.face_track_id`. Two or more talking faces in more than 30 % of the frames → `overlap: true`. With exactly one face in view and no clear lip motion, that face is used.
- Preview: small camera picture with a box per face; the active speaker's box is green and thick.
- Optional session start: `?face_start=1` (and `&face_start_ms=1500`): a face that looks at the camera for 1.5 s sends `session.start`. Browsers need one click or key press on the page before sound may play, so the first conversation after loading still needs the button. It re-arms only after the camera saw nobody for 2 s.
- Privacy: frames, landmarks and boxes never leave the browser; only `face_track_id` (a small counter) and `overlap` are sent.
- What was checked: the pure logic (tracking, active speaker, overlap, looking timer) with `node backend/tests/js/speaker_logic_test.mjs`, and the page in a headless browser with a synthetic camera (model loads locally, loop runs, no external requests). **Not checked: real faces.** The validation steps below are still to do.

### Conversation behaviour
- Each speaker gets their own short history (their last turns), so "وكم ساعة؟" refers to *their* previous question.
- When a new person (Speaker 2+) asks a question, a cached greeting plays at once ("أهلين فيك كمان!"), which also covers part of the answer latency.
- Overlap → "لو سمحتوا واحد واحد، عشان أقدر أسمعكم منيح. تفضّلوا." and nothing is answered from the mixed audio.

## Validation (numbers for the report)
1. **Voice labelling:** 3 people, 5 questions each, in random order. Measure the % of utterances given the right speaker number (target to be agreed with the supervisor, proposed ≥ 85 %).
2. **Active speaker with camera:** 2–3 people in front of the camera taking turns. Measure the % of utterances where the correct face is chosen, and the time until the camera points at them.
3. **Overlap:** two people speaking at once in 10 trials. Count how often the robot asks for one speaker at a time.
4. Browser prototype, manual check (do this first, results into docs/DEMO_SPRINT_LOG.md):
   - One person: open `/robot?camera=1`, start, ask 5 questions. Expected: one box, it turns green while you speak, the transcript in `/admin` → Conversations shows source `face` or `voice+face`, and "متحدث 1" every time. While you are silent and only nod or smile, the box should not turn green (if it does, raise `mouth_thr`; if it never turns green while you talk, lower it).
   - Two people side by side (0.5–1.5 m): take turns, 5 questions each. Expected: the green box follows the speaker and the badge switches between "متحدث 1" and "متحدث 2". Then both talk at once 5 times: expected "لو سمحتوا واحد واحد…". Count correct / total for each.
5. Report conditions honestly: room noise, distance (about 0.5–1.5 m), lighting, and the microphone used.
