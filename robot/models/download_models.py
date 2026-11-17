"""Download the two local vision models used by visiond (run once; needs internet).

    python robot/models/download_models.py            # both models
    python robot/models/download_models.py --yunet    # only the face detector

The model files are NOT committed (see .gitignore); this script and README.md are.

| File                               | Source (official)                | Licence    |
|------------------------------------|----------------------------------|------------|
| face_detection_yunet_2023mar.onnx  | OpenCV Zoo (github.com/opencv)   | MIT        |
| face_landmarker.task               | Google MediaPipe model storage   | Apache-2.0 |
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent

MODELS: dict[str, dict[str, str]] = {
    "yunet": {
        "file": "face_detection_yunet_2023mar.onnx",
        "url": (
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
            "face_detection_yunet_2023mar.onnx"
        ),
        "licence": "MIT (opencv_zoo/models/face_detection_yunet/LICENSE)",
    },
    "landmarker": {
        "file": "face_landmarker.task",
        "url": (
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
            "float16/1/face_landmarker.task"
        ),
        "licence": "Apache-2.0 (MediaPipe Face Landmarker model card)",
    },
}
MIN_BYTES = 100_000  # a smaller file is an error page or a Git LFS pointer, not a model


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(name: str, *, force: bool = False) -> Path:
    spec = MODELS[name]
    target = MODELS_DIR / spec["file"]
    if target.is_file() and target.stat().st_size >= MIN_BYTES and not force:
        print(f"{spec['file']}: already here ({target.stat().st_size} bytes)")
        return target
    print(f"{spec['file']}: downloading from {spec['url']}")
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "visiond-setup"})
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (fixed https URL)
        partial.write_bytes(response.read())
    if partial.stat().st_size < MIN_BYTES:
        partial.unlink()
        raise RuntimeError(f"{spec['file']}: the download is too small to be the model")
    partial.replace(target)
    print(f"{spec['file']}: {target.stat().st_size} bytes, sha256 {sha256(target)}")
    print(f"  licence: {spec['licence']}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yunet", action="store_true", help="only the YuNet face detector")
    parser.add_argument("--landmarker", action="store_true", help="only the MediaPipe model")
    parser.add_argument("--force", action="store_true", help="download again")
    args = parser.parse_args(argv)
    names = [n for n in MODELS if getattr(args, n)] or list(MODELS)
    failed = False
    for name in names:
        try:
            download(name, force=args.force)
        except Exception as exc:  # noqa: BLE001 - report and continue with the other model
            failed = True
            print(f"{MODELS[name]['file']}: FAILED ({exc})", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
