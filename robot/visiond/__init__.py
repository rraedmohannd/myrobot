"""visiond: camera -> faces -> engagement / active speaker -> pan-servo target.

A separate process on the Raspberry Pi (and on the laptop for development). Privacy rule:
frames never leave this process and nothing is written to disk; only ids, boxes (fractions of
the frame) and flags are sent to robotd.

Layout:
    config.py     thresholds (shared/vision_thresholds.json) and run-time settings (env / CLI)
    geometry.py   pure maths on landmarks: mouth ratio, facing rule, head pose      (no OpenCV)
    logic.py      pure logic: tracking, engagement, active speaker, overlap, selection (no OpenCV)
    servo.py      pure pan controller and the SIMULATED servo                       (no OpenCV)
    messages.py   the vision.state message and the robotd -> visiond messages       (no OpenCV)
    camera.py     camera source with reconnect                                      (OpenCV)
    detectors.py  YuNet detection, MediaPipe landmarks, YuNet 5-point fallback      (OpenCV)
    link.py       WebSocket client to robotd (retries forever)
    app.py        the process: capture/analysis thread + asyncio output
"""

__version__ = "0.1.0"
