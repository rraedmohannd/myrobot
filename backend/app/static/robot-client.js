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
        protocolVersion: 2,       // shared/scr_protocol; version 1 clients are still accepted
        audioStream: true,        // accept answer speech as PCM chunks (answer.audio.start/chunk/end)
      }, options || {});
      this.handlers = {};
      this.ws = null; this.ctx = null; this.node = null; this.stream = null; this.analyser = null;
      this.state = "IDLE"; this.followUpTimeout = 10; this.maxUtterance = 20;
      this.speechRate = 1; this.silenceFramesEnd = 30;   // both come from hello.ack (SPEECH_RATE, VAD_SILENCE_MS)
      this.audio = null; this.current = null; this.openStream = null;
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
        // robotd's local UI server serves this page from the robot itself (plain http) and sets
        // window.SCR_BACKEND ("host:port" of the backend), window.SCR_ROBOT_TOKEN and, for a
        // backend behind HTTPS (server mode), window.SCR_BACKEND_SECURE = true (then wss://).
        // Without them (pages served by the backend) nothing changes: same host, token from the
        // options, wss:// when the page itself is https (the visitor cookie goes with it).
        const proto = global.SCR_BACKEND_SECURE ? "wss" : (location.protocol === "https:" ? "wss" : "ws");
        const host = global.SCR_BACKEND || location.host;
        const token = this.o.token || global.SCR_ROBOT_TOKEN || null;
        const query = token ? `?token=${encodeURIComponent(token)}` : "";
        let ws;
        try { ws = new WebSocket(`${proto}://${host}/ws/robot${query}`); }
        catch (e) { this.connecting = null; this._closed(null, "closed"); resolve(false); return; }
        this.ws = ws;
        ws.onopen = () => {
          this.connecting = null; this.lastSeen = performance.now();
          this.emit("conn", "open");
          this.send({type: "hello", robot_id: this.o.robotId, fw_version: this.o.fwVersion, protocol_version: this.o.protocolVersion,
                     audio_stream: this.o.audioStream ? ["pcm16"] : []});
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
      if (this.openStream) { this.openStream.ended = true; if (this.openStream.feed) this.openStream.feed(); this.openStream = null; }
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
        case "hello.ack":
          this.followUpTimeout = m.follow_up_timeout_s; this.maxUtterance = m.max_utterance_s;
          if (m.speech_rate > 0) this.speechRate = m.speech_rate;
          if (m.vad_silence_ms > 0) this.silenceFramesEnd = Math.max(5, Math.round(m.vad_silence_ms / FRAME_MS));
          break;
        case "dialogue.state": this.state = m.state; this.emit("state", this.state); this._maybeListen(); break;
        case "answer.audio": {
          const bytes = Uint8Array.from(atob(m.mp3_b64), c => c.charCodeAt(0));
          this.queue.push({bytes, text: m.text, seq: m.seq, last: m.last, tag: this.o.tagAudio(), segment: m.segment, kind: m.kind});
          this._playNext(); break;
        }
        case "answer.audio.start": {
          const item = {stream: true, text: m.text, seq: m.seq, last: false, tag: this.o.tagAudio(),
                        sampleRate: m.sample_rate || 24000, pending: [], ended: false, stopped: false, feed: null};
          this.openStream = item; this.queue.push(item); this._playNext(); break;
        }
        case "answer.audio.chunk": {
          const item = this.openStream;
          if (item && item.seq === m.seq && !item.stopped) {
            item.pending.push(Uint8Array.from(atob(m.pcm16_b64), c => c.charCodeAt(0)));
            if (item.feed) item.feed();
          }
          break;
        }
        case "answer.audio.end": {
          const item = this.openStream;
          if (item && item.seq === m.seq) { item.ended = true; item.last = m.last; this.openStream = null; if (item.feed) item.feed(); }
          break;
        }
        case "session.ended": clearTimeout(this.listenTimer); break;
      }
      this.emit("message", m);
    }

    // ---------- playback queue ----------
    // Two kinds of items: a whole mp3 file (`answer.audio`: cached phrases, explainer segments)
    // and a PCM stream (`answer.audio.start/chunk/end`: fresh answers, played while the backend
    // is still receiving them from text-to-speech).
    _playNext() {
      if (this.playing || this.queue.length === 0) return;
      this.playing = true; this._resetVad();
      const item = this.queue.shift();
      this.current = item;
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true; this.playing = false; this.current = null; this.audio = null;
        if (this.queue.length) this._playNext(); else { this.emit("playidle"); this._maybeListen(); }
      };
      item.finish = done;
      this.emit("play", item);
      if (item.stream) this._playStream(item, done);
      else this._playFile(item, item.bytes, isWav(item.bytes) ? "audio/wav" : "audio/mpeg", done);
    }

    _playFile(item, bytes, mime, done) {
      const url = URL.createObjectURL(new Blob([bytes], {type: mime}));
      const a = new Audio(url);
      // Speaking pace: the same rate on every page (and later on the Pi), with the pitch kept.
      a.preservesPitch = true; a.defaultPlaybackRate = this.speechRate; a.playbackRate = this.speechRate;
      this.audio = a;
      this._toAnalyser(a);
      const end = () => { URL.revokeObjectURL(url); done(); };
      item.stop = () => { try { a.pause(); } catch (e) { /* already stopped */ } end(); };
      a.onended = end; a.onerror = end;
      a.play().catch(end);
    }

    _playStream(item, done) {
      const ctx = this.ctx;
      if (!ctx || ctx.state !== "running") {
        // No running AudioContext (no user gesture yet): play the piece as one WAV when it is complete.
        item.feed = () => {
          if (!item.ended || item.stopped) return;
          item.feed = null;
          this._playFile(item, pcmToWav(item.pending, item.sampleRate), "audio/wav", done);
        };
        item.stop = () => { item.stopped = true; done(); };
        item.feed();
        return;
      }
      const out = this._analyserNode() || ctx.destination;
      const nodes = [];
      let nextTime = 0, active = 0, buffered = 0, started = false;
      const finishIfDone = () => { if (item.ended && item.pending.length === 0 && active === 0) done(); };
      item.stop = () => { item.stopped = true; item.pending = []; for (const n of nodes) { try { n.onended = null; n.stop(); } catch (e) { /* not started */ } } done(); };
      item.feed = () => {
        if (item.stopped) return;
        // Wait for about 0.2 s of audio before starting, so the first words do not stutter.
        if (!started) {
          buffered = item.pending.reduce((n, c) => n + c.length, 0);
          if (buffered < item.sampleRate * 2 * 0.2 && !item.ended) return;
          started = true;
        }
        while (item.pending.length) {
          const pcm = item.pending.shift(), n = pcm.length >> 1;
          if (!n) continue;
          const buf = ctx.createBuffer(1, n, item.sampleRate), ch = buf.getChannelData(0);
          const view = new DataView(pcm.buffer, pcm.byteOffset, n * 2);
          for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
          const src = ctx.createBufferSource();
          src.buffer = buf; src.playbackRate.value = this.speechRate;   // (a buffer source cannot keep the pitch)
          src.connect(out);
          const t = Math.max(ctx.currentTime + 0.02, nextTime);
          src.start(t); nextTime = t + buf.duration / this.speechRate;
          active++; nodes.push(src);
          src.onended = () => { active--; finishIfDone(); };
        }
        finishIfDone();
      };
      item.feed();
    }

    /** Stop the robot's speech now and drop everything that is queued (stop button). */
    stopPlayback() {
      this.queue = [];
      if (this.openStream) { this.openStream.stopped = true; }
      if (this.current && this.current.stop) this.current.stop();
    }
    /** Stop only the piece that is playing; the next queued piece starts (skip button). */
    skipCurrent() { if (this.current && this.current.stop) this.current.stop(); }
    /** 0..1 position inside the mp3 piece that is playing (null for streams or when unknown). */
    playProgress() {
      const a = this.audio;
      return a && a.duration > 0 && isFinite(a.duration) ? Math.min(1, a.currentTime / a.duration) : null;
    }
    /** Seconds since the current piece started (for animations that need a clock). */
    playSeconds() { const a = this.audio; return a ? a.currentTime / (this.speechRate || 1) : null; }

    _analyserNode() {
      if (!this.ctx || this.ctx.state !== "running") return null;
      if (!this.analyser) {
        this.analyser = this.ctx.createAnalyser(); this.analyser.fftSize = 512;
        this.analyser.connect(this.ctx.destination);
        this.levelBuf = new Uint8Array(this.analyser.fftSize);
      }
      return this.analyser;
    }

    // Robot speech also goes through an analyser, so a page can move the mouth with the sound.
    _toAnalyser(audioElement) {
      const analyser = this._analyserNode();
      if (!analyser) return;                                   // plain <audio> playback still works
      try { this.ctx.createMediaElementSource(audioElement).connect(analyser); }
      catch (e) { /* keep the direct output */ }
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
      if (this.silenceFrames >= this.silenceFramesEnd || this.voicedFrames * FRAME_MS > this.maxUtterance * 1000) {   // enough silence (VAD_SILENCE_MS): done
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

  const isWav = b => b.length > 12 && b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46;   // "RIFF" (test audio)

  // PCM16 mono chunks -> one WAV file (fallback when WebAudio is not running).
  function pcmToWav(chunks, sampleRate) {
    const size = chunks.reduce((n, c) => n + c.length, 0), out = new Uint8Array(44 + size), v = new DataView(out.buffer);
    const tag = (o, t) => { for (let i = 0; i < 4; i++) out[o + i] = t.charCodeAt(i); };
    tag(0, "RIFF"); v.setUint32(4, 36 + size, true); tag(8, "WAVE"); tag(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); tag(36, "data"); v.setUint32(40, size, true);
    let o = 44; for (const c of chunks) { out.set(c, o); o += c.length; }
    return out;
  }

  global.RobotClient = RobotClient;
})(window);
