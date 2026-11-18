/* Injected by robotd as the FIRST script of the robot page it serves (robot mode).

   Camera ownership (sprint 5 default 13): on the robot, visiond owns the webcam. In robot mode the
   page never opens the camera: this guard runs before any page script and refuses every
   getUserMedia request that asks for video. Audio (the microphone for the conversation) passes.

   Server mode (sprint 6 P0.7): this page is served over http://localhost, so the page's client
   builds ws://<SCR_BACKEND>/ws/robot. When robotd's backend address is wss:// it sets
   window.SCR_BACKEND_SECURE (after this script, read at call time) and only that one address is
   upgraded to wss:// here; every other socket (the local ws://localhost/ws/ui) is untouched. */
(function (global) {
  "use strict";
  const Native = global.WebSocket;
  if (typeof Native !== "function" || Native.__scrSecure) return;
  function ScrWebSocket(url, protocols) {
    const host = global.SCR_BACKEND;
    if (global.SCR_BACKEND_SECURE === true && host && typeof url === "string" &&
        url.indexOf("ws://" + host + "/") === 0) {
      url = "wss://" + url.slice(5);
    }
    return protocols === undefined ? new Native(url) : new Native(url, protocols);
  }
  ScrWebSocket.prototype = Native.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(k => { ScrWebSocket[k] = Native[k]; });
  ScrWebSocket.__scrSecure = true;
  global.WebSocket = ScrWebSocket;
})(typeof window !== "undefined" ? window : globalThis);

(function (global) {
  "use strict";
  global.SCR_ROBOT_MODE = true;
  const md = global.navigator && global.navigator.mediaDevices;
  if (!md || typeof md.getUserMedia !== "function" || md.__scrGuarded) return;
  const original = md.getUserMedia.bind(md);
  md.getUserMedia = function (constraints) {
    if (constraints && constraints.video) {
      console.warn("robot mode: the camera belongs to visiond; the page does not open it");
      const err = new Error("camera disabled in robot mode (visiond owns the webcam)");
      err.name = "NotAllowedError";
      return Promise.reject(err);
    }
    return original(constraints);
  };
  md.__scrGuarded = true;
})(typeof window !== "undefined" ? window : globalThis);
