/* Injected by robotd as the FIRST script of the robot page it serves (robot mode).

   Camera ownership (sprint 5 default 13): on the robot, visiond owns the webcam. In robot mode the
   page never opens the camera: this guard runs before any page script and refuses every
   getUserMedia request that asks for video. Audio (the microphone for the conversation) passes. */
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
