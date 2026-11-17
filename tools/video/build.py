"""Build the timeline, narration track and mouth envelope for one program video.

Usage: python3 build.py <program_dir>
The program dir holds spec.json and audio/<key>.mp3 (narration clips from the robot's TTS).
Writes <program_dir>/timeline.json and <program_dir>/narration.wav.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS = 30
SR = 24000
INTRO_PRE = 2.4  # visitor bubble alone before the robot answers
GAP_BEFORE = 0.9  # scene visible before its narration starts
HOLD_AFTER = 0.5  # scene stays after its narration ends
OUTRO_HOLD = 2.2


def probe_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def silences(p: Path) -> list[tuple[float, float]]:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "silencedetect=noise=-40dB:d=0.18", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    return list(zip(starts, ends))


def speech_bounds(dur: float, sil: list[tuple[float, float]]) -> tuple[float, float]:
    s0, s1 = 0.0, dur
    for a, b in sil:
        if a <= 0.01:
            s0 = b
        if b >= dur - 0.01:
            s1 = a
    return s0, s1


def align(chunks: list[str], dur: float, sil: list[tuple[float, float]]) -> list[float]:
    """Return start time (clip-relative) of each chunk, snapping boundaries to pauses."""
    s0, s1 = speech_bounds(dur, sil)
    k = len(chunks)
    if k == 1:
        return [s0]
    lens = np.array([max(len(c), 1) for c in chunks], dtype=float)
    cum = np.cumsum(lens)[:-1] / lens.sum()
    targets = s0 + cum * (s1 - s0)
    cands = [((a + b) / 2, b) for a, b in sil if a > s0 + 0.05 and b < s1 - 0.05]
    n, m = len(targets), len(cands)
    if m < n:  # not enough pauses: use proportional times
        return [s0] + list(targets)
    INF = 1e18
    dp = np.full((n + 1, m + 1), INF)
    dp[0, :] = 0
    choice = {}
    for i in range(1, n + 1):
        for j in range(i, m + 1):
            # boundary i uses candidate j-1, or candidate j-1 skipped
            skip = dp[i, j - 1]
            use = dp[i - 1, j - 1] + (cands[j - 1][0] - targets[i - 1]) ** 2
            if use <= skip:
                dp[i, j] = use
                choice[(i, j)] = True
            else:
                dp[i, j] = skip
                choice[(i, j)] = False
    picks, i, j = [], n, m
    while i > 0:
        if choice.get((i, j)):
            picks.append(cands[j - 1][1])  # chunk starts when the pause ends
            i, j = i - 1, j - 1
        else:
            j -= 1
    picks.reverse()
    return [s0] + picks


def main(prog_dir: str) -> None:
    root = Path(prog_dir)
    spec = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    t = 0.0
    scenes = []
    clips = []
    for idx, sc in enumerate(spec["scenes"]):
        clip = root / "audio" / f"{sc['audio']}.mp3"
        dur = probe_duration(clip)
        sil = silences(clip)
        s_start = t
        lead = INTRO_PRE if sc["type"] == "intro" else GAP_BEFORE
        a_start = s_start + lead
        rel = align([c[1] for c in sc["chunks"]], dur, sil)
        speech_end = speech_bounds(dur, sil)[1]
        chunk_times = [a_start + r for r in rel]
        a_end = a_start + speech_end
        hold = OUTRO_HOLD if sc["type"] == "outro" else HOLD_AFTER
        s_end = a_end + hold
        scenes.append({**sc, "start": round(s_start, 3), "end": round(s_end, 3), "audio_start": round(a_start, 3),
                       "audio_end": round(a_end, 3),
                       "chunk_times": [round(x, 3) for x in chunk_times]})
        clips.append((clip, a_start))
        t = s_end
    total = t

    # narration track
    inputs, filters = [], []
    for i, (clip, start) in enumerate(clips):
        inputs += ["-i", str(clip)]
        ms = int(round(start * 1000))
        filters.append(f"[{i}:a]aresample={SR},adelay={ms}|{ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(clips)))
    filters.append(f"{mix}amix=inputs={len(clips)}:normalize=0,apad,atrim=0:{total:.3f}[out]")
    wav = root / "narration.wav"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "[out]", "-ac", "1", "-ar", str(SR), str(wav)], check=True)

    # mouth envelope (RMS per video frame)
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-f", "s16le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    hop = SR // FPS
    n = int(np.ceil(total * FPS))
    env = np.zeros(n)
    for f in range(n):
        seg = pcm[f * hop:(f + 1) * hop]
        env[f] = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
    env = env / (np.percentile(env[env > 0], 95) if np.any(env > 0) else 1)
    env = np.clip(env, 0, 1)
    env = np.convolve(env, np.ones(2) / 2, mode="same")

    timeline = {"fps": FPS, "total": round(total, 3), "scenes": scenes,
                "env": [round(float(x), 3) for x in env],
                **{k: v for k, v in spec.items() if k != "scenes"}}
    (root / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    print(f"total {total:.2f}s, frames {n}")
    for s in scenes:
        print(f"  {s['type']:8s} {s['start']:7.2f} → {s['end']:7.2f}  audio {s['audio_start']:.2f}–{s['audio_end']:.2f}  "
              f"chunks {s['chunk_times']}")


if __name__ == "__main__":
    main(sys.argv[1])
