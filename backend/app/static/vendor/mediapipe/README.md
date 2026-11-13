# MediaPipe Face Landmarker (vendored)

Used by `/static/speaker-camera.js` for the "who is speaking now" camera prototype
(docs/SPEAKERS.md). The files are kept here so the robot screen needs no CDN and keeps working
offline after the first load. All processing happens in the browser; no frame is sent anywhere.

| File | Source | Version |
|---|---|---|
| `vision_bundle.mjs` | npm package `@mediapipe/tasks-vision` | 1.2.3 |
| `vision_wasm_internal.js`, `vision_wasm_internal.wasm` | same package, folder `wasm/` | 1.2.3 |
| `face_landmarker.task` | https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task | float16, version 1 |
| `LICENSE` | https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE | Apache License 2.0 |

Downloaded on 2026-11-13, unmodified. Licence: Apache-2.0 (see `LICENSE`), copyright The MediaPipe Authors.
Only the SIMD build is included (Chrome and Edge support it).

SHA-256 at download time:
```
8707eef0533987efc5b155d64761eeb6e20793f50b9bd1a68dad1cf4719d0ed8 *LICENSE
64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff *face_landmarker.task
fb0c02c296f44ce0cc324ba16bd646d8773eb4a7847db4019cc4d0430db7a9b5 *vision_bundle.mjs
2aa5fd3fdd3d8208cd7587df0bb19fb6925c4ea16a76dd025aedc1f64d6a6309 *vision_wasm_internal.js
225bc06c0f51a9cf31fea0e9e0b0243e9e01fb5ef332cc1a7fa6656dbdfdaabf *vision_wasm_internal.wasm
```
