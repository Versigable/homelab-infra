/* Selkies WebRTC volume (homelab-infra: hosts/jellyfin/config/webstation/seed).
 *
 * RomM's stream page sets the volume by posting {type: "setVolume", value: 0..1} and
 * {type: "setMute", value: bool} into this frame. Selkies 2.0.0 applies both only to
 * the GainNode of its websockets audio pipeline (AudioWorklet -> GainNode -> speakers).
 * In WebRTC mode the audio arrives as an RTCPeerConnection track and plays through
 * the <video> element that _ontrack gives the stream to, so the slider did nothing
 * (noticed 2026-11-07, after the move to WebRTC).
 *
 * This listens for the same two messages and applies them to every media element
 * whose stream carries an audio track, with Selkies' own semantics: setVolume always
 * applies (and so ends a mute, as Selkies' gain update does), setMute(true) silences,
 * setMute(false) restores the last volume. (A first version held the volume at 0
 * while muted; RomM can mute at volume 0 and not unmute on the way back up, so the
 * slider stayed silent until mute was toggled.) Mute is volume 0, never .muted: unmuting by
 * script, without a gesture in this frame, can make Chromium pause autoplayed video.
 * It re-applies every 500 ms, so a stream that connects (or reconnects) later gets the
 * setting too. In websockets mode no media element carries audio, so it does nothing.
 */
(function () {
  if (window.__homelabWebrtcVolume) return;
  window.__homelabWebrtcVolume = true;
  var volume = null;   // last setVolume, 0..1; null until RomM sends one
  var muted = false;   // last setMute

  function apply() {
    if (volume === null && !muted) return;
    var target = muted ? 0 : volume;
    var els = document.querySelectorAll("video, audio");
    for (var i = 0; i < els.length; i++) {
      var s = els[i].srcObject;
      if (!s || typeof s.getAudioTracks !== "function" || s.getAudioTracks().length === 0) continue;
      if (Math.abs(els[i].volume - target) > 0.001) els[i].volume = target;
    }
  }

  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || typeof d !== "object") return;
    if (d.type === "setVolume" && typeof d.value === "number" && isFinite(d.value)) {
      volume = Math.max(0, Math.min(1, d.value));
      muted = false;  // Selkies sets the gain to the new volume whether muted or not
      apply();
    } else if (d.type === "setMute" && typeof d.value === "boolean") {
      muted = d.value;
      if (!muted && volume === null) volume = 1;
      apply();
    }
  });

  setInterval(apply, 500);
})();
