/* Injected by robotd into the robot page it serves on http://localhost (never by the backend).

   Local WebSocket /ws/ui between the page and robotd:
     robotd -> page  local.engage                      start the conversation (as if Start was pressed)
                     local.vision {face_track_id, overlap, engaged, faces}
                     local.state  {state, backend_ok, is_simulated}
     page -> robotd  local.vad {speaking}              the page's VAD (for visiond)
                     local.conversation {active}       a conversation is running

   No image ever passes here: visiond sends only ids and flags (docs/SPEAKERS.md). */
(function () {
  "use strict";
  const page = typeof client !== "undefined" ? client : null;   // the page's RobotClient
  let ws = null, active = false, speaking = false;
  let visionAt = -Infinity, heard = {id: null, overlap: false};

  // ---- badge: SIMULATED label (CLAUDE.md rule 5) and the robot state ----
  const badge = document.createElement("div");
  badge.id = "localBadge";
  badge.style.cssText = "position:fixed;top:8px;left:8px;z-index:9999;font:700 14px system-ui,sans-serif;" +
    "padding:4px 12px;border-radius:999px;color:#fff;background:#555;opacity:.92;pointer-events:none;direction:rtl";
  badge.hidden = true;
  document.body.appendChild(badge);
  function showState(m) {
    if (!m) { badge.hidden = false; badge.style.background = "#555"; badge.textContent = "الروبوت مش متصل · robotd offline"; return; }
    const parts = [];
    if (m.is_simulated) parts.push("محاكاة · SIMULATED");
    if (!m.backend_ok) parts.push("الخدمة مش متاحة · service unavailable");
    parts.push(m.state);
    badge.textContent = parts.join(" — ");
    badge.style.background = m.is_simulated ? "#b45309" : (m.backend_ok ? "#1f6f43" : "#8a1c1c");
    badge.hidden = false;
    document.body.dataset.simulated = m.is_simulated ? "1" : "0";
  }

  function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }
  function setActive(value) { active = value; send({type: "local.conversation", active}); }

  function handle(m) {
    switch (m.type) {
      case "local.engage":
        // The same path as the Start button. Chromium kiosk needs
        // --autoplay-policy=no-user-gesture-required for sound without a first touch.
        if (typeof start === "function") start();
        break;
      case "local.vision":
        visionAt = performance.now();
        if (speaking) {
          if (m.face_track_id !== null && m.face_track_id !== undefined) heard.id = m.face_track_id;
          if (m.overlap) heard.overlap = true;
        }
        break;
      case "local.state": showState(m); break;
    }
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/ui`);
    ws.onopen = () => send({type: "local.conversation", active});
    ws.onmessage = e => { try { handle(JSON.parse(e.data)); } catch (err) { console.warn("local message", err); } };
    ws.onclose = () => { ws = null; showState(null); setTimeout(connect, 1000); };
  }

  if (page) {
    page.on("speechstart", () => { speaking = true; heard = {id: null, overlap: false}; send({type: "local.vad", speaking: true}); });
    page.on("speechend", () => { speaking = false; send({type: "local.vad", speaking: false}); });
    page.on("message", m => {
      if (m.type === "session.started") setActive(true);
      else if (m.type === "session.ended") setActive(false);
    });
    page.on("conn", state => { if (state !== "open" && state !== "connecting" && active) setActive(false); });
  }

  // audio.end: use visiond's speaker while it is reporting; otherwise the page's own camera.
  // Robot mode: visiond owns the webcam; the page's camera must not run (robot-guard.js refuses
  // video anyway; this stops one that was somehow started).
  const own = window.speakerCamera || null;
  if (own && own.running && typeof own.stop === "function") own.stop();
  window.speakerCamera = {
    audioEndFields() {
      if (performance.now() - visionAt < 1500) {
        const out = {overlap: heard.overlap};
        if (heard.id !== null) out.face_track_id = heard.id;
        return out;
      }
      return own ? own.audioEndFields() : {};
    },
  };

  connect();
})();
