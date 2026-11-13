/* "Who is speaking now" with the camera — browser prototype (docs/SPEAKERS.md, camera part).

   Privacy: everything here runs inside the browser. No video frame, face image or landmark is
   ever sent or stored. The only values that leave this file are, with `audio.end`:
     face_track_id  a small number that means "the same face as before" during this page visit
     overlap        true when two or more faces were talking at the same time

   Pipeline: webcam -> MediaPipe Face Landmarker (local wasm + model in /static/vendor/mediapipe)
   -> IoU tracking (stable ids) -> mouth-opening ratio per face -> mouth activity (how much the
   ratio changes over the last 0.5 s) -> while the VAD says someone is speaking, the face with
   the most active frames is the speaker.

   The first part (SpeakerLogic) is pure maths with no browser API, so it can be tested with
   Node: `node backend/tests/js/speaker_logic_test.mjs`.
*/
(function (global) {
  "use strict";

  // ---------------- pure logic ----------------
  const LM = { lipTop: 13, lipBottom: 14, mouthLeft: 78, mouthRight: 308, nose: 1, cheekLeft: 234, cheekRight: 454 };
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  function faceBox(landmarks) {
    let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
    for (const p of landmarks) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  /** Inner-lip opening divided by mouth width: about 0 when closed, 0.1-0.6 while talking. */
  function mouthRatio(landmarks) {
    const width = dist(landmarks[LM.mouthLeft], landmarks[LM.mouthRight]);
    return width > 1e-6 ? dist(landmarks[LM.lipTop], landmarks[LM.lipBottom]) / width : 0;
  }
  /** Roughly facing the camera: the nose tip sits near the middle between the two cheeks. */
  function lookingAtCamera(landmarks) {
    const left = landmarks[LM.cheekLeft].x, right = landmarks[LM.cheekRight].x;
    const span = right - left;
    if (Math.abs(span) < 1e-6) return false;
    const pos = (landmarks[LM.nose].x - left) / span;
    return pos > 0.3 && pos < 0.7;
  }
  function iou(a, b) {
    const x0 = Math.max(a.x, b.x), y0 = Math.max(a.y, b.y);
    const x1 = Math.min(a.x + a.w, b.x + b.w), y1 = Math.min(a.y + a.h, b.y + b.h);
    const inter = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
    const union = a.w * a.h + b.w * b.h - inter;
    return union > 0 ? inter / union : 0;
  }
  function std(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((s, v) => s + v, 0) / values.length;
    return Math.sqrt(values.reduce((s, v) => s + (v - mean) * (v - mean), 0) / values.length);
  }

  /** Keeps a stable id per face while it stays in view (greedy IoU matching). */
  class FaceTracker {
    constructor(options) {
      this.o = Object.assign({ iouMin: 0.25, maxAgeMs: 1000, windowMs: 500 }, options || {});
      this.tracks = []; this.nextId = 1;
    }
    /** faces: [{box, ratio, looking}] of one frame at time tMs -> the live tracks. */
    update(faces, tMs) {
      const pairs = [];
      this.tracks.forEach((t, ti) => faces.forEach((f, fi) => { const v = iou(t.box, f.box); if (v >= this.o.iouMin) pairs.push([v, ti, fi]); }));
      pairs.sort((a, b) => b[0] - a[0]);
      const usedT = new Set(), usedF = new Set();
      for (const [, ti, fi] of pairs) {
        if (usedT.has(ti) || usedF.has(fi)) continue;
        usedT.add(ti); usedF.add(fi);
        this._feed(this.tracks[ti], faces[fi], tMs);
      }
      faces.forEach((f, fi) => {
        if (usedF.has(fi)) return;
        const t = { id: this.nextId++, box: f.box, history: [], lastSeen: tMs, lookingSince: null, activity: 0, ratio: 0, visible: true };
        this._feed(t, f, tMs); this.tracks.push(t);
      });
      this.tracks.forEach((t, ti) => { if (!usedT.has(ti) && t.lastSeen !== tMs) t.visible = false; });
      this.tracks = this.tracks.filter(t => tMs - t.lastSeen <= this.o.maxAgeMs);
      return this.tracks.filter(t => t.visible);
    }
    _feed(t, face, tMs) {
      t.box = face.box; t.ratio = face.ratio; t.lastSeen = tMs; t.visible = true;
      t.history.push([tMs, face.ratio]);
      while (t.history.length && tMs - t.history[0][0] > this.o.windowMs) t.history.shift();
      t.activity = std(t.history.map(h => h[1]));               // mouth activity over the last 0.5 s
      if (face.looking) { if (t.lookingSince === null) t.lookingSince = tMs; } else t.lookingSince = null;
    }
  }

  /** Collects the frames of ONE utterance (while the VAD says "speech") and decides who spoke. */
  class SpeechObserver {
    constructor(options) {
      this.o = Object.assign({ activityMin: 0.025, overlapShare: 0.3 }, options || {});
      this.reset();
    }
    reset() { this.frames = 0; this.overlapFrames = 0; this.counts = new Map(); this.sums = new Map(); this.seen = new Set(); }
    frame(tracks) {
      this.frames++;
      let active = 0;
      for (const t of tracks) {
        this.seen.add(t.id);
        if (t.activity < this.o.activityMin) continue;
        active++;
        this.counts.set(t.id, (this.counts.get(t.id) || 0) + 1);
        this.sums.set(t.id, (this.sums.get(t.id) || 0) + t.activity);
      }
      if (active >= 2) this.overlapFrames++;
    }
    /** -> {} when the camera saw nothing useful, else {face_track_id, overlap}. */
    result() {
      if (this.frames === 0) return {};
      let best = null;
      for (const [id, n] of this.counts) {
        if (best === null || n > this.counts.get(best) || (n === this.counts.get(best) && this.sums.get(id) > this.sums.get(best))) best = id;
      }
      // Nobody's lips moved enough, but exactly one face was there: it can only be that person.
      if (best === null && this.seen.size === 1) best = [...this.seen][0];
      const out = { overlap: this.overlapFrames / this.frames > this.o.overlapShare };
      if (best !== null) out.face_track_id = best;
      return out;
    }
  }

  const SpeakerLogic = { LM, faceBox, mouthRatio, lookingAtCamera, iou, std, FaceTracker, SpeechObserver };
  if (typeof module !== "undefined" && module.exports) { module.exports = SpeakerLogic; return; }
  global.SpeakerLogic = SpeakerLogic;

  // ---------------- browser part ----------------
  const VENDOR = "/static/vendor/mediapipe";

  class SpeakerCamera {
    /** options: client (RobotClient), video, canvas, note (elements), box (preview container),
        onFaceStart() for the optional "a face looked at the camera" session start. */
    constructor(options) {
      this.o = Object.assign({ activityMin: 0.025, faceStart: false, faceStartMs: 1500, maxFaces: 4, onFaceStart: null, onStatus: null }, options);
      this.tracker = new FaceTracker();
      this.observer = new SpeechObserver({ activityMin: this.o.activityMin });
      this.running = false; this.landmarker = null; this.stream = null; this.speaking = false;
      this.lastTracks = []; this.activeId = null; this.armed = true; this.noFaceSince = null;
      this.o.client.on("speechstart", () => { this.observer.reset(); this.speaking = true; });
      this.o.client.on("speechend", () => { this.speaking = false; });
    }

    status(text) { if (this.o.onStatus) this.o.onStatus(text, this.running); }

    async start() {
      if (this.running || this.starting) return;
      this.starting = true;
      try {
        this.status("جاري تشغيل الكاميرا… · starting camera");
        if (!this.landmarker) {
          const vision = await import(`${VENDOR}/vision_bundle.mjs`);
          // Local files only (no CDN): the page keeps working offline after the first load.
          this.landmarker = await vision.FaceLandmarker.createFromOptions(
            { wasmLoaderPath: `${VENDOR}/vision_wasm_internal.js`, wasmBinaryPath: `${VENDOR}/vision_wasm_internal.wasm` },
            { baseOptions: { modelAssetPath: `${VENDOR}/face_landmarker.task` }, runningMode: "VIDEO", numFaces: this.o.maxFaces });
        }
        this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240, facingMode: "user" }, audio: false });
        this.o.video.srcObject = this.stream;
        await this.o.video.play();
        this.running = true;
        this.o.box.classList.add("on");
        this.status("الكاميرا شغّالة · المعالجة داخل المتصفح فقط، ولا صورة بتنبعث · camera on, frames never leave the browser");
        this._loop();
      } catch (e) {
        console.warn("camera unavailable", e);
        this.stop();
        this.status("الكاميرا مش متاحة · camera unavailable");
      } finally { this.starting = false; }
    }

    stop() {
      this.running = false;
      if (this.stream) { this.stream.getTracks().forEach(t => t.stop()); this.stream = null; }
      this.o.video.srcObject = null;
      this.o.box.classList.remove("on");
      this.lastTracks = []; this.activeId = null;
      this.status("");
    }
    toggle() { return this.running ? this.stop() : this.start(); }

    /** Fields for `audio.end` (called by the shared client when the VAD ends an utterance). */
    audioEndFields() {
      if (!this.running) return {};
      const result = this.observer.result();
      this.activeId = result.face_track_id ?? null;
      this.observer.reset();
      return result;
    }

    _loop() {
      if (!this.running) return;
      const video = this.o.video, now = performance.now();
      if (video.readyState >= 2 && now - (this.lastRun || 0) >= 60) {       // about 15 frames per second
        this.lastRun = now;
        let faces = [];
        try {
          const out = this.landmarker.detectForVideo(video, now);
          faces = (out.faceLandmarks || []).map(lm => ({ box: faceBox(lm), ratio: mouthRatio(lm), looking: lookingAtCamera(lm) }));
        } catch (e) { console.warn("face landmarker failed", e); }
        const tracks = this.tracker.update(faces, now);
        this.lastTracks = tracks;
        if (this.speaking) {
          this.observer.frame(tracks);
          const talking = tracks.filter(t => t.activity >= this.o.activityMin).sort((a, b) => b.activity - a.activity)[0];
          if (talking) this.activeId = talking.id;
        }
        this._faceStart(tracks, now);
        this._draw(tracks);
      }
      requestAnimationFrame(() => this._loop());
    }

    // Optional: a face that looks at the camera for faceStartMs starts a conversation. It is
    // re-armed only after the camera saw nobody for 2 s, so one visitor is greeted once.
    _faceStart(tracks, now) {
      if (!this.o.faceStart || !this.o.onFaceStart) return;
      if (tracks.length === 0) { if (this.noFaceSince === null) this.noFaceSince = now; if (now - this.noFaceSince > 2000) this.armed = true; return; }
      this.noFaceSince = null;
      if (!this.armed) return;
      if (tracks.some(t => t.lookingSince !== null && now - t.lookingSince >= this.o.faceStartMs)) { this.armed = false; this.o.onFaceStart(); }
    }

    _draw(tracks) {
      const c = this.o.canvas, g = c.getContext("2d");
      g.clearRect(0, 0, c.width, c.height);
      g.font = "bold 20px sans-serif"; g.textBaseline = "top";
      for (const t of tracks) {
        const active = t.id === this.activeId;
        const x = (1 - t.box.x - t.box.w) * c.width, y = t.box.y * c.height;      // the preview is mirrored
        g.lineWidth = active ? 6 : 2; g.strokeStyle = active ? "#3ecf7a" : "#ffffff";
        g.strokeRect(x, y, t.box.w * c.width, t.box.h * c.height);
        g.fillStyle = active ? "#3ecf7a" : "#ffffff";
        g.fillText((active ? "● " : "") + t.id, x + 4, Math.max(0, y - 22));
      }
    }
  }

  global.SpeakerCamera = SpeakerCamera;
})(typeof window !== "undefined" ? window : globalThis);
