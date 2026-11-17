# Vision models for `visiond`

The model files are **not committed** (`.gitignore`: `robot/models/*.onnx`, `robot/models/*.task`).
Download them once (needs internet):

```
python robot/models/download_models.py
```

| File | Used for | Official source | Licence |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` (about 233 KB) | face detection + 5 points (`cv2.FaceDetectorYN`) | OpenCV Zoo: `https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet` | MIT (the `LICENSE` file in that folder) |
| `face_landmarker.task` (about 3.8 MB) | 478 face landmarks: mouth opening, head pose (MediaPipe Face Landmarker) | Google MediaPipe model storage: `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task` | Apache-2.0 (MediaPipe) |

`face_landmarker.task` is the same file as the one vendored for the browser prototype in
`backend/app/static/vendor/mediapipe/` (same SHA-256 on 2026-11-17:
`64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`).
YuNet downloaded on 2026-11-17 had SHA-256
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.

Both models run locally on the CPU. They detect faces and face landmarks only; they do not
recognise who a person is, and `visiond` stores nothing.

Without `face_landmarker.task` (or without the `mediapipe` package) `visiond` still runs, on
YuNet's 5 points: head yaw is a rough estimate and the mouth opening cannot be seen, so
"who is speaking" is unreliable in that mode.
