"""studio watch: an agent looks at a recorded play session (vault notes/step-3c-watch-plan).

A model shouldn't watch every frame of a recording. It reads what happened as text (the
session's facts, the Studio Kit's timeline, the numbers tuned on the Stage, the player log's
warnings and errors, each with its time on the recording) and asks for pictures only for the
moments it has to see: a filmstrip of a stretch, or one frame.

Two halves. studio-agentd (root) asks the hub for the sessions of this cell's builds, fetches a
session's files from the depot into /srv/studio/_watch/<game>/<session>/, which jobs can read
and can't write, and builds the text. The `studio` CLI, as the caller and inside its sandbox,
cuts the pictures with ffmpeg into the worktree's Logs/watch/ (git-ignored): no root process
ever decodes a video that a build produced.
"""

import datetime as dt
import os
import re
import subprocess
import time

from . import common
from . import hub as hubmod

WATCH_ROOT = os.path.join(common.STUDIO_ROOT, "_watch")
SID_RE = re.compile(r"^[0-9a-f]{16}$")
KEEP_DAYS = 7
# A player log line with -timestamps: 2026-11-27T23:10:02.108Z|0x7fa5a1b7d7c0|text
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z\|[^|]*\|(.*)$")
# Whole words: Unity lists its GL extensions on one line, GL_KHR_no_error among them.
NOTEWORTHY = re.compile(r"\b(errors?|\w*exception|warnings?|failed|assert\w*|crash\w*|out of memory)\b", re.I)
MAX_MOMENTS, MAX_LINE = 200, 300
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
MAX_FRAMES, STRIP_COLUMNS = 16, 4


def clock(seconds):
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def parse_time(text):
    """4:10, 1:02:03 or 250 (seconds) as seconds. ValueError if it's none of them."""
    parts = str(text).split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"{text!r} isn't a time (4:10, 1:02:03 or seconds)")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def log_moments(raw, start):
    """The player log's warnings and errors, each placed on the recording's clock when the line
    carries a time and the recording's start is known. At most 200, each cut to 300 characters."""
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        m = LOG_LINE.match(line)
        text = m.group(2) if m else line
        if not NOTEWORTHY.search(text):
            continue
        at = None
        if m and start:
            try:
                t = dt.datetime.fromisoformat(m.group(1)).replace(tzinfo=dt.timezone.utc).timestamp() - start
                at = clock(t) if t >= 0 else "before the recording"
            except ValueError:
                pass
        out.append({"at": at, "line": text.strip()[:MAX_LINE]})
        if len(out) >= MAX_MOMENTS:
            break
    return out


def timeline_events(raw, start, seconds):
    """The kit's timeline with each event's time on the recording ("at"), or how far into the
    player's run it was ("in") when it can't be placed."""
    tl = hubmod.read_timeline(raw)
    shift = tl["wall"] - start if tl["wall"] and start else None
    out = []
    for ev in tl["events"]:
        at = ev["t"] + shift if shift is not None else None
        row = {"at": clock(at)} if at is not None and 0 <= at <= (seconds or 0) else {"in": clock(ev["t"])}
        out.append({**row, "kind": ev["kind"], **ev["fields"]})
    return out


def summary(s):
    """One session, as the list shows it."""
    return {"id": s["id"], "person": s["person"], "played": s["at"], "minutes": (s.get("viewer_seconds") or 0) // 60,
            "revision": (s.get("revision") or "")[:10], "mr": s.get("mr_iid"), "opened": s.get("open") or "game",
            "recording": clock(s["recording_seconds"]) if s.get("recording") else None,
            "voice": bool(s.get("voice")),
            **({"note": s["note"]} if s.get("note") else {})}


def fetch(hub, s, group, out=None):
    """The session's files from the depot into WATCH_ROOT/<game>/<id>/, once each (a file is
    kept under its object's name, so a finished download is never fetched again). Root owns
    them; the work group reads them. `out` hears once if anything has to be downloaded.
    Returns {"recording": path, "log": path, "timeline": path, "voice": path}."""
    d = os.path.join(WATCH_ROOT, s["game"], s["id"])
    os.makedirs(d, mode=0o750, exist_ok=True)
    for p in (WATCH_ROOT, os.path.dirname(d), d):
        os.chown(p, 0, group)
        os.chmod(p, 0o750)
    paths = {}
    for key, name in (("recording", "recording.mp4"), ("log", "player.log"), ("timeline", "timeline.jsonl"),
                      ("voice", "voice.ogg")):
        sha = s.get(key)
        if not sha or not hubmod.SHA_RE.match(sha):
            continue
        dest, mark = os.path.join(d, name), os.path.join(d, f".{name}.{sha[:16]}")
        if not (os.path.isfile(dest) and os.path.isfile(mark)):
            if out:
                out("fetching the session's files from the depot")
                out = None
            hub.get(sha, dest)
            open(mark, "w").close()  # noqa: SIM115
        os.chown(dest, 0, group)
        os.chmod(dest, 0o640)
        paths[key] = dest
    os.utime(d)
    return paths


def trim(now=None):
    """Sessions nobody has looked at for a week go from the cell (the depot still has them)."""
    import shutil
    now = now or time.time()
    try:
        games = os.listdir(WATCH_ROOT)
    except OSError:
        return
    for g in games:
        gd = os.path.join(WATCH_ROOT, g)
        for sid in os.listdir(gd) if os.path.isdir(gd) else ():
            p = os.path.join(gd, sid)
            if SID_RE.match(sid) and now - os.path.getmtime(p) > KEEP_DAYS * 86400:
                shutil.rmtree(p, ignore_errors=True)


def read_text(s, paths):
    """What happened in the session, as text: the daemon's answer to `studio watch <id>`."""
    start, seconds = s.get("recording_start"), s.get("recording_seconds")
    out = {"session": summary(s), "ended": s.get("end_reason"),
           "recording": paths.get("recording"), "seconds": seconds if paths.get("recording") else None}
    if not paths.get("recording"):
        out["no_recording"] = s.get("note") or "this session has no recording (yet: it arrives within a minute of the end)"
    if paths.get("timeline"):
        with open(paths["timeline"], "rb") as f:
            out["timeline"] = timeline_events(f.read(hubmod.MAX_TIMELINE_BYTES), start, seconds)
    else:
        out["timeline"] = None
    tweaks = (hubmod.check_tweaks(s.get("tweaks")) or {}).get("assets") if s.get("tweaks") else None
    if tweaks:
        out["tweaks"] = tweaks
    if paths.get("log"):
        with open(paths["log"], "rb") as f:
            out["log_moments"] = log_moments(f.read(hubmod.MAX_LOG_BYTES), start)
        out["log"] = paths["log"]
    if paths.get("voice"):
        # The player's microphone alone (it's in the recording too). Its clock against the recording's:
        out["voice"] = paths["voice"]
        if s.get("voice_start") is not None and start is not None:
            out["voice_begins"] = round(s["voice_start"] - start, 2)
    if paths.get("recording"):
        out["look"] = (f"studio watch {s['id']} --from M:SS --to M:SS  (a filmstrip of that stretch; --frames N, "
                       f"8 by default)   studio watch {s['id']} --at M:SS  (one frame, full size)")
    return out


# --- pictures, cut by the caller ---------------------------------------------------------------

def _font():
    for f in FONTS:
        if os.path.isfile(f):
            return f
    return None


def _ffmpeg(argv, out):
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *argv, out], capture_output=True, text=True,
                           timeout=180, check=False)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg isn't installed on this cell") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg took more than 3 minutes; ask for a shorter stretch") from None
    if r.returncode != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        raise RuntimeError(f"ffmpeg couldn't cut the picture: {r.stderr.strip()[-300:] or 'no output'}")
    return out


def frame(recording, seconds, at, out_dir, stamp=None):
    """One frame at `at`, 1280 wide."""
    if not 0 <= at <= (seconds or at):
        raise ValueError(f"the recording runs 0:00 to {clock(seconds)}")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{stamp or time.strftime('%Y%m%dT%H%M%S')}-at-{int(at)}s.png")
    _ffmpeg(["-ss", f"{at:.3f}", "-i", recording, "-frames:v", "1", "-vf", "scale=1280:-2"], out)
    return {"png": out, "at": clock(at)}


def filmstrip(recording, seconds, start, end, out_dir, frames=8, stamp=None):
    """`frames` evenly spaced frames from start..end in one picture, four to a row, each with
    its time on the recording burned in (when the cell has the font)."""
    end = min(end, seconds) if seconds else end
    if not 0 <= start < end:
        raise ValueError(f"--from must be before --to, inside the recording (0:00 to {clock(seconds)})")
    frames = max(2, min(int(frames), MAX_FRAMES))
    span = end - start
    times = [start + span * i / frames for i in range(frames)]      # where ffmpeg's fps filter samples
    cols = min(frames, STRIP_COLUMNS)
    rows = -(-frames // cols)
    vf = [f"fps={frames}/{span:.3f}", "scale=480:-2"]
    font = _font()
    if font:
        # After fps, a frame's time is its place in the stretch; the offset makes it the recording's.
        vf.append(f"drawtext=fontfile={font}:text='%{{pts\\:hms\\:{start:.3f}}}':x=8:y=8:fontsize=22:"
                  "fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=5")
    vf.append(f"tile={cols}x{rows}:padding=4:margin=4")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{stamp or time.strftime('%Y%m%dT%H%M%S')}-{int(start)}s-{int(end)}s.png")
    _ffmpeg(["-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", recording, "-vf", ",".join(vf), "-frames:v", "1"], out)
    return {"png": out, "from": clock(start), "to": clock(end), "layout": f"{cols} across, {rows} down, in order",
            "frames": [clock(t) for t in times], "times_burned_in": bool(font)}
