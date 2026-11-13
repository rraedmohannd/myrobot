/* Shared /ws/robot client for the browser pages /voice and /robot (docs/PROTOCOL.md).

   It does what the Raspberry Pi client will do: WebSocket + heartbeats, microphone capture,
   energy VAD, PCM16 @ 16 kHz streaming, the mp3 playback queue and turn-taking (the microphone
   is deaf while the robot talks or thinks). Pages only render what it reports through events:

     conn      "connecting" | "open" | "closed" | "lost"
     message   every backend message (after the client handled it)
     state     dialogue state (IDLE, GREETING, LISTENING, ANSWERING, FOLLOW_UP, CLOSING)
     hint      "speak" | "hearing" | "thinking" | "robot" | ""
     level     microphone RMS of the last 30 ms frame
     play      {text, seq, last, tag} when a queued audio piece starts playing
     playidle  the playback queue became empty
     speechstart / speechend   VAD boundaries (used by the camera code)
*/
(function (global) {
  "use strict";
  const TARGET_RATE = 16000, FRAME_MS = 30;

  class RobotClient {
    constructor(options) {
      this.o = Object.assign({
        robotId: "laptop-browser",
        fwVersion: "robot-client-1",
        token: null,              // ROBOT_TOKEN, if the backend requires one
        autoReconnect: false,     // /robot reconnects by itself; /voice reconnects on the next click
        reconnectMs: 2000,
        heartbeatMs: 5000,
        // No message for this long = backend lost. A slow LLM call can keep the backend quiet for
        // a while (it answers heartbeats only between messages), so this is generous on purpose.
        lostAfterMs: 40000,
        sensitivity: () => 3,     // VAD threshold = noise floor x this
        audioEndExtra: () => ({}),// extra fields for audio.end (face_track_id, overlap)
        tagAudio: () => null,     // value attached to each queued audio piece (e.g. display mode)
      }, options || {});
      this.handlers = {};
      this.ws = null; this.ctx = null; this.node = null; this.stream = null; this.analyser = null;
      this.state = "IDLE"; this.followUpTimeout = 10; this.maxUtterance = 20;
      this.playing = false; this.queue = []; this.listenTimer = null;
      this.inSpeech = false; this.speechFrames = 0; this.silenceFrames = 0; this.voicedFrames = 0;
      this.preRoll = []; this.pending = []; this.frameBuf = new Float32Array(0);
      this.noise = 0.01; this.calib = 0;
      this.connecting = null; this.heartbeatTimer = null; this.lastSeen = 0; this.reconnectTimer = null;
    }

    on(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); return this; }
    emit(name, data) { for (const fn of this.handlers[name] || []) fn(data); }

    // ---------- connection ----------
    get connected() { return !!this.ws && this.ws.readyState === 1; }
    send(msg) { if (this.connected) this.ws.send(JSON.stringify(msg)); }

    connect() {
      if (this.connected) return Promise.resolve(true);
      if (this.connecting) return this.connecting;
      clearTimeout(this.reconnectTimer);
      this.emit("conn", "connecting");
      this.connecting = new Promise(resolve => {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const query = this.o.token ? `?token=${encodeURIComponent(this.o.token)}` : "";
        let ws;
        try { ws = new WebSocket(`${proto}://${location.host}/ws/robot${query}`); }
        catch (e) { this.connecting = null; this._closed(null, "closed"); resolve(false); return; }
        this.ws = ws;
        ws.onopen = () => {
          this.connecting = null; this.lastSeen = performance.now();
          this.emit("conn", "open");
          this.send({type: "hello", robot_id: this.o.robotId, fw_version: this.o.fwVersion, protocol_version: 1});
          this._startHeartbeat();
          resolve(true);
        };
        ws.onclose = () => { this._closed(ws, "closed"); resolve(false); };
        ws.onmessage = e => { this.lastSeen = performance.now(); this._onMessage(JSON.parse(e.data)); };
      });
      return this.connecting;
    }

    _closed(ws, why) {
      if (ws && this.ws !== ws) return;          // an older socket: already replaced
      if (ws) { ws.onopen = ws.onclose = ws.onmessage = null; try { ws.close(); } catch (e) { /* gone */ } }
      this.ws = null; this.connecting = null;
      clearInterval(this.heartbeatTimer); clearTimeout(this.listenTimer);
      // The backend closes the conversation when the socket drops, so the client starts clean too.
      this.state = "IDLE"; this._resetVad();
      this.emit("conn", why); this.emit("state", this.state); this.emit("hint", "");
      if (this.o.autoReconnect) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connect(), this.o.reconnectMs);
      }
    }

    _startHeartbeat() {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = setInterval(() => {
        if (!this.connected) return;
        if (performance.now() - this.lastSeen > this.o.lostAfterMs) { this._closed(this.ws, "lost"); return; }
        this.send({type: "heartbeat"});
      }, this.o.heartbeatMs);
    }

    // ---------- backend messages ----------
    _onMessage(m) {
      switch (m.type) {
        case "hello.ack": this.followUpTimeout = m.follow_up_timeout_s; this.maxUtterance = m.max_utterance_s; break;
        case "dialogue.state": this.state = m.state; this.emit("state", this.state); this._maybeListen(); break;
        case "answer.audio": {
          const bytes = Uint8Array.from(atob(m.mp3_b64), c => c.charCodeAt(0));
          this.queue.push({bytes, text: m.text, seq: m.seq, last: m.last, tag: this.o.tagAudio()});
          this._playNext(); break;
        }
        case "session.ended": clearTimeout(this.listenTimer); break;
      }
      this.emit("message", m);
    }

    // ---------- playback queue ----------
    _playNext() {
      if (this.playing || this.queue.length === 0) return;
      this.playing = true; this._resetVad();
      const item = this.queue.shift();
      const url = URL.createObjectURL(new Blob([item.bytes], {type: "audio/mpeg"}));
      const a = new Audio(url);
      this._toAnalyser(a);
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true; URL.revokeObjectURL(url); this.playing = false;
        if (this.queue.length) this._playNext(); else { this.emit("playidle"); this._maybeListen(); }
      };
      a.onended = done; a.onerror = done;
      this.emit("play", item);
      a.play().catch(done);
    }

    // Robot speech also goes through an analyser, so a page can move the mouth with the sound.
    _toAnalyser(audioElement) {
      if (!this.ctx || this.ctx.state !== "running") return;   // plain <audio> playback still works
      try {
        if (!this.analyser) {
          this.analyser = this.ctx.createAnalyser(); this.analyser.fftSize = 512;
          this.analyser.connect(this.ctx.destination);
          this.levelBuf = new Uint8Array(this.analyser.fftSize);
        }
        this.ctx.createMediaElementSource(audioElement).connect(this.analyser);
      } catch (e) { /* keep the direct output */ }
    }

    /** RMS (0..1) of the robot's own voice right now; 0 when silent or when no analyser exists. */
    outputLevel() {
      if (!this.analyser || !this.playing) return 0;
      this.analyser.getByteTimeDomainData(this.levelBuf);
      let sum = 0;
      for (let i = 0; i < this.levelBuf.length; i++) { const v = (this.levelBuf[i] - 128) / 128; sum += v * v; }
      return Math.sqrt(sum / this.levelBuf.length);
    }

    // ---------- turn-taking ----------
    canListen() { return (this.state === "LISTENING" || this.state === "FOLLOW_UP") && !this.playing && this.queue.length === 0; }

    _maybeListen() {
      clearTimeout(this.listenTimer);
      if (!this.canListen()) { this.emit("hint", this.playing ? "robot" : ""); return; }
      this.emit("hint", "speak");
      this._resetVad();
      if (this.state === "FOLLOW_UP") {
        this.listenTimer = setTimeout(() => {
          if (this.canListen() && !this.inSpeech) this.send({type: "listen.timeout"});
        }, this.followUpTimeout * 1000);
      }
    }

    _resetVad() { this.inSpeech = false; this.speechFrames = this.silenceFrames = this.voicedFrames = 0; this.preRoll = []; this.pending = []; }

    // ---------- microphone ----------
    /** An AudioContext without the microphone (enough for playback + analyser). Needs a user gesture. */
    async ensureContext() {
      if (!this.ctx) this.ctx = new AudioContext();
      if (this.ctx.state !== "running") { try { await this.ctx.resume(); } catch (e) { /* stays suspended */ } }
      return this.ctx;
    }

    async initAudio() {
      if (this.node) return;
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      await this.ensureContext();
      const code = `class Cap extends AudioWorkletProcessor { process(i){ const c=i[0][0]; if(c) this.port.postMessage(c.slice(0)); return true; } } registerProcessor('cap', Cap);`;
      await this.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code], {type: "application/javascript"})));
      this.node = new AudioWorkletNode(this.ctx, "cap");
      this.ctx.createMediaStreamSource(this.stream).connect(this.node);
      this.node.port.onmessage = e => this._onSamples(e.data);
    }

    _onSamples(f32) {
      const merged = new Float32Array(this.frameBuf.length + f32.length);
      merged.set(this.frameBuf); merged.set(f32, this.frameBuf.length);
      const size = Math.round(this.ctx.sampleRate * FRAME_MS / 1000);
      let off = 0;
      while (merged.length - off >= size) { this._onFrame(merged.subarray(off, off + size)); off += size; }
      this.frameBuf = merged.slice(off);
    }

    _downsample(frame) {
      const ratio = this.ctx.sampleRate / TARGET_RATE, n = Math.floor(frame.length / ratio), out = new Int16Array(n);
      for (let i = 0; i < n; i++) {
        let s = 0, a = Math.floor(i * ratio), b = Math.min(frame.length, Math.floor((i + 1) * ratio));
        for (let j = a; j < b; j++) s += frame[j];
        const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
        out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      return out;
    }

    _onFrame(frame) {
      let sum = 0; for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);
      this.emit("level", rms);
      if (!this.canListen()) return;                       // turn-taking: deaf while the robot talks/thinks
      if (!this.inSpeech && this.calib < 20) { this.noise = (this.noise * this.calib + rms) / (this.calib + 1); this.calib++; }
      const thr = Math.max(0.012, this.noise * this.o.sensitivity());
      const pcm = this._downsample(frame);
      if (!this.inSpeech) {
        this.preRoll.push(pcm); if (this.preRoll.length > 10) this.preRoll.shift();
        if (rms > thr) this.speechFrames++; else { this.speechFrames = 0; this.noise = this.noise * 0.95 + rms * 0.05; }
        if (this.speechFrames >= 4) {                       // ~120 ms of voice: speech started
          this.inSpeech = true; this.silenceFrames = 0; this.voicedFrames = 0; clearTimeout(this.listenTimer);
          this.pending = this.preRoll.slice(); this.preRoll = [];
          this.emit("hint", "hearing"); this.emit("speechstart");
        }
        return;
      }
      this.pending.push(pcm); this.voicedFrames++;
      if (rms > thr) this.silenceFrames = 0; else this.silenceFrames++;
      if (this.pending.length >= 3) this._flush();
      if (this.silenceFrames >= 30 || this.voicedFrames * FRAME_MS > this.maxUtterance * 1000) {   // ~0.9 s silence: done
        this._flush(); this.inSpeech = false;
        this.emit("speechend");
        this.send(Object.assign({type: "audio.end"}, this.o.audioEndExtra() || {}));
        this.state = "ANSWERING"; this.emit("hint", "thinking");
      }
    }

    _flush() {
      if (!this.pending.length) return;
      const total = this.pending.reduce((n, a) => n + a.length, 0), all = new Int16Array(total);
      let o = 0; for (const a of this.pending) { all.set(a, o); o += a.length; }
      this.pending = [];
      const bytes = new Uint8Array(all.buffer); let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      this.send({type: "audio.chunk", seq: Date.now(), sample_rate: TARGET_RATE, pcm16_b64: btoa(bin)});
    }

    // ---------- conversation controls ----------
    async startSession(lang) { if (await this.connect()) this.send({type: "session.start", lang: lang || "ar"}); }
    endSession(reason) { this.send({type: "session.end", reason: reason || "user_done"}); }
    async sendText(text) { if (await this.connect()) this.send({type: "text.input", text}); }
  }

  global.RobotClient = RobotClient;
})(window);
