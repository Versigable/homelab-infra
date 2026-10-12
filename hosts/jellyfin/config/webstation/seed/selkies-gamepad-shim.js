/* Selkies gamepad standardizer (homelab-infra: hosts/jellyfin/config/webstation/seed).
 *
 * Chrome reports some controllers with `mapping: ""` (no standard layout). For those,
 * the Selkies client looks up jsdb/<os>/<vid>-<pid>.json, but that path drops raw
 * axes 4 and 5, turns them into the d-pad, and ignores hats. A controller whose
 * right stick sits on axis 5 and whose d-pad is a POV hat (PowerA's GameCube-style
 * pad) ends up with its C-stick driving the d-pad and no d-pad at all.
 *
 * This script loads before the Selkies bundle and rewrites known controllers into
 * the W3C standard layout, in both navigator.getGamepads() and the
 * gamepadconnected event, so Selkies handles them like any Xbox-layout pad.
 * Pads without a profile here pass through untouched.
 *
 * A profile is keyed "<os>-chrome:<vid>-<pid>" and maps each standard index to a raw
 * index. Raw indices differ by OS and browser, so add a profile per combination,
 * probed with navigator.getGamepads() on that machine.
 */
(() => {
  "use strict";
  if (typeof navigator === "undefined" || typeof navigator.getGamepads !== "function") return;

  // Standard layout: 0 A(bottom) 1 B(right) 2 X(left) 3 Y(top) 4 LB 5 RB 6 LT 7 RT
  // 8 Back 9 Start 10 LS 11 RS 12-15 d-pad up/down/left/right 16 Guide.
  // Axes: 0 LX, 1 LY, 2 RX, 3 RY.
  const PROFILES = {
    // PowerA "Core (Plus) Wired Controller", GameCube layout (20d6:a711). Probed 2026-10-12.
    // Raw: Y b0, B b1, A b2, X b3, ZL b4, Z b5, L b6, R b7, - b8, + b9, LS b10, RS b11,
    // Home b12, Capture b13; main stick a0/a1, C-stick a2/a5, d-pad = POV hat on a9.
    // Face buttons go by position (GameCube A, the big one, is standard A), which is
    // the layout the webstation's Dolphin GCPad map expects: A<-S, B<-W, X<-E, Y<-N,
    // Z<-RB, L/R<-LT/RT.
    "windows-chrome:20d6-a711": {
      name: "PowerA GameCube-style wired",
      buttons: { 0: 2, 1: 3, 2: 1, 3: 0, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 16: 12 },
      axes: [0, 1, 2, 5],
      hat: 9,
    },
  };

  const ua = navigator.userAgent || "";
  const os = /Windows/i.test(ua) ? "windows" : /Android/i.test(ua) ? "android"
    : /Macintosh|Mac OS X/i.test(ua) ? "mac" : "linux";
  const browser = /Chrome\//.test(ua) ? "chrome" : /Firefox\//.test(ua) ? "firefox" : "other";
  const ID_RE = /Vendor: ([0-9a-f]{4}) Product: ([0-9a-f]{4})/i; // Chromium's id format
  const announced = new Set();

  function profileFor(g) {
    if (!g || g.mapping === "standard") return null;
    const m = ID_RE.exec(g.id || "");
    if (!m) return null;
    const p = PROFILES[`${os}-${browser}:${m[1].toLowerCase()}-${m[2].toLowerCase()}`];
    if (p && !announced.has(g.index + g.id)) {
      announced.add(g.index + g.id);
      console.info(`[pad-shim] pad ${g.index}: "${g.id}" -> standard layout (${p.name})`);
    }
    return p || null;
  }

  // Chromium encodes a POV hat as -1 + dir * 2/7 (dir 0 = up, clockwise to 7 = up-left)
  // and reports about 3.29 when centred.
  function hat(v) {
    if (typeof v !== "number" || v > 1.01 || v < -1.01) return [false, false, false, false];
    const d = Math.round((v + 1) * 3.5) & 7;
    return [d === 7 || d <= 1, d >= 3 && d <= 5, d >= 5, d >= 1 && d <= 3]; // up, down, left, right
  }

  const UP = Object.freeze({ pressed: false, touched: false, value: 0 });
  const DOWN = Object.freeze({ pressed: true, touched: true, value: 1 });

  function standardize(g) {
    const p = profileFor(g);
    if (!p) return g;
    const buttons = [];
    for (let s = 0; s < 17; s++) {
      const b = p.buttons[s] === undefined ? null : g.buttons[p.buttons[s]];
      buttons.push(b ? { pressed: b.pressed, touched: b.touched, value: b.value } : UP);
    }
    if (p.hat !== undefined) {
      hat(g.axes[p.hat]).forEach((on, k) => { if (on) buttons[12 + k] = DOWN; });
    }
    return {
      id: g.id,
      index: g.index,
      connected: g.connected,
      timestamp: g.timestamp,
      mapping: "standard",
      axes: p.axes.map((i) => (typeof g.axes[i] === "number" ? g.axes[i] : 0)),
      buttons,
      vibrationActuator: g.vibrationActuator || null,
      hapticActuators: g.hapticActuators || [],
    };
  }

  const rawGetGamepads = navigator.getGamepads.bind(navigator);
  navigator.getGamepads = function getGamepads() {
    return Array.from(rawGetGamepads(), (g) => (g ? standardize(g) : g));
  };

  // Selkies announces a pad (id, axis and button counts) from this event's gamepad,
  // so re-issue it with the standardized one. Registered before the bundle loads,
  // so it runs ahead of Selkies' own listener.
  window.addEventListener("gamepadconnected", (ev) => {
    if (ev.padShim) return;
    const std = standardize(ev.gamepad);
    if (std === ev.gamepad) return;
    ev.stopImmediatePropagation();
    const re = new Event("gamepadconnected");
    Object.defineProperty(re, "gamepad", { value: std });
    Object.defineProperty(re, "padShim", { value: true });
    window.dispatchEvent(re);
  }, true);

  window.__padShim = { profiles: PROFILES, key: `${os}-${browser}`, standardize };
})();
