"""studio-unityd: the Unity broker (design: engine-and-agent-runtime §2-3).

Root. The agent's only way into Unity. It keeps a warm editor per worktree (a systemd unit
running as unity, with its own X server that nothing else can reach), forwards Pipeline
commands to it, and runs the cold gates and builds on committed revisions in a separate
checkout, one at a time per cell. Callers are identified by the kernel (common.Caller) and
act only on the worktree they stand in.
"""

import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

from . import common

RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_RUN = os.path.join(RUNTIME_DIR, "bin", "studio-editor-run")
GATE_LOCK = os.path.join(common.RUN_DIR, "gate.lock")

# Pipeline commands an agent may not call through the broker. Not a security boundary (an
# agent's editor scripts run as unity anyway), but it keeps every change in committed files,
# where review and the gates see it: no code run from a string, no batches that could wrap
# one, no changes to the Pipeline server's own settings.
DENY = {("script", "eval"), ("script", "eval_file"), ("script", "run"), ("job", "batch")}
DENY_GROUPS = {"settings.runtime_pipeline"}
# The CLI's own options: a command's arguments may not retarget it at another editor.
RESERVED = {"--project-path", "--runtime", "--runtime-path", "--port", "--pid", "--instance",
            "--detach", "--format", "--json", "--proxy", "--proxy-disable", "--log-proxy",
            "--no-log-proxy", "--result-only", "--timeout"}
WORD_RE = re.compile(r"^[a-z][a-z0-9_.]*$")
PARAM_RE = re.compile(r"^--[a-z_][a-z0-9_]*$")


class Fail(Exception):
    """A refusal or failure to report to the caller (rc 1 unless given)."""

    def __init__(self, msg, rc=1, result=None):
        super().__init__(msg)
        self.rc, self.result = rc, result


class Broker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.unity_user = cfg["unity_user"]
        self.agent_uid = pwd.getpwnam(cfg["agent_user"]).pw_uid
        self.editor = cfg["unity_editor"]
        self.version = cfg["unity_editor_version"]
        self.env = cfg["unity_env"]
        self.lock = threading.Lock()
        self.starting = {}      # worktree key -> Lock
        self.last_used = {}     # worktree key -> time
        self.worktrees = {}     # worktree key -> Worktree
        self.gate_holder = None

    # --- warm editors -----------------------------------------------------------------------

    def unit(self, wt):
        return f"studio-editor-{wt.key}.service"

    def active(self, wt):
        r = subprocess.run(["systemctl", "is-active", "--quiet", self.unit(wt)], check=False)
        return r.returncode == 0

    def unity(self, wt, args, timeout=600):
        """`unity command` on the worktree's editor, as unity. Returns the parsed JSON."""
        r = common.run_as(self.unity_user,
                          ["unity", "command", "--project-path", wt.path, "--format", "json", *args],
                          env=self.env, cwd=wt.path, timeout=timeout)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {"success": False, "errors": [{"code": "NO_JSON", "message": (r.stderr or r.stdout)[-800:]}]}

    def ready(self, wt):
        d = self.unity(wt, ["editor", "status"], timeout=30)
        st = ((d.get("data") or {}).get("result") or {}) if d.get("success") else {}
        return st.get("status") == "ready" and not st.get("compiling") and not st.get("domainReloadInProgress")

    def ensure(self, wt, out):
        """Start the worktree's warm editor if it isn't running, and wait until it's ready."""
        with self.lock:
            lk = self.starting.setdefault(wt.key, threading.Lock())
            self.worktrees[wt.key] = wt
        with lk:
            self.last_used[wt.key] = time.time()
            if self.active(wt) and self.ready(wt):
                return
            if not self.active(wt):
                if wt.project_version() != self.version:
                    raise Fail(f"the project is on Unity {wt.project_version()}, this cell has {self.version}")
                os.makedirs(os.path.join(wt.path, "Logs"), exist_ok=True)
                shutil.chown(os.path.join(wt.path, "Logs"), self.cfg["agent_user"], self.cfg["work_group"])
                out(f"starting a warm editor for {wt.game}/{wt.name} (the first import can take minutes)")
                subprocess.run(["systemctl", "reset-failed", self.unit(wt)], capture_output=True, check=False)
                argv = ["systemd-run", "--quiet", "--collect", f"--unit={self.unit(wt)}",
                        f"--uid={self.unity_user}", f"--gid={self.unity_user}",
                        f"--working-directory={wt.path}", "--property=Type=exec",
                        "--property=PrivateTmp=yes", "--property=KillMode=control-group",
                        "--property=TimeoutStopSec=20", f"--property=MemoryMax={self.cfg['editor_memory_max']}",
                        "--property=UMask=0002", "--setenv=HOME=" + pwd.getpwnam(self.unity_user).pw_dir,
                        "--setenv=LANG=C.UTF-8"]
                argv += [f"--setenv={k}={v}" for k, v in self.env.items()]
                argv += ["--", EDITOR_RUN, wt.path, self.editor]
                subprocess.run(argv, check=True)
            deadline = time.time() + 900
            next_note = time.time() + 20
            while time.time() < deadline:
                if not self.active(wt):
                    raise Fail("the editor exited while starting; its log: " + self.log_tail(wt))
                if self.ready(wt):
                    out("the warm editor is ready")
                    return
                if time.time() > next_note:
                    out("still importing...")
                    next_note = time.time() + 30
                time.sleep(3)
            raise Fail("the editor wasn't ready after 15 minutes; its log: " + self.log_tail(wt))

    def log_tail(self, wt, n=15):
        try:
            with open(os.path.join(wt.path, "Logs", "Editor.log"), encoding="utf-8", errors="replace") as f:
                lines = [l.rstrip() for l in f.readlines() if "ALSA" not in l]
            return "\n".join(lines[-n:])
        except OSError:
            return "(no log)"

    def stop(self, wt):
        subprocess.run(["systemctl", "stop", self.unit(wt)], capture_output=True, check=False)
        self.last_used.pop(wt.key, None)

    def adopt(self):
        """Warm editors outlive a broker restart (they're their own units): take them back."""
        r = subprocess.run(["systemctl", "list-units", "--plain", "--no-legend", "--state=active",
                            "studio-editor-*.service"], capture_output=True, text=True, check=False)
        root = os.path.join(os.path.realpath(common.STUDIO_ROOT), "")
        for line in r.stdout.splitlines():
            unit = line.split()[0] if line.split() else ""
            wd = subprocess.run(["systemctl", "show", "-P", "WorkingDirectory", unit],
                                capture_output=True, text=True, check=False).stdout.strip()
            parts = wd[len(root):].split(os.sep) if wd.startswith(root) else []
            if len(parts) == 3 and parts[1] == "work":
                wt = common.Worktree(parts[0], parts[2])
                self.worktrees[wt.key] = wt
                self.last_used[wt.key] = time.time()
                print(f"adopted the running editor for {wt.game}/{wt.name}", flush=True)

    def reaper(self):
        idle = 60 * int(self.cfg.get("editor_idle_minutes", 30))
        while True:
            time.sleep(60)
            for key, wt in list(self.worktrees.items()):
                if key in self.last_used and time.time() - self.last_used[key] > idle and self.active(wt):
                    print(f"stopping idle editor {key}", flush=True)
                    self.stop(wt)

    def result_of(self, d, what):
        if not d.get("success"):
            raise Fail(f"{what} failed", result={"errors": d.get("errors")})
        return (d.get("data") or {}).get("result")

    # --- verbs ---------------------------------------------------------------------------------

    def v_status(self, caller, args, out):
        eds = []
        for key, wt in sorted(self.worktrees.items()):
            if self.active(wt):
                idle = int(time.time() - self.last_used.get(key, time.time()))
                eds.append({"worktree": f"{wt.game}/{wt.name}", "idle_seconds": idle})
        return {"editors": eds, "gate": self.gate_holder or "free", "unity": self.version}

    def v_start(self, caller, args, out):
        wt = common.worktree_of(caller)
        self.ensure(wt, out)
        return {"worktree": f"{wt.game}/{wt.name}", "editor": "ready"}

    def v_stop(self, caller, args, out):
        wt = common.worktree_of(caller)
        self.stop(wt)
        return {"worktree": f"{wt.game}/{wt.name}", "editor": "stopped"}

    def v_unity(self, caller, args, out):
        if len(args) < 2:
            raise Fail("usage: studio unity <group> <command> [--param value ...]", rc=2)
        group, cmd, rest = args[0], args[1], args[2:]
        if not (WORD_RE.match(group) and WORD_RE.match(cmd)):
            raise Fail("bad group or command name", rc=2)
        if (group, cmd) in DENY or group in DENY_GROUPS:
            raise Fail(f"'{group} {cmd}' isn't available through studio: put the change in code and let the editor compile it", rc=2)
        for a in rest:
            if a.startswith("-") and (a.split("=", 1)[0] in RESERVED or not PARAM_RE.match(a.split("=", 1)[0])):
                raise Fail(f"argument {a!r} isn't allowed", rc=2)
        wt = common.worktree_of(caller)
        self.ensure(wt, out)
        d = self.unity(wt, [group, cmd, *rest])
        self.last_used[wt.key] = time.time()
        return self.result_of(d, f"{group} {cmd}")

    BUSY = {"compiling", "pending", "requested", "queued", "reloading", "running", "in_progress", "started"}

    def compile(self, wt, out):
        self.ensure(wt, out)
        st = self.result_of(self.unity(wt, ["script", "recompile"], timeout=120), "recompile") or {}
        if st.get("status") != "up_to_date":
            # Compiling, then a domain reload, during which the Pipeline server is away: a failed
            # poll just means "wait". Done when the editor is ready and the status isn't busy.
            time.sleep(3)
            deadline = time.time() + 600
            while time.time() < deadline:
                d = self.unity(wt, ["script", "recompile_status"], timeout=60)
                if d.get("success"):
                    st = (d.get("data") or {}).get("result") or {}
                    if str(st.get("status", "")).lower() not in self.BUSY and self.ready(wt):
                        break
                time.sleep(2)
            else:
                raise Fail("the editor didn't finish compiling within 10 minutes; its log: " + self.log_tail(wt))
        errs = self.result_of(self.unity(wt, ["console", "get", "--level", "error", "--tail", "50"], timeout=60),
                              "console get") or {}
        return st, [e.get("message", "")[:400] for e in errs.get("entries", [])]

    def v_compile(self, caller, args, out):
        wt = common.worktree_of(caller)
        st, errors = self.compile(wt, out)
        self.last_used[wt.key] = time.time()
        failed = bool(st.get("failed") or st.get("compilationFailed"))
        res = {"compiled": not failed, "errors": st.get("errors") or [], "console_errors": errors}
        if failed:
            raise Fail("compilation failed", result=res)
        return res

    def v_test(self, caller, args, out):
        mode, flt = "all", None
        it = iter(args)
        for a in it:
            if a == "--mode":
                mode = next(it, "all")
            elif a == "--filter":
                flt = next(it, None)
            else:
                raise Fail("usage: studio test [--mode editor|playmode|all] [--filter NAME]", rc=2)
        if mode not in ("editor", "playmode", "all"):
            raise Fail("--mode is editor, playmode or all", rc=2)
        wt = common.worktree_of(caller)
        st, errors = self.compile(wt, out)
        if st.get("failed") or st.get("compilationFailed"):
            raise Fail("compilation failed", result={"errors": st.get("errors"), "console_errors": errors})
        out(f"running {mode} tests on the warm editor")
        # Async, then poll: PlayMode tests only run this way on a batch-mode editor (a
        # synchronous run returns no results for them).
        targs = ["test", "run", "--mode", mode, "--async_tests", "true"] + (["--filter", flt] if flt else [])
        self.result_of(self.unity(wt, targs, timeout=120), "test run")
        deadline, res = time.time() + 1200, {}
        while time.time() < deadline:
            time.sleep(2)
            d = self.unity(wt, ["test", "status"], timeout=60)
            if d.get("success"):
                res = (d.get("data") or {}).get("result") or {}
                if str(res.get("status", "")).lower() in ("completed", "failed", "cancelled", "error"):
                    break
        else:
            raise Fail("the tests didn't finish within 20 minutes")
        self.last_used[wt.key] = time.time()
        summary = res.get("summary") or {}
        failures = [{"test": r.get("FullName"), "message": (r.get("Message") or "")[:400]}
                    for r in (res.get("results") or []) if str(r.get("Status", "")).lower().startswith("fail")]
        out_res = {"mode": mode, "status": res.get("status"), "summary": summary, "failures": failures[:30]}
        if summary.get("failed") or str(res.get("status", "")).lower() != "completed":
            raise Fail(f"{summary.get('failed', '?')} test(s) failed", rc=8, result=out_res)
        if not summary.get("total"):
            out("note: no tests matched")
        return out_res

    def v_capture(self, caller, args, out):
        w, h = "1280", "720"
        it = iter(args)
        for a in it:
            if a == "--width":
                w = next(it, w)
            elif a == "--height":
                h = next(it, h)
            else:
                raise Fail("usage: studio capture [--width N] [--height N]", rc=2)
        if not (w.isdigit() and h.isdigit()):
            raise Fail("width and height are numbers", rc=2)
        wt = common.worktree_of(caller)
        self.ensure(wt, out)
        # Pipeline's save_path lands under Assets/ (where the capture would be imported and
        # committed), so take the image inline and write it to Logs/captures/ ourselves: Logs/
        # is git-ignored and, unlike Temp/, the Editor leaves it alone.
        res = self.result_of(self.unity(wt, ["capture", "game_view", "--source", "camera", "--width", w,
                                             "--height", h]), "capture") or {}
        if not res.get("base64"):
            raise Fail("the capture returned no image", result=res)
        d = os.path.join(wt.path, "Logs", "captures")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, time.strftime("%Y%m%dT%H%M%S") + ".png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["base64"]))
        for p in (d, path):
            shutil.chown(p, self.cfg["agent_user"], self.cfg["work_group"])
        os.chmod(path, 0o660)
        return {"png": path, "width": res.get("width"), "height": res.get("height"), "source": res.get("source")}

    # --- cold gates and builds ---------------------------------------------------------------

    def game_dir(self, wt, sub, owner):
        path = os.path.join(wt.game_dir, sub)
        if not os.path.isdir(path):
            os.makedirs(path)
            shutil.chown(path, owner, self.cfg["work_group"])
            os.chmod(path, 0o2750)
        return path

    def git_unity(self, args, cwd, timeout=600):
        r = common.run_as(self.unity_user, ["git", "-c", "safe.directory=*", *args], env=self.env,
                          cwd=cwd, timeout=timeout)
        if r.returncode != 0:
            raise Fail(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[-500:]}")
        return r.stdout.strip()

    def checkout_head(self, wt, out):
        """The worktree's committed HEAD, checked out in the game's gate checkout (as unity)."""
        gate = self.game_dir(wt, "gate", self.unity_user)
        head = self.git_unity(["rev-parse", "HEAD"], wt.path)
        dirty = self.git_unity(["status", "--porcelain"], wt.path)
        if not os.path.isdir(os.path.join(gate, ".git")):
            self.git_unity(["init", "-q", gate], gate)
        self.git_unity(["fetch", "-q", "--no-tags", "--force", wt.path, "+HEAD:refs/studio/gate"], gate)
        self.git_unity(["checkout", "-q", "--force", "--detach", head], gate)
        self.git_unity(["clean", "-q", "-ffdx", "-e", "/Library/", "-e", "/Logs/", "-e", "/UserSettings/"], gate)
        n_dirty = len([l for l in dirty.splitlines() if l.strip()])
        if n_dirty:
            out(f"note: {n_dirty} uncommitted change(s) in the worktree are not part of {head[:10]}")
        return gate, head, n_dirty

    def cold(self, gate, extra, log, minutes):
        """One cold, batch-mode editor run in a transient unit, as unity. Returns its exit code."""
        argv = ["systemd-run", "--quiet", "--wait", "--collect", "--pipe",
                f"--uid={self.unity_user}", f"--gid={self.unity_user}", f"--working-directory={gate}",
                "--property=PrivateTmp=yes", f"--property=MemoryMax={self.cfg['gate_memory_max']}",
                f"--property=RuntimeMaxSec={minutes * 60}", "--property=UMask=0002",
                "--setenv=HOME=" + pwd.getpwnam(self.unity_user).pw_dir, "--setenv=LANG=C.UTF-8"]
        argv += [f"--setenv={k}={v}" for k, v in self.env.items()]
        argv += ["--", self.editor, "-batchmode", "-nographics", "-projectPath", gate, "-logFile", log, *extra]
        return subprocess.run(argv, capture_output=True, text=True, check=False).returncode

    @staticmethod
    def compile_errors(log):
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                errs = [l.strip() for l in f if re.search(r"error CS\d+", l)]
        except OSError:
            return []
        return list(dict.fromkeys(errs))[:30]

    @staticmethod
    def nunit(xml_path):
        try:
            root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError):
            return None
        s = {k: int(root.get(k, 0)) for k in ("total", "passed", "failed", "skipped")}
        s["failures"] = []
        for tc in root.iter("test-case"):
            if tc.get("result") == "Failed":
                msg = tc.find("failure/message")
                s["failures"].append({"test": tc.get("fullname"), "message": (msg.text or "").strip()[:400] if msg is not None else ""})
        s["failures"] = s["failures"][:30]
        return s

    def gate_lock(self, caller, out):
        fd = os.open(GATE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out(f"waiting for the gate lock (held by {self.gate_holder})")
            fcntl.flock(fd, fcntl.LOCK_EX)
        self.gate_holder = str(caller)
        return fd

    def gate_unlock(self, fd):
        self.gate_holder = None
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def record(self, wt, head, kind, res):
        d = os.path.join(common.STATE_DIR, "results", wt.game, head)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{kind}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)

    def v_gate(self, caller, args, out):
        kind = args[0] if args else "quick"
        if kind not in ("quick", "full") or len(args) > 1:
            raise Fail("usage: studio gate [quick|full]", rc=2)
        wt = common.worktree_of(caller)
        fd = self.gate_lock(caller, out)
        try:
            gate, head, dirty = self.checkout_head(wt, out)
            results = self.game_dir(wt, "results", self.unity_user)
            rdir = os.path.join(results, head)
            os.makedirs(rdir, exist_ok=True)
            shutil.chown(rdir, self.unity_user, self.cfg["work_group"])
            res = {"revision": head, "kind": kind, "uncommitted": dirty, "steps": []}
            steps = [("editmode", "EditMode")] + ([("playmode", "PlayMode")] if kind == "full" else [])
            ok = True
            for name, platform in steps:
                out(f"gate {kind}: {name} tests, cold, on {head[:10]}")
                log, xml = os.path.join(rdir, f"{name}.log"), os.path.join(rdir, f"{name}.xml")
                rc = self.cold(gate, ["-runTests", "-testPlatform", platform, "-testResults", xml], log, 40)
                step = {"step": name, "exit": rc, "tests": self.nunit(xml), "compile_errors": self.compile_errors(log)}
                step["passed"] = rc == 0 and step["tests"] is not None and step["tests"]["failed"] == 0
                res["steps"].append(step)
                if not step["passed"]:
                    ok = False
                    break
            if ok and kind == "full":
                b = self.build(wt, gate, head, "linux", out)
                res["steps"].append(b)
                ok = b["passed"]
            res["passed"] = ok
            self.record(wt, head, f"gate-{kind}", res)
        finally:
            self.gate_unlock(fd)
        if not ok:
            raise Fail(f"gate {kind} failed on {head[:10]}", result=res)
        return res

    def build(self, wt, gate, head, target, out):
        out(f"building {target}, cold, on {head[:10]}")
        bdir = self.game_dir(wt, "builds", self.unity_user)
        tdir = os.path.join(bdir, head, target)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir)
        os.makedirs(tdir)
        shutil.chown(os.path.dirname(tdir), self.unity_user, self.cfg["work_group"])
        shutil.chown(tdir, self.unity_user, self.cfg["work_group"])
        exe = {"linux": f"{wt.game}.x86_64", "windows": f"{wt.game}.exe"}[target]
        flag = {"linux": "-buildLinux64Player", "windows": "-buildWindows64Player"}[target]
        rdir = os.path.join(wt.game_dir, "results", head)
        log = os.path.join(rdir, f"build-{target}.log")
        rc = self.cold(gate, [flag, os.path.join(tdir, exe), "-quit"], log, 40)
        main = os.path.join(tdir, exe)
        res = {"step": f"build-{target}", "exit": rc, "path": tdir, "compile_errors": self.compile_errors(log)}
        if rc == 0 and os.path.isfile(main):
            h = hashlib.sha256()
            with open(main, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            size = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(tdir) for fn in fns)
            res.update({"passed": True, "binary": main, "sha256": h.hexdigest(), "bytes": size})
        else:
            res["passed"] = False
        return res

    def v_build(self, caller, args, out):
        if len(args) != 1 or args[0] not in ("linux", "windows", "web"):
            raise Fail("usage: studio build linux|windows|web", rc=2)
        if args[0] == "web":
            raise Fail("web builds arrive with the Studio Kit's build method (step 3)", rc=2)
        wt = common.worktree_of(caller)
        fd = self.gate_lock(caller, out)
        try:
            gate, head, _ = self.checkout_head(wt, out)
            self.game_dir(wt, "results", self.unity_user)
            rdir = os.path.join(wt.game_dir, "results", head)
            os.makedirs(rdir, exist_ok=True)
            shutil.chown(rdir, self.unity_user, self.cfg["work_group"])
            res = self.build(wt, gate, head, args[0], out)
            res["revision"] = head
            self.record(wt, head, f"build-{args[0]}", res)
        finally:
            self.gate_unlock(fd)
        if not res["passed"]:
            raise Fail(f"the {args[0]} build failed on {head[:10]}", result=res)
        return res


VERBS = {"status", "start", "stop", "unity", "compile", "test", "capture", "gate", "build"}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        broker = self.server.broker
        try:
            caller = common.Caller(conn)
        except OSError:
            return
        def out(msg):
            try:
                common.send(conn, {"out": msg})
            except OSError:
                pass
        rc, result, verb = 0, None, "?"
        try:
            verb, args = common.read_request(conn)
            if caller.uid not in (0, broker.agent_uid):
                raise Fail(f"{caller.user} may not use studio-unityd", rc=13)
            if verb not in VERBS:
                raise Fail(f"unknown verb {verb!r}", rc=2)
            result = getattr(broker, f"v_{verb}")(caller, args, out)
        except Fail as e:
            rc, result = e.rc, {"error": str(e), **({"detail": e.result} if e.result else {})}
        except (PermissionError, ValueError) as e:
            rc, result = 13 if isinstance(e, PermissionError) else 2, {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - report, keep serving
            rc, result = 1, {"error": f"{type(e).__name__}: {e}"}
        print(f"{caller} {verb} -> {rc}", flush=True)
        try:
            common.send(conn, {"rc": rc, "result": result})
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    cfg = common.load_config()
    os.makedirs(common.RUN_DIR, exist_ok=True)
    path = common.UNITYD_SOCKET
    if os.path.exists(path):
        os.unlink(path)
    server = Server(path, Handler)
    # Anyone may connect; the handler admits root and agent only (SO_PEERCRED).
    os.chmod(path, 0o666)
    server.broker = Broker(cfg)
    server.broker.adopt()
    threading.Thread(target=server.broker.reaper, daemon=True).start()
    print(f"studio-unityd listening on {path} (Unity {cfg['unity_editor_version']})", flush=True)
    try:
        server.serve_forever()
    finally:
        os.unlink(path)


if __name__ == "__main__":
    sys.exit(main())
