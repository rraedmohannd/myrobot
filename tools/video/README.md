# Program videos (`tools/video/`)

Renders the program explainer videos (1920×1080, 30 fps, MP4) with the **robot's own voice**. Made on 2026-11-17 after HyperFrames by HeyGen was tried and dropped (see `docs/VIDEO_SCRIPTS.md`, "Production log").

## What is here
| File | What |
|---|---|
| `engine.html` | The scenes (intro, title, stats, donut, chips, rows, grid, badges, studio, quote, closing, outro), line drawings, icons, the talking robot face and captions. Pure HTML/JS; every frame is a function of time (`setTime(t)`). |
| `build.py` | Reads `<program>/spec.json` and `<program>/audio/*.mp3`, aligns each caption chunk to the pauses in the audio, mixes the narration track and computes the mouth envelope. Writes `timeline.json` and `narration.wav`. |
| `render.js` | Opens `engine.html` in Playwright Chromium, renders every frame and pipes it to ffmpeg. `--stills t1,t2,…` writes test frames only. |
| `fetch_audio.py` | Gets each narration line from the backend (`POST /api/tts`, cached, the robot's voice). |
| `ise/`, `arch/`, `ree/` | `spec.json` per program: the on-screen text, the spoken text and the displayed caption of every chunk, and the scene data. |
| `fonts/` | Cairo (Arabic subset, SIL OFL 1.1, Google Fonts) and Outfit (SIL OFL 1.1). |

## Facts rule
Every number and claim in a `spec.json` comes from the approved sources listed in `docs/VIDEO_SCRIPTS.md`. Spoken numbers are written as words (so the TTS reads them correctly); captions and boards show the same number in digits. Listen to each video once and compare every spoken number with the board before uploading it.

## Render one program
Needs: Python 3 with numpy, Node 18+ with `playwright` (Chromium installed), ffmpeg on the PATH, and the backend running.
```
python fetch_audio.py ise                # robot voice, cached by the backend
python build.py ise                       # timeline.json + narration.wav
node render.js ise --stills 5,20,40       # optional: check a few frames in ise/stills/
node render.js ise                        # ise/ise.mp4 (about 10 min on a laptop)
ffmpeg -i ise/ise.mp4 -c:v copy -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:a aac -b:a 160k ise/ise_final.mp4
```
Then upload in `/admin → Media`, link it to the program and approve it.

## New program
Copy a `spec.json`, change the texts from that program's approved sources, add a line drawing to `DRAWINGS` in `engine.html` if wanted (`drawing` field), then run the four commands above.
