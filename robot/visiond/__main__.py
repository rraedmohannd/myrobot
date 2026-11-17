"""`python -m visiond` — see `--help`. Settings come from the environment, flags override."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from visiond.app import VisionApp
from visiond.config import VisionConfig, config_from_env, load_thresholds


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m visiond",
        description="Camera -> faces -> engagement / active speaker -> pan-servo target. "
        "Frames never leave this process; only ids, boxes and flags go to robotd.",
    )
    p.add_argument(
        "--source",
        help="camera: device index (0), URL (http://<phone-ip>:8080/video for IP Webcam, "
        "http://<phone-ip>:4747/video for DroidCam) or a video file [env CAMERA_SOURCE, default 0]",
    )
    p.add_argument("--robotd-url", help="[env ROBOTD_VISION_URL, ws://127.0.0.1:8765/ws/vision]")
    p.add_argument("--models-dir", type=Path, help="[env VISION_MODELS_DIR, robot/models]")
    p.add_argument(
        "--landmarks",
        choices=["auto", "mediapipe", "yunet"],
        help="auto = MediaPipe when available, else the weak YuNet 5-point fallback "
        "[env VISION_LANDMARKS]",
    )
    p.add_argument("--thresholds", type=Path, help="default: shared/vision_thresholds.json")
    p.add_argument("--hfov-deg", type=float, help="camera horizontal field of view (placeholder)")
    p.add_argument("--max-fps", type=float, help="analysis rate limit [env VISION_MAX_FPS, 15]")
    p.add_argument("--mirror", action="store_true", help="flip the picture left-right")
    p.add_argument("--console", action="store_true", help="print a status line per change/second")
    p.add_argument(
        "--assume-speaking",
        action="store_true",
        help="no microphone/VAD: ASSUME speech while any mouth moves (labelled ASSUMED)",
    )
    p.add_argument(
        "--sim-servo",
        action="store_true",
        help="SIMULATED servo for a fixed camera (laptop test): no hardware moves",
    )
    p.add_argument("--debug-window", action="store_true", help="local preview window (not saved)")
    p.add_argument("--loop-file", action="store_true", help="replay a video file forever")
    p.add_argument("--exit-at-eof", action="store_true", help="stop when the video file ends")
    p.add_argument(
        "--apriltag",
        action="store_true",
        help="EXPERIMENTAL: report the start-mark AprilTag 36h11 (start_tag) [env VISION_APRILTAG]",
    )
    p.add_argument("--apriltag-id", type=int, help="tag id at the start mark [env, default 0]")
    p.add_argument(
        "--apriltag-size-m", type=float, help="side of the black square [env, default 0.15]"
    )
    p.add_argument("--max-seconds", type=float, help="stop after this many seconds")
    return p


def config_from_args(args: argparse.Namespace, base: VisionConfig | None = None) -> VisionConfig:
    cfg = base or config_from_env()
    changes: dict[str, object] = {}
    for name, attr in (
        ("source", "camera_source"),
        ("robotd_url", "robotd_url"),
        ("models_dir", "models_dir"),
        ("landmarks", "landmarks"),
        ("max_fps", "max_fps"),
        ("max_seconds", "max_seconds"),
    ):
        value = getattr(args, name)
        if value is not None:
            changes[attr] = value
    for name, attr in (
        ("mirror", "mirror"),
        ("console", "console"),
        ("assume_speaking", "assume_speaking"),
        ("sim_servo", "sim_servo"),
        ("debug_window", "debug_window"),
        ("loop_file", "loop_file"),
        ("exit_at_eof", "exit_at_eof"),
    ):
        if getattr(args, name):
            changes[attr] = True
    tag_changes: dict[str, object] = {}
    if args.apriltag:
        tag_changes["enabled"] = True
    if args.apriltag_id is not None:
        tag_changes["tag_id"] = args.apriltag_id
    if args.apriltag_size_m is not None:
        tag_changes["tag_size_m"] = args.apriltag_size_m
    if tag_changes:
        changes["apriltag"] = replace(cfg.apriltag, **tag_changes)  # type: ignore[arg-type]
    if args.hfov_deg is not None:
        changes["servo"] = replace(cfg.servo, hfov_deg=args.hfov_deg)
    return replace(cfg, **changes)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    app = VisionApp(config, load_thresholds(args.thresholds))
    print(
        f"[visiond] source={config.camera_source} robotd={config.robotd_url} "
        f"landmarks={config.landmarks} hfov={config.servo.hfov_deg} (placeholder)"
        + (" | servo SIMULATED" if config.sim_servo else "")
        + (" | VAD ASSUMED from mouth motion" if config.assume_speaking else "")
        + (
            f" | AprilTag EXPERIMENTAL id={config.apriltag.tag_id} "
            f"size={config.apriltag.tag_size_m} m (not counted in any result)"
            if config.apriltag.enabled
            else ""
        ),
        flush=True,
    )
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        app.stop()
    except FileNotFoundError as exc:
        print(f"[visiond] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
