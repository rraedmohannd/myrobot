"""Fetch the narration clips of one program video from the robot backend (the robot's own voice).

Usage: python fetch_audio.py <program_dir> [--backend http://localhost:8000]

For every scene in <program_dir>/spec.json the spoken chunks are joined with spaces and sent to
POST /api/tts. The backend caches each phrase (same text + voice + instructions = same file), so
asking again for an unchanged line costs nothing. The mp3 is saved as <program_dir>/audio/<key>.mp3.
Standard library only.
"""

import json
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    prog = Path(args[0])
    backend = "http://localhost:8000"
    if "--backend" in args:
        backend = args[args.index("--backend") + 1].rstrip("/")
    spec = json.loads((prog / "spec.json").read_text(encoding="utf-8"))
    out = prog / "audio"
    out.mkdir(parents=True, exist_ok=True)
    for sc in spec["scenes"]:
        text = " ".join(spoken for spoken, _display in sc["chunks"])
        req = urllib.request.Request(f"{backend}/api/tts", data=json.dumps({"text": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        (out / f"{sc['audio']}.mp3").write_bytes(data)
        print(f"{sc['audio']}.mp3  {len(data):>7} bytes  {text[:60]}")


if __name__ == "__main__":
    main()
