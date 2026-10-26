/* Selkies playout buffer (homelab-infra: hosts/jellyfin/config/webstation/seed).
 *
 * The Selkies client's video worker draws each decoded frame the moment the decoder
 * hands it over (present() in the worker source), so any bunching on the way here (the
 * WebSocket over Cloudflare's tunnel, a TLS-scanning antivirus, a busy main thread)
 * shows up on screen as a hitch, even when the server sends a steady 60 fps. On
 * 2026-10-12 about 77% of the output jitter was already present when frames arrived,
 * and a simulated 12 ms buffer cut the gaps over 28 ms from 190 to 12 per 20 s.
 *
 * This script loads ahead of the client's bundle and wraps Blob. When the client builds
 * its video worker from source (one string, recognised by a comment in it), the worker
 * patch below is prepended. In the worker it subclasses VideoDecoder: each decoded frame
 * gets a due time one measured frame period after the previous one, held about
 * homelab.playoutMs behind its arrival, and frames are drawn on the display's refresh
 * (requestAnimationFrame in the worker). A late frame, with the buffer empty, is shown
 * at once and the buffer then grows back gradually. After a burst the clock drops back
 * to the target instead of letting latency pile up. At most 6 decoded frames are held,
 * because hardware decoders have small frame pools.
 *
 * Costs about homelab.playoutMs of latency. The stats panel's decode time includes it.
 * Covers the worker path only: Firefox, and Chromium with selkies-video-sink.js (the
 * default here). The client's own page decoder (homelab.videoSink=mstg) is untouched.
 *
 * To tune or turn it off, run this in the stream frame's console (DevTools context
 * picker: the Selkies frame, not "top") and reload:
 *   localStorage.setItem("homelab.playoutMs", "33")   // two frames at 60 fps
 *   localStorage.setItem("homelab.playoutMs", "0")    // off: draw frames as they arrive
 *   localStorage.removeItem("homelab.playoutMs")      // back to the default (17 ms)
 * The client logs "[playout] ..." in the stream frame's console when it applies.
 */
(() => {
  "use strict";
  const DEFAULT_MS = 17;
  const SIGNATURE = "Video sink and optional in-worker decoder";
  let ms = DEFAULT_MS;
  try {
    const v = window.localStorage.getItem("homelab.playoutMs");
    if (v !== null && v.trim() !== "" && Number.isFinite(Number(v))) ms = Math.max(0, Math.min(200, Number(v)));
  } catch (e) {
    // Storage blocked: take the default.
  }
  if (!(ms > 0)) {
    console.info("[playout] homelab.playoutMs=0: frames are drawn as they arrive");
    return;
  }
  if (typeof window.Blob !== "function" || typeof Proxy !== "function") return;
  const prefix = "(" + workerPlayout.toString() + ")(" + ms + ");\n";
  let logged = false;
  window.Blob = new Proxy(window.Blob, {
    construct(Target, args, newTarget) {
      const parts = args[0];
      if (Array.isArray(parts) && parts.length === 1 && typeof parts[0] === "string"
          && parts[0].indexOf(SIGNATURE) !== -1) {
        args = [[prefix + parts[0]]].concat(Array.prototype.slice.call(args, 1));
        if (!logged) {
          logged = true;
          console.info("[playout] video worker patched: frames are held about " + ms + " ms and drawn on the display's refresh");
        }
      }
      return Reflect.construct(Target, args, newTarget);
    },
  });

  // Runs inside the video worker, ahead of the client's own worker code. Self-contained:
  // it's serialised with toString(), so it can't use anything from this page script.
  function workerPlayout(targetMs) {
    "use strict";
    if (!(targetMs > 0) || typeof VideoDecoder === "undefined") return;
    const NativeDecoder = VideoDecoder;
    const now = () => performance.now();
    const onRefresh = typeof requestAnimationFrame === "function"
      ? (cb) => requestAnimationFrame(cb)
      : (cb) => setTimeout(cb, 2);
    const MAX_HELD = 6;  // decoded frames held at most
    const WINDOW = 120;  // arrivals averaged for the frame period (2 s at 60 fps)
    const SLACK = 2;     // ms: a frame due this close to the refresh is drawn on it

    class PlayoutDecoder extends NativeDecoder {
      constructor(init) {
        const output = init.output;
        const held = [];      // [{ frame, due }], oldest first
        const arrivals = [];
        let lastDue = -Infinity;
        let ticking = false;
        const tick = () => {
          const t = now();
          let n = 0;
          while (n < held.length && held[n].due <= t + SLACK) n++;
          if (n > 0) {
            // Frames overtaken by a newer due one would only add lag: drop them.
            for (let i = 0; i < n - 1; i++) held[i].frame.close();
            const frame = held[n - 1].frame;
            held.splice(0, n);
            output(frame);
          }
          if (held.length) onRefresh(tick);
          else ticking = false;
        };
        super(Object.assign({}, init, {
          output: (frame) => {
            const t = now();
            arrivals.push(t);
            if (arrivals.length > WINDOW) arrivals.shift();
            const period = arrivals.length > 10
              ? Math.min(100, Math.max(4, (t - arrivals[0]) / (arrivals.length - 1)))
              : 1000 / 60;
            let due = lastDue + period;
            const lag = due - t;
            if (!Number.isFinite(due) || lag > targetMs + 2 * period) due = t + targetMs; // start, or too deep after a burst
            else if (lag < 0) due = t;                                                     // late: the buffer ran dry
            else due -= (lag - targetMs) * 0.1;                                            // ease toward the target
            lastDue = due;
            held.push({ frame: frame, due: due });
            while (held.length > MAX_HELD) held.shift().frame.close();
            if (!ticking) {
              ticking = true;
              onRefresh(tick);
            }
          },
        }));
        this._playoutFlush = () => {
          while (held.length) held.shift().frame.close();
          lastDue = -Infinity;
        };
      }
      reset() {
        this._playoutFlush();
        return super.reset();
      }
      close() {
        this._playoutFlush();
        return super.close();
      }
    }
    self.VideoDecoder = PlayoutDecoder;
  }
})();
