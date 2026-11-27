"""forge-playd: the Forge's play sessions (design: architecture §4 "play"; vault notes/step-2b-1-plan).

Root, on the Forge (role forge_play). Every few seconds it tells the hub's broker what runs
(ready or not, and how many viewers each session has) and gets back the sessions that should
run, then makes it so:

  - a session that should run and doesn't: fetch its build from the depot by sha256 (checked),
    unpack it once into the cache (read-only to the session), and start play-s<slot>: the play
    image, the GPU, a fresh tmpfs home, the build mounted read-only, on the locked bridge,
    published on <host_ip>:<base_port + slot> for studio-gw to forward Traefik's streams to;
  - a container whose session shouldn't run, or that died: its player log is kept, the numbers
    the person tuned on the Stage are collected (the Studio Kit writes them to
    $STUDIO_OUT/tweaks.json, inside the session's tmpfs home), and it goes. The tweaks go to
    the hub with the next sync, which posts them to the MR with the play.

A session opens the game, its Stage, or one asset on its Stage: the player gets
-studio-open <game|stage|stage:id>, which the hub chose from the build's own catalogue.

Every session is recorded (vault notes/step-3c-watch-plan): once it's ready, wf-recorder inside
the container captures the session's screen (labwc, wayland-0) and the game's sound into a named
pipe in the session's own tmpfs, and this daemon reads the pipe from outside and writes it to
the Forge's disk, up to a size cap. The container gets no writable path on the host. When the
session ends, the recording is remuxed to MP4 in a throwaway container with no network, and it,
the player log and the Studio Kit's timeline ($STUDIO_OUT/timeline.jsonl) go to the depot; the
hub hears of them at the next sync. No microphone, no camera: what the game shows and plays.

Viewers are the established connections to the container's web port (Selkies' nginx, 3000)
from outside the container, read from the container's own /proc/<pid>/net/tcp.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.request

from . import hub as hubmod

CONFIG_FILE = os.environ.get("FORGE_PLAYD_CONFIG", "/etc/forge-playd/playd.json")
LABEL = "studio.session"
WEB_PORT = 3000
STUDIO_OUT = "/config/studio"     # in the session's tmpfs home: where the Studio Kit writes tweaks.json
REC_FIFO = "/config/.studio-rec.fifo"
# The session's desktop user in the play image, and labwc's socket (wayland-1 is the stream's
# own compositor socket, which refuses screen capture).
REC_USER, REC_ENV = "abc", {"XDG_RUNTIME_DIR": "/config/.XDG", "WAYLAND_DISPLAY": "wayland-0", "HOME": "/config"}
REC_DEFAULTS = {"enabled": True, "fps": 30, "cq": 30, "max_bytes": 3 << 30, "audio": "output.monitor",
                "uid": 1000, "gid": 1000}


def log(msg):
    print(msg, flush=True)


def docker(*args, check=True, timeout=120):
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {args[0]}: {r.stderr.strip()[-500:]}")
    return r.stdout.strip()


class Playd:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hub = hubmod.Hub(cfg)
        self.cache = cfg["cache"]
        self.state = cfg["state"]
        for d in (self.cache, os.path.join(self.state, "sessions"), os.path.join(self.state, "logs")):
            os.makedirs(d, mode=0o755, exist_ok=True)
        self.failed = {}          # session id -> why, reported at the next sync
        self.dead = set()         # sessions reported failed; never started again
        self.collected = {}       # session id -> its tweaks (or None), until a sync takes them
        self.recorders = {}       # session id -> the processes reading its recording out of the container
        self.capped = set()       # sessions whose recording ended early (the size cap)
        self.files = {}           # session id -> its files in the depot, until a sync takes them
        self.files_lock = threading.Lock()
        self.rec = {**REC_DEFAULTS, **(cfg.get("record") or {})}
        self.rec_root = os.path.join(self.state, "rec")
        os.makedirs(self.rec_root, mode=0o700, exist_ok=True)
        self.leftovers_done = False

    # --- what runs ------------------------------------------------------------------------------

    def containers(self):
        """Session containers: {session: {"name", "slot", "running", "pid"}}."""
        out = {}
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}").split()
        if not ids:
            return out
        for c in json.loads(docker("inspect", *ids)):
            labels = c["Config"]["Labels"] or {}
            out[labels[LABEL]] = {"name": c["Name"].lstrip("/"), "slot": int(labels.get("studio.slot", -1)),
                                  "running": c["State"]["Running"], "pid": c["State"]["Pid"],
                                  "exit": c["State"].get("ExitCode")}
        return out

    @staticmethod
    def viewers(pid):
        """Established TCP connections to the web port from outside the container."""
        n = 0
        for f in ("tcp", "tcp6"):
            try:
                with open(f"/proc/{pid}/net/{f}", encoding="ascii") as fh:
                    next(fh)
                    for line in fh:
                        p = line.split()
                        local, remote, st = p[1], p[2], p[3]
                        if st != "01" or int(local.rsplit(":", 1)[1], 16) != WEB_PORT:
                            continue
                        rip = remote.rsplit(":", 1)[0]
                        if rip in ("0100007F", "00000000000000000000000001000000"):
                            continue
                        n += 1
            except (OSError, StopIteration, ValueError, IndexError):
                pass
        return n

    def ready(self, slot):
        url = f"http://{self.cfg['host_ip']}:{self.cfg['base_port'] + slot}/play/s{slot}/"
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    # --- builds ---------------------------------------------------------------------------------

    def build_dir(self, obj):
        """The build, unpacked once by hash, read-only to sessions."""
        dest = os.path.join(self.cache, obj)
        if os.path.isdir(os.path.join(dest, "build")):
            os.utime(dest)
            return os.path.join(dest, "build")
        tmp = dest + ".part"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, mode=0o755)
        archive = os.path.join(tmp, "build.tar.gz")
        log(f"fetching {obj[:12]} from the depot")
        self.hub.get(obj, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(os.path.join(tmp, "build"), filter="data")
        os.unlink(archive)
        os.replace(tmp, dest)
        self.trim_cache(keep=obj)
        return os.path.join(dest, "build")

    def trim_cache(self, keep):
        """Oldest unpacked builds go until the cache is under its limit; never one in use."""
        in_use = {keep} | {s.get("object") for s in getattr(self, "want", [])}
        entries = []
        for name in os.listdir(self.cache):
            p = os.path.join(self.cache, name)
            if name.endswith(".part") or not os.path.isdir(p):
                continue
            size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs)
            entries.append((os.path.getmtime(p), name, p, size))
        total = sum(e[3] for e in entries)
        for _, name, p, size in sorted(entries):
            if total <= self.cfg["cache_max_bytes"]:
                break
            if name not in in_use:
                shutil.rmtree(p, ignore_errors=True)
                total -= size
                log(f"cache: removed {name[:12]}")

    # --- sessions -------------------------------------------------------------------------------

    def start(self, s):
        sid, slot = s["id"], int(s["slot"])
        build = self.build_dir(s["object"])
        exe = os.path.normpath(s["exe"])
        if exe.startswith(("/", "..")) or not os.path.isfile(os.path.join(build, exe)):
            raise RuntimeError(f"the build has no {s['exe']}")
        sdir = os.path.join(self.state, "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        target = hubmod.check_open(s.get("open") or "game")
        if not target:
            log(f"{sid}: the hub asked to open {str(s.get('open'))[:60]!r}; opening the game")
            target = "game"
        autostart = os.path.join(sdir, "autostart")
        with open(autostart, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n# forge-playd: the session's game; RESTART_APP brings it back if it exits.\n"
                    f"mkdir -p {STUDIO_OUT}\n"
                    f"exec env STUDIO_OUT={STUDIO_OUT} /game/{shlex.quote(exe)} {self.cfg['player_args']} "
                    f"-studio-open {shlex.quote(target)} -logFile /config/player.log\n")
        os.chmod(autostart, 0o755)
        name = f"play-s{slot}"
        docker("rm", "-f", name, check=False)
        c = self.cfg
        argv = ["run", "-d", "--name", name, "--label", f"{LABEL}={sid}", "--label", f"studio.slot={slot}",
                "--runtime", "nvidia", "--gpus", "all", "--network", c["network"],
                "--shm-size", "1g", "--memory", c["mem_limit"], "--memory-swap", c["mem_limit"],
                "--tmpfs", "/config:uid=1000,gid=1000,mode=0755,size=1g",
                "-v", f"{build}:/game:ro", "-v", f"{autostart}:/defaults/autostart_wayland:ro",
                "-p", f"{c['host_ip']}:{c['base_port'] + slot}:{WEB_PORT}"]
        for dev in c["devices"]:
            argv += ["--device", f"{dev}:{dev}"]
        env = {"PUID": "1000", "PGID": "1000", "TZ": "Etc/UTC", "TITLE": f"{s['game']} {s['revision'][:10]}",
               "SUBFOLDER": f"/play/s{slot}/", "NVIDIA_DRIVER_CAPABILITIES": "all", "PIXELFLUX_WAYLAND": "true",
               "HARDEN_DESKTOP": "true", "RESTART_APP": "true", "SELKIES_MANUAL_WIDTH": str(c["width"]),
               "SELKIES_MANUAL_HEIGHT": str(c["height"]), "SELKIES_SCALING_DPI": "96", "SELKIES_FRAMERATE": "60-60"}
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        docker(*argv, c["image"])
        log(f"{sid}: started {s['game']}@{s['revision'][:10]} on slot {slot}")

    # --- recordings -----------------------------------------------------------------------------

    def rec_dir(self, sid):
        return os.path.join(self.rec_root, sid)

    def recorder_argv(self, name):
        """docker exec: wf-recorder writes Matroska (readable even if it's cut short) into a
        named pipe, and cat hands the pipe to this daemon as the exec's standard output.
        wf-recorder's own messages go to a file in the session, because it prints to stdout."""
        r = self.rec
        rec = ["wf-recorder", "-y", "-c", "h264_nvenc", "-x", "yuv420p", "-p", "preset=p4", "-p", "rc=vbr",
               "-p", f"cq={int(r['cq'])}", "-p", f"g={int(r['fps']) * 2}", "-r", str(int(r["fps"])),
               f"--audio={r['audio']}", "-m", "matroska", "-f", REC_FIFO]
        script = (f"rm -f {REC_FIFO}; mkfifo {REC_FIFO} || exit 1; "
                  f"{' '.join(shlex.quote(a) for a in rec)} >/config/.studio-rec.log 2>&1 & exec cat {REC_FIFO}")
        argv = ["docker", "exec", "-u", REC_USER]
        for k, v in REC_ENV.items():
            argv += ["-e", f"{k}={v}"]
        return argv + [name, "sh", "-c", script]

    def start_recording(self, sid, name):
        """Once per session: a session whose recording was cut (this daemon restarted) keeps
        what it has."""
        d = self.rec_dir(sid)
        if not self.rec["enabled"] or sid in self.recorders or os.path.exists(os.path.join(d, "rec.mkv")):
            return
        os.makedirs(d, mode=0o700, exist_ok=True)
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"start": time.time()}, f)
        with open(os.path.join(d, "rec.mkv"), "wb") as out:
            src = subprocess.Popen(self.recorder_argv(name), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL)
            # head stops at the cap. Docker goes on draining the exec's output, so the recorder
            # never meets a closed pipe: recording_capped() tells it to stop.
            cap = subprocess.Popen(["head", "-c", str(int(self.rec["max_bytes"]))], stdin=src.stdout, stdout=out,
                                   stderr=subprocess.DEVNULL)
            src.stdout.close()
        self.recorders[sid] = (src, cap)
        log(f"{sid}: recording")

    def recording_capped(self, sid, name):
        """A recording that reached its size cap (or whose reader ended any other way while the
        session runs on): the recorder inside stops too, instead of encoding into nothing."""
        procs = self.recorders.get(sid)
        if not procs or procs[1].poll() is None or sid in self.capped:
            return
        self.capped.add(sid)
        subprocess.run(["docker", "exec", name, "pkill", "-INT", "-x", "wf-recorder"],
                       capture_output=True, check=False, timeout=20)
        log(f"{sid}: the recording stopped before the session did (its size cap is {int(self.rec['max_bytes'])} bytes)")

    def stop_recording(self, sid, name, running):
        """Ask the recorder to finish its file, then wait for the reader to see the end."""
        procs = self.recorders.pop(sid, None)
        self.capped.discard(sid)
        if running:
            subprocess.run(["docker", "exec", name, "pkill", "-INT", "-x", "wf-recorder"],
                           capture_output=True, check=False, timeout=20)
        for p in procs or ():
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    @staticmethod
    def read_out(name, path, limit):
        """A file from the session's tmpfs home (docker cp can't see a tmpfs), at most limit
        bytes, or None."""
        try:
            r = subprocess.run(["docker", "exec", name, "head", "-c", str(limit), path],
                               capture_output=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            return None
        return r.stdout if r.returncode == 0 and r.stdout else None

    def keep_session_files(self, sid, name):
        """Before the container goes: its player log and the kit's timeline, beside the recording."""
        d = self.rec_dir(sid)
        os.makedirs(d, mode=0o700, exist_ok=True)
        for src, dest, limit in (("/config/player.log", "player.log", hubmod.MAX_LOG_BYTES),
                                 (f"{STUDIO_OUT}/timeline.jsonl", "timeline.jsonl", hubmod.MAX_TIMELINE_BYTES)):
            raw = self.read_out(name, src, limit)
            if raw:
                with open(os.path.join(d, dest), "wb") as f:
                    f.write(raw)

    def remux(self, d):
        """rec.mkv -> out/rec.mp4 (no re-encode, index first so a browser can seek), in a
        container from the play image with no network, no capabilities and the recording
        read-only. Returns (path, seconds), or (None, why)."""
        r = self.rec
        out = os.path.join(d, "out")
        os.makedirs(out, mode=0o700, exist_ok=True)
        os.chown(out, r["uid"], r["gid"])
        os.chmod(os.path.join(d, "rec.mkv"), 0o644)
        base = ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
                "--user", f"{r['uid']}:{r['gid']}", "-v", f"{d}/rec.mkv:/in/rec.mkv:ro", "-v", f"{out}:/out"]
        x = subprocess.run(base + ["--entrypoint", "ffmpeg", self.cfg["image"], "-v", "error", "-y", "-i",
                                   "/in/rec.mkv", "-c", "copy", "-movflags", "+faststart", "/out/rec.mp4"],
                           capture_output=True, text=True, timeout=900, check=False)
        mp4 = os.path.join(out, "rec.mp4")
        if x.returncode != 0 or not os.path.isfile(mp4) or not os.path.getsize(mp4):
            return None, (x.stderr.strip() or "no output")[-300:]
        p = subprocess.run(base + ["--entrypoint", "ffprobe", self.cfg["image"], "-v", "error", "-show_entries",
                                   "format=duration", "-of", "csv=p=0", "/out/rec.mp4"],
                           capture_output=True, text=True, timeout=120, check=False)
        try:
            seconds = round(float(p.stdout.strip()), 1)
        except ValueError:
            seconds = None
        return mp4, seconds

    @staticmethod
    def sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()

    def finish_files(self, sid):
        """In its own thread, after the container is gone: the recording as MP4, the player log
        and the timeline go to the depot, and the next sync tells the hub. The session's
        directory goes once the depot has them; a failure leaves it for the daemon's next start."""
        d = self.rec_dir(sid)
        files = {}
        try:
            mkv = os.path.join(d, "rec.mkv")
            if os.path.isfile(mkv) and os.path.getsize(mkv):
                mp4, seconds = self.remux(d)
                if mp4:
                    sha = self.sha256(mp4)
                    self.hub.put(mp4, sha)
                    start = None
                    try:
                        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                            start = float(json.load(f)["start"])
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                    files.update(recording=sha, recording_seconds=seconds, recording_start=start)
                    log(f"{sid}: recording {sha[:12]} in the depot ({os.path.getsize(mp4) / 1e6:.1f} MB, {seconds} s)")
                else:
                    files["note"] = f"the recording couldn't be read: {seconds}"[:200]
                    log(f"{sid}: {files['note']}")
            for fn, key in (("player.log", "log"), ("timeline.jsonl", "timeline")):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and os.path.getsize(p):
                    sha = self.sha256(p)
                    self.hub.put(p, sha)
                    files[key] = sha
        except Exception as e:  # noqa: BLE001 - kept on disk; the daemon's next start tries again
            log(f"{sid}: its files didn't reach the depot: {e}")
            return
        if files:
            with self.files_lock:
                self.files[sid] = files
        shutil.rmtree(d, ignore_errors=True)

    def finish_leftovers(self, have):
        """Once, at the first tick: sessions whose files were still here when the daemon last
        stopped, and whose containers are gone."""
        self.leftovers_done = True
        for sid in os.listdir(self.rec_root):
            if sid not in have and re.fullmatch(r"[0-9a-f]{16}", sid):
                shutil.rmtree(os.path.join(self.rec_root, sid, "out"), ignore_errors=True)
                log(f"{sid}: files left from before; sending them")
                threading.Thread(target=self.finish_files, args=(sid,), daemon=True).start()

    @staticmethod
    def collect_tweaks(name):
        """The session's tweaks.json, read inside the container before it goes: its home is a
        tmpfs, gone with it. None when there are none or they can't be read. Checked here, and
        again by the hub."""
        try:
            # head -c, not cat: the build wrote this file, so read no more than a real one can be.
            r = subprocess.run(["docker", "exec", name, "head", "-c", str(4 * hubmod.MAX_TWEAKS_BYTES + 1),
                                f"{STUDIO_OUT}/tweaks.json"], capture_output=True, timeout=20, check=False)
        except subprocess.TimeoutExpired:
            return None
        if r.returncode != 0 or not r.stdout or len(r.stdout) > 4 * hubmod.MAX_TWEAKS_BYTES:
            return None
        try:
            t = hubmod.check_tweaks(json.loads(r.stdout))
        except (ValueError, RecursionError):
            return None
        return t if t and t["assets"] else None

    def stop(self, sid, info, why):
        """End the recording, collect the Stage tweaks, keep the player log and the timeline,
        then remove the container and the session's files. The recording and the kept files go
        to the depot from a thread of their own."""
        self.stop_recording(sid, info["name"], info.get("running"))
        self.collected[sid] = self.collect_tweaks(info["name"]) if info.get("running") else None
        if self.collected[sid]:
            log(f"{sid}: collected tweaks for {len(self.collected[sid]['assets'])} asset(s)")
        if info.get("running"):
            self.keep_session_files(sid, info["name"])
        # The Forge's own copy of the log, for a failed session's report. (Until 2026-11 this
        # was `docker cp`, which can't read the session's tmpfs home, so no log was ever kept.)
        try:
            shutil.copyfile(os.path.join(self.rec_dir(sid), "player.log"),
                            os.path.join(self.state, "logs", f"{sid}.log"))
        except OSError:
            pass
        docker("rm", "-f", info["name"], check=False)
        if os.path.isdir(self.rec_dir(sid)):
            threading.Thread(target=self.finish_files, args=(sid,), daemon=True).start()
        shutil.rmtree(os.path.join(self.state, "sessions", sid), ignore_errors=True)
        logs = sorted(os.listdir(os.path.join(self.state, "logs")),
                      key=lambda f: os.path.getmtime(os.path.join(self.state, "logs", f)))
        for f in logs[:-50]:
            os.unlink(os.path.join(self.state, "logs", f))
        log(f"{sid}: removed ({why})")

    def tail(self, sid):
        try:
            with open(os.path.join(self.state, "logs", f"{sid}.log"), encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-12:])
        except OSError:
            return ""

    # --- the loop -------------------------------------------------------------------------------

    def tick(self):
        have = self.containers()
        if not self.leftovers_done:
            self.finish_leftovers(have)
        running, gone = {}, {}
        for sid, info in have.items():
            if info["running"]:
                running[sid] = {"ready": self.ready(info["slot"]), "viewers": self.viewers(info["pid"])}
                if running[sid]["ready"]:
                    try:
                        self.start_recording(sid, info["name"])
                        self.recording_capped(sid, info["name"])
                    except Exception as e:  # noqa: BLE001 - a session plays on without a recording
                        log(f"{sid}: couldn't start recording: {e}")
            else:
                gone[sid] = info
        for sid, info in gone.items():           # a container that died: its session failed
            self.stop(sid, info, f"exited with {info['exit']}")
            if sid not in self.dead:
                self.failed[sid] = f"the play container exited ({info['exit']})\n{self.tail(sid)}"
        collected = dict(self.collected)
        with self.files_lock:
            files = dict(self.files)
        report = {"running": running, "failed": self.failed, "collected": collected, "files": files}
        want = self.hub.call("POST", "/v1/forge/sync", report, timeout=15)["sessions"]
        self.dead |= set(self.failed)
        self.failed = {}
        for sid in collected:                    # taken; anything collected since waits for the next
            self.collected.pop(sid, None)
        with self.files_lock:
            for sid in files:
                self.files.pop(sid, None)
        self.want = want
        wanted = {s["id"] for s in want}
        for sid, info in have.items():
            if sid not in wanted and sid not in gone:
                self.stop(sid, info, "the session ended")
        for s in want:
            if s["id"] in running or s["id"] in self.dead:
                continue
            try:
                self.start(s)
            except Exception as e:  # noqa: BLE001 - one bad session must not stop the others
                log(f"{s['id']}: couldn't start: {e}")
                self.failed[s["id"]] = f"couldn't start: {e}"[:2000]

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive anything one tick hits
                log("tick failed:\n" + traceback.format_exc())
                time.sleep(10)
            time.sleep(self.cfg.get("poll_seconds", 2))


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    p = Playd(cfg)
    log(f"forge-playd: {cfg['slots']} slots from :{cfg['base_port']}, image {cfg['image']}, "
        f"hub {cfg['hub']['url']} as {p.hub.whoami()['name']}")
    p.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
