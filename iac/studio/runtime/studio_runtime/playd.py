"""forge-playd: the Forge's play sessions (design: architecture §4 "play"; vault notes/step-2b-1-plan).

Root, on the Forge (role forge_play). Every few seconds it tells the hub's broker what runs
(ready or not, and how many viewers each session has) and gets back the sessions that should
run, then makes it so:

  - a session that should run and doesn't: fetch its build from the depot by sha256 (checked),
    unpack it once into the cache (read-only to the session), and start play-s<slot>: the play
    image, the GPU, a fresh tmpfs home, the build mounted read-only, on the locked bridge,
    published on <host_ip>:<base_port + slot> for studio-gw to forward Traefik's streams to;
  - a container whose session shouldn't run, or that died: its player log is kept, and it goes.

Viewers are the established connections to the container's web port (Selkies' nginx, 3000)
from outside the container, read from the container's own /proc/<pid>/net/tcp.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.request

from . import hub as hubmod

CONFIG_FILE = os.environ.get("FORGE_PLAYD_CONFIG", "/etc/forge-playd/playd.json")
LABEL = "studio.session"
WEB_PORT = 3000


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
        autostart = os.path.join(sdir, "autostart")
        with open(autostart, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n# forge-playd: the session's game; RESTART_APP brings it back if it exits.\n"
                    f"exec /game/{shlex.quote(exe)} {self.cfg['player_args']} -logFile /config/player.log\n")
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

    def stop(self, sid, info, why):
        """Keep the player log, then remove the container and the session's files."""
        dest = os.path.join(self.state, "logs", f"{sid}.log")
        subprocess.run(["docker", "cp", f"{info['name']}:/config/player.log", dest],
                       capture_output=True, check=False, timeout=30)
        docker("rm", "-f", info["name"], check=False)
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
        running, gone = {}, {}
        for sid, info in have.items():
            if info["running"]:
                running[sid] = {"ready": self.ready(info["slot"]), "viewers": self.viewers(info["pid"])}
            else:
                gone[sid] = info
        for sid, info in gone.items():           # a container that died: its session failed
            self.stop(sid, info, f"exited with {info['exit']}")
            if sid not in self.dead:
                self.failed[sid] = f"the play container exited ({info['exit']})\n{self.tail(sid)}"
        report = {"running": running, "failed": self.failed}
        want = self.hub.call("POST", "/v1/forge/sync", report, timeout=15)["sessions"]
        self.dead |= set(self.failed)
        self.failed = {}
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
