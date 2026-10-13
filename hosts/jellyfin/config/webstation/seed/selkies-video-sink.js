/* Selkies video sink (homelab-infra: hosts/jellyfin/config/webstation/seed).
 *
 * The Selkies 2.0.0 client chooses where to decode and show full-frame video (H.264 by
 * default) mainly from typeof MediaStreamTrackGenerator, read once when its bundle
 * loads (the worker's VideoTrackGenerator check and offscreen_worker also count).
 * - Chromium browsers have it, so the client decodes on the page's main thread. If
 *   that decoder backs up past 8 queued frames, it drops everything until a requested
 *   key frame arrives. Decoded frames go to a MediaStreamTrackGenerator feeding a
 *   hidden <video>.
 * - Firefox lacks it, so the client decodes in its video worker. The worker's
 *   DecodeGate reports lost frames instead, and asks for a key frame only after 1 s.
 *   Frames are drawn to an OffscreenCanvas.
 * On 2026-10-12 the same games froze for about a second at a time in Chrome and Brave,
 * while the bytes kept arriving and the page kept acknowledging frames. In Firefox
 * they froze much less. This script loads before the bundle and hides the generator,
 * so Chromium takes the worker path too: the same server stream and the same WebCodecs
 * (GPU) decoder, but a different decode thread, backlog policy and sink. Whether that
 * removes the freezes in Chrome is the experiment.
 *
 * Check the path in use from the client's own log line, "[Selkies] video sink: ...".
 * "OffscreenCanvas in the video worker" means the switch took effect. If the worker
 * fails, Chromium now falls back to the 2D canvas on the page, not to the <video>.
 * Upstream bug, unchanged here: a shared viewer (#shared, #playerN, a viewer token) on
 * the worker path resends START_VIDEO after a tab hide/show; Firefox already does this.
 *
 * To compare the two paths in one browser, run this in the stream frame's console
 * (DevTools context picker: the Selkies frame, not "top") and reload:
 *   localStorage.setItem("homelab.videoSink", "mstg")   // the client's own <video> path
 *   localStorage.removeItem("homelab.videoSink")        // back to the worker
 */
(() => {
  "use strict";
  if (typeof window === "undefined" || !("MediaStreamTrackGenerator" in window)) return;
  let keep = false;
  try {
    keep = window.localStorage.getItem("homelab.videoSink") === "mstg";
  } catch (e) {
    // Storage blocked: take the default.
  }
  if (keep) {
    console.info("[video-sink] homelab.videoSink=mstg: leaving MediaStreamTrackGenerator in place");
    return;
  }
  try {
    delete window.MediaStreamTrackGenerator;
  } catch (e) {
    // Not configurable in this browser; the check below reports it.
  }
  console.info(typeof window.MediaStreamTrackGenerator === "undefined"
    ? "[video-sink] MediaStreamTrackGenerator hidden: the client should use its video worker"
    : "[video-sink] could not hide MediaStreamTrackGenerator; the client keeps its page decoder and <video>");
})();
