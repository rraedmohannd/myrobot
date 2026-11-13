// Tests of the pure "who is speaking" logic in app/static/speaker-camera.js (no camera needed).
// Run on the laptop:   node backend/tests/js/speaker_logic_test.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const L = require("../../app/static/speaker-camera.js");

// A fake face: 478 landmarks inside a box, with a mouth of the given opening ratio.
function face(x, y, w, h, ratio, nosePos = 0.5) {
  const lm = Array.from({ length: 478 }, (_, i) => ({ x: x + (w * (i % 10)) / 9, y: y + (h * (i % 7)) / 6 }));
  const cx = x + w / 2, my = y + h * 0.75, mouthW = w * 0.4;
  lm[L.LM.mouthLeft] = { x: cx - mouthW / 2, y: my };
  lm[L.LM.mouthRight] = { x: cx + mouthW / 2, y: my };
  lm[L.LM.lipTop] = { x: cx, y: my - (ratio * mouthW) / 2 };
  lm[L.LM.lipBottom] = { x: cx, y: my + (ratio * mouthW) / 2 };
  lm[L.LM.cheekLeft] = { x, y: y + h / 2 };
  lm[L.LM.cheekRight] = { x: x + w, y: y + h / 2 };
  lm[L.LM.nose] = { x: x + w * nosePos, y: y + h / 2 };
  return lm;
}
const seen = lm => ({ box: L.faceBox(lm), ratio: L.mouthRatio(lm), looking: L.lookingAtCamera(lm) });
const talking = i => 0.25 + 0.2 * Math.sin(i * 1.3);   // the mouth opens and closes
const SILENT = 0.02;

// --- geometry ---
{
  const lm = face(0.2, 0.2, 0.2, 0.3, 0.4);
  const box = L.faceBox(lm);
  assert.ok(Math.abs(box.x - 0.2) < 1e-9 && Math.abs(box.w - 0.2) < 1e-9 && Math.abs(box.h - 0.3) < 1e-9);
  assert.ok(Math.abs(L.mouthRatio(lm) - 0.4) < 1e-9);
  assert.equal(L.lookingAtCamera(lm), true);
  assert.equal(L.lookingAtCamera(face(0.2, 0.2, 0.2, 0.3, 0.1, 0.1)), false);   // head turned away
  assert.equal(L.iou({ x: 0, y: 0, w: 1, h: 1 }, { x: 0, y: 0, w: 1, h: 1 }), 1);
  assert.equal(L.iou({ x: 0, y: 0, w: 1, h: 1 }, { x: 2, y: 2, w: 1, h: 1 }), 0);
  assert.ok(Math.abs(L.iou({ x: 0, y: 0, w: 2, h: 1 }, { x: 1, y: 0, w: 2, h: 1 }) - 1 / 3) < 1e-9);
  assert.equal(L.std([5]), 0);
  assert.ok(Math.abs(L.std([1, 3]) - 1) < 1e-9);
}

// --- tracking: ids stay stable while faces move slowly, and a new face gets a new id ---
{
  const tracker = new L.FaceTracker();
  let tracks;
  for (let i = 0; i < 20; i++) {
    tracks = tracker.update([seen(face(0.1 + i * 0.004, 0.2, 0.2, 0.3, SILENT)), seen(face(0.6, 0.25, 0.2, 0.3, SILENT))], i * 66);
  }
  assert.deepEqual(tracks.map(t => t.id).sort(), [1, 2]);
  tracks = tracker.update([seen(face(0.6, 0.25, 0.2, 0.3, SILENT))], 20 * 66);    // face 1 left the view
  assert.deepEqual(tracks.map(t => t.id), [2]);
  tracks = tracker.update([seen(face(0.6, 0.25, 0.2, 0.3, SILENT)), seen(face(0.18, 0.2, 0.2, 0.3, SILENT))], 21 * 66);
  assert.deepEqual(tracks.map(t => t.id).sort(), [1, 2]);                          // back within 1 s: same id
  tracks = tracker.update([seen(face(0.6, 0.25, 0.2, 0.3, SILENT))], 21 * 66 + 1500);
  tracks = tracker.update([seen(face(0.6, 0.25, 0.2, 0.3, SILENT)), seen(face(0.18, 0.2, 0.2, 0.3, SILENT))], 21 * 66 + 1600);
  assert.deepEqual(tracks.map(t => t.id).sort(), [2, 3]);                          // gone for longer: a new id
}

// --- active speaker: the face whose mouth moves ---
function utterance(ratios /* per face: i => ratio */, frames = 30) {
  const tracker = new L.FaceTracker(), obs = new L.SpeechObserver();
  for (let i = 0; i < 10; i++) tracker.update(ratios.map((r, k) => seen(face(0.1 + 0.4 * k, 0.2, 0.2, 0.3, SILENT))), i * 66);
  for (let i = 10; i < 10 + frames; i++) obs.frame(tracker.update(ratios.map((r, k) => seen(face(0.1 + 0.4 * k, 0.2, 0.2, 0.3, r(i)))), i * 66));
  return obs.result();
}
assert.deepEqual(utterance([talking, () => SILENT]), { overlap: false, face_track_id: 1 });
assert.deepEqual(utterance([() => SILENT, talking]), { overlap: false, face_track_id: 2 });
assert.deepEqual(utterance([() => SILENT]), { overlap: false, face_track_id: 1 });        // one quiet face: it is that person
assert.deepEqual(utterance([() => SILENT, () => SILENT]), { overlap: false });            // two quiet faces: unknown
assert.deepEqual(utterance([]), { overlap: false });                                      // nobody in view
assert.deepEqual(new L.SpeechObserver().result(), {});                                    // no frames at all

// --- overlap: two faces talking for more than 30 % of the frames ---
{
  const both = utterance([talking, i => talking(i + 2)]);
  assert.equal(both.overlap, true);
  // the second person only says a word at the start (about 15 % of the frames): not an overlap
  const brief = utterance([talking, i => (i < 13 ? talking(i) : SILENT)], 40);
  assert.equal(brief.overlap, false);
  assert.equal(brief.face_track_id, 1);
}

// --- "looking at the camera" timer used for the optional session start ---
{
  const tracker = new L.FaceTracker();
  let t;
  for (let i = 0; i <= 30; i++) [t] = tracker.update([seen(face(0.3, 0.2, 0.2, 0.3, SILENT, i < 5 ? 0.1 : 0.5))], i * 66);
  assert.equal(t.lookingSince, 5 * 66);
  assert.ok(30 * 66 - t.lookingSince >= 1500);
}

console.log("speaker logic: all checks passed");
