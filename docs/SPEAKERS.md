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

### Conversation behaviour
- Each speaker gets their own short history (their last turns), so "وكم ساعة؟" refers to *their* previous question.
- When a new person (Speaker 2+) asks a question, a cached greeting plays at once ("أهلين فيك كمان!"), which also covers part of the answer latency.
- Overlap → "لو سمحتوا واحد واحد، عشان أقدر أسمعكم منيح. تفضّلوا." and nothing is answered from the mixed audio.

## Validation (numbers for the report)
1. **Voice labelling:** 3 people, 5 questions each, in random order. Measure the % of utterances given the right speaker number (target to be agreed with the supervisor, proposed ≥ 85 %).
2. **Active speaker with camera:** 2–3 people in front of the camera taking turns. Measure the % of utterances where the correct face is chosen, and the time until the camera points at them.
3. **Overlap:** two people speaking at once in 10 trials. Count how often the robot asks for one speaker at a time.
4. Report conditions honestly: room noise, distance (about 0.5–1.5 m), lighting, and the microphone used.
