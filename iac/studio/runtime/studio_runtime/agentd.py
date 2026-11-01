"""studio-agentd: the cell's daemon (design: idea-to-playable, "studio-agentd").

Root. Every poll it reads the studio group's open issues and MRs labelled agent-request,
and acts only on approvals by this cell's person: the label event's author must be one of
the cell's approvers. It claims the item (agent-running, a claim note, a journal entry, so a
restart never runs a job twice), takes a snapshot of the item as it stood when approved,
and runs the job as a sandboxed transient unit (studio-job-<id>) as agent, in the job's own
worktree. The job holds one credential, the person's AI sign-in; every GitLab write is the
daemon's own, as the agent account, after the job:

  implement   issue -> branch agent/<iid>, MR, gate status, ledger, then a review job
  review      MR -> review notes from the other vendor

`studio done` (a job's last verb) asks the broker for the cold gates on the job's committed
HEAD; the job counts as done only with a passing gate for that exact revision (the ledger).
Publishing puts that revision's Linux build in the hub's depot, and the MR links it on the
portal; each poll then hands the hub any manifest it hasn't taken and the state of those MRs.
"""

import datetime as dt
import json
import os
import pwd
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import traceback

from . import admin, common
from . import hub as hubmod
from .gitlab import GitLab, GitLabError

RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB_RUN = os.path.join(RUNTIME_DIR, "bin", "studio-job-run")
AGENTD_SOCKET = common.AGENTD_SOCKET
JOBS_DIR = os.path.join(common.STATE_DIR, "jobs")
REQUEST, RUNNING, REVIEW = "agent-request", "agent-running", "agent-review"
BLOCKED, FAILED, STOP = "agent-blocked", "agent-failed", "agent-stop"
OTHER = {"claude": "codex", "codex": "claude"}


def now():
    return dt.datetime.now(dt.timezone.utc)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def log(msg):
    print(msg, flush=True)


class Job:
    """A job's journal, /var/lib/studio/jobs/<id>/job.json (root only)."""

    def __init__(self, jid, data=None):
        self.id = jid
        self.dir = os.path.join(JOBS_DIR, jid)
        self.data = data if data is not None else self._load()

    def _load(self):
        with open(os.path.join(self.dir, "job.json"), encoding="utf-8") as f:
            return json.load(f)

    def save(self):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        tmp = os.path.join(self.dir, "job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, os.path.join(self.dir, "job.json"))

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, **kw):
        self.data.update(kw)
        self.save()

    @property
    def unit(self):
        return f"studio-job-{self.id}.service"

    @staticmethod
    def all():
        if not os.path.isdir(JOBS_DIR):
            return []
        out = []
        for jid in sorted(os.listdir(JOBS_DIR)):
            try:
                out.append(Job(jid))
            except (OSError, ValueError):
                pass
        return out


class Agentd:
    def __init__(self, cfg):
        self.cfg = cfg
        self.a = cfg["agentd"]
        self.gl = GitLab(cfg)
        self.agent_uid = pwd.getpwnam(cfg["agent_user"]).pw_uid
        self.owner_uid = pwd.getpwnam(cfg["owner"]).pw_uid
        self.lock = threading.RLock()
        self.ignored = set()          # label events that aren't approvals, already noted
        self.hub = None               # the hub's depot, once this cell has its certificate
        if cfg.get("hub"):
            try:
                self.hub = hubmod.Hub(cfg)
            except (OSError, ssl.SSLError) as e:
                log(f"no hub: {e}")
        self.mr_synced = 0.0

    # --- the poll ------------------------------------------------------------------------------

    def loop(self):
        self.recover()
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive anything one tick hits
                log("tick failed:\n" + traceback.format_exc())
            time.sleep(int(self.a.get("poll_seconds", 60)))

    def recover(self):
        """After a restart: a job whose unit is gone was interrupted. It is never run again."""
        for job in Job.all():
            if job["state"] == "running" and not self.unit_active(job):
                self.finish(job)

    def tick(self):
        with self.lock:
            self.sync_hub()
            for job in Job.all():
                if job["state"] == "running" and not self.unit_active(job):
                    self.finish(job)
                elif job["state"] == "running" and self.stop_requested(job):
                    self.stop(job, "stopped by agent-stop")
            if os.path.exists(self.a.get("pause_file", "/etc/studio/pause")):
                return
            if sum(1 for j in Job.all() if j["state"] == "running") >= int(self.a.get("max_jobs", 1)):
                return
            for job in Job.all():
                if job["state"] == "queued":
                    self.start(job)
                    return
            for kind, item in sorted(self.gl.requested(REQUEST), key=lambda x: x[1]["updated_at"]):
                approval = self.approval(kind, item)
                if approval:
                    self.claim(kind, item, approval)
                    return

    def approval(self, kind, item):
        """The latest agent-request label event, if a person this cell answers to set it."""
        events = [e for e in self.gl.label_events(kind, item["project_id"], item["iid"])
                  if (e.get("label") or {}).get("name") == REQUEST and e.get("action") == "add"]
        if not events:
            return None
        ev = events[-1]
        who = (ev.get("user") or {}).get("username")
        if who in self.a["approvers"]:
            return {"by": who, "at": ev["created_at"], "event": ev["id"]}
        key = (kind, item["project_id"], item["iid"], ev["id"])
        if key not in self.ignored:
            self.ignored.add(key)
            log(f"{kind} {item['references']['full']}: agent-request set by {who}, not an approver here; ignored")
        return None

    # --- claiming and the snapshot ---------------------------------------------------------------

    def snapshot(self, kind, item, approval):
        """The item as it stood at approval: title, description and the comments up to then."""
        at = ts(approval["at"])
        notes = [n for n in self.gl.notes(kind, item["project_id"], item["iid"])
                 if not n.get("system") and ts(n["created_at"]) <= at]
        edited = [n for n in notes if ts(n.get("updated_at")) and ts(n["updated_at"]) > at + dt.timedelta(seconds=5)]
        void = None
        full_path = re.split(r"[#!]", item["references"]["full"])[0]
        last = ts(self.gl.last_edited(kind, full_path, item["iid"]))
        if last and last > at:
            void = "its description was edited after the approval"
        elif edited:
            void = "a comment was edited after the approval"
        lines = [f"# {item['title']}", "", item.get("description") or "(no description)", ""]
        for n in notes:
            lines += [f"--- comment by {n['author']['username']} at {n['created_at']}", n["body"], ""]
        return "\n".join(lines), void

    def claim(self, kind, item, approval):
        ref = item["references"]["full"]
        game = ref.split("/")[1].split("#")[0].split("!")[0]
        labels = item.get("labels", [])
        vendor = "codex" if "vendor:codex" in labels else "claude"
        jkind = "implement" if kind == "issue" else "review"
        if jkind == "review" and "vendor:claude" not in labels and "vendor:codex" not in labels:
            vendor = "codex"
        text, void = self.snapshot(kind, item, approval)
        if void:
            self.gl.relabel(kind, item["project_id"], item["iid"], add=[BLOCKED], remove=[REQUEST])
            self.gl.note(kind, item["project_id"], item["iid"],
                         f"Not started: {void}. Approve it again (`agent-request`) to run it as it stands now.")
            log(f"{ref}: void ({void})")
            return
        ok, why = self.preflight(vendor)
        if not ok:
            log(f"{ref}: waiting ({why})")
            return
        jid = f"{game}-{item['iid']}-{int(time.time()) % 10**7:07d}"
        job = Job(jid, {
            "id": jid, "kind": jkind, "vendor": vendor, "state": "queued", "game": game,
            "item": {"kind": kind, "project_id": item["project_id"], "iid": item["iid"], "ref": ref,
                     "title": item["title"], "web_url": item["web_url"],
                     "source_branch": item.get("source_branch"), "target_branch": item.get("target_branch")},
            "approval": approval, "claimed_at": now().isoformat()})
        job.save()
        with open(os.path.join(job.dir, "snapshot.md"), "w", encoding="utf-8") as f:
            f.write(text)
        self.gl.relabel(kind, item["project_id"], item["iid"], add=[RUNNING], remove=[REQUEST])
        self.gl.note(kind, item["project_id"], item["iid"],
                     f"Claimed by `cell-{self.cfg['owner']}`: job `{jid}` ({jkind}, {vendor}). "
                     f"Approved by @{approval['by']} at {approval['at']}; the job sees this item as it stood then.")
        log(f"{ref}: claimed as {jid} ({jkind}, {vendor})")
        self.start(job)

    # --- preflight ------------------------------------------------------------------------------

    def preflight(self, vendor):
        """The vendor's last known usage is under this person's ceilings, and Unity is licensed."""
        lim = self.limits(vendor)
        ceil = self.a["ceilings"]
        for window, used in lim.get("windows", {}).items():
            if used is not None and used >= ceil.get(window, 1.0):
                return False, f"{vendor} {window} window at {used:.0%}"
        if lim.get("overage"):
            return False, f"{vendor} is drawing on extra usage or credits"
        r = common.run_as(self.cfg["unity_user"], ["unity", "license", "status", "--format", "json"],
                          env=self.cfg["unity_env"], timeout=60)
        try:
            if not json.loads(r.stdout).get("data", {}).get("active"):
                return False, "the Unity licence isn't active"
        except ValueError:
            return False, "couldn't read the Unity licence status"
        return True, ""

    def limits(self, vendor):
        """Usage from the vendor's own records: Claude's rate_limit_event in the last job's
        stream, Codex's rate_limits in its newest rollout file."""
        if vendor == "claude":
            for job in sorted(Job.all(), key=lambda j: j.get("claimed_at", ""), reverse=True):
                info = job.get("rate_limit_info")
                if job.get("vendor") == "claude" and info:
                    wins = {k: v.get("utilization") for k, v in (info.get("unifiedWindows") or {}).items()}
                    return {"windows": wins, "overage": bool(info.get("isUsingOverage"))}
            return {}
        sess = os.path.join(pwd.getpwnam(self.cfg["agent_user"]).pw_dir, ".codex", "sessions")
        files = sorted((os.path.join(dp, f) for dp, _, fs in os.walk(sess) for f in fs if f.endswith(".jsonl")),
                       key=os.path.getmtime)
        for f in reversed(files[-5:]):
            rl = None
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "rate_limits" in line:
                        try:
                            p = json.loads(line).get("payload") or {}
                        except ValueError:
                            continue
                        rl = p.get("rate_limits") or (p.get("info") or {}).get("rate_limits") or rl
            if rl:
                wins = {}
                for name, key in (("primary", "primary"), ("secondary", "secondary")):
                    w = rl.get(key)
                    if w:
                        label = "five_hour" if w.get("window_minutes") == 300 else "seven_day" if w.get("window_minutes") == 10080 else name
                        wins[label] = (w.get("used_percent") or 0) / 100
                return {"windows": wins, "overage": bool(rl.get("rate_limit_reached_type"))}
        return {}

    # --- running a job --------------------------------------------------------------------------

    def start(self, job):
        item = job["item"]
        branch = f"agent/{item['iid']}" if job["kind"] == "implement" else item["source_branch"]
        wt = job.get("worktree")
        if not (job.get("resume") and wt and os.path.isdir(wt)):
            try:
                wt = admin.workspace(self.cfg, job["game"], job.id, branch)
            except Exception as e:  # noqa: BLE001
                self.fail(job, f"couldn't make the worktree: {e}")
                return
            # Where the branch stood on GitLab as the job began ("" = it didn't exist): the push's
            # lease. Kept in the journal, which the job can't touch.
            job.set(lease=self.remote_head(wt, branch))
            with open(os.path.join(job.dir, "prompt.md"), "w", encoding="utf-8") as f:
                f.write(self.prompt(job, branch))
        env = {"HOME": pwd.getpwnam(self.cfg["agent_user"]).pw_dir, "LANG": "C.UTF-8",
               "PATH": "/usr/local/bin:/usr/bin:/bin", **self.cfg["agent_env"]}
        prompt_file = os.path.join(job.dir, "prompt.md")
        if job.get("resume") and job.get("session_id"):
            env["STUDIO_RESUME"] = job["session_id"]
            prompt_file = os.path.join(job.dir, "prompt-resume.md")
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write("The person has handed the job back to you. Carry on from where the session "
                        "stands, and finish with `studio done` as the original instructions say.")
        argv = ["systemd-run", "--quiet", "--collect", f"--unit={job.unit}",
                f"--uid={self.cfg['agent_user']}", f"--gid={self.cfg['agent_user']}",
                f"--working-directory={wt}", "--property=Type=exec",
                "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes",
                "--property=ProtectSystem=strict", f"--property=ReadWritePaths={wt}",
                "--property=ProtectHome=tmpfs"]
        home = env["HOME"]
        for p in self.a["home_paths"]:                      # the sign-ins, and nothing else of home
            path = os.path.join(home, p)
            if os.path.exists(path):
                argv.append(f"--property=BindPaths={path}")
        # The prompt arrives on stdin, opened by systemd: the job can't read the job directory.
        argv += [f"--property=StandardInput=file:{prompt_file}",
                 f"--property=MemoryMax={self.a['job_memory_max']}", "--property=CPUWeight=50",
                 f"--property=RuntimeMaxSec={int(self.a['job_hours']) * 3600}",
                 "--property=KillMode=control-group", "--property=TimeoutStopSec=30",
                 f"--property=StandardOutput=file:{job.dir}/stream.jsonl",
                 f"--property=StandardError=file:{job.dir}/stderr.log"]
        argv += [f"--setenv={k}={v}" for k, v in env.items()]
        argv += ["--", JOB_RUN, job["vendor"], job["kind"]]
        subprocess.run(["systemctl", "reset-failed", job.unit], capture_output=True, check=False)
        subprocess.run(argv, check=True)
        job.set(state="running", branch=branch, worktree=wt, started_at=now().isoformat(), resume=False)
        log(f"{job.id}: started ({job['vendor']} {job['kind']} on {branch})")

    def prompt(self, job, branch):
        with open(os.path.join(job.dir, "snapshot.md"), encoding="utf-8") as f:
            snap = f.read()
        item = job["item"]
        if job["kind"] == "implement":
            return (f"You are this person's agent in a studio cell, working unattended on {item['ref']}, "
                    f"approved by @{job['approval']['by']}. The item, as it stood when approved:\n\n{snap}\n\n"
                    f"You are in a git worktree of the game on branch `{branch}`. Read AGENTS.md first and "
                    "follow it. Use `studio` for everything Unity (compile, test, capture, gate, docs). "
                    "Commit your work on this branch as you go; don't push (the daemon does). When the work "
                    "is complete and committed, run `studio done --summary <file>`, where the file holds a "
                    "short MR description: what changed, how it was tested, what to look at when playing. "
                    "`studio done` runs the cold gates on your HEAD; if it refuses, fix the problem and run "
                    "it again. If you can't complete the task, write why to a file and run "
                    "`studio done --blocked <file>`.")
        return (f"You are reviewing {item['ref']} for this person, unattended. The MR as approved for "
                f"review:\n\n{snap}\n\nYou are in a worktree of its branch `{branch}`; `git diff "
                f"origin/{item['target_branch']}...HEAD` is the change. Read AGENTS.md. Review for "
                "correctness, tests that would fail without the change, the studio's rules, and anything a "
                "player would notice. Don't change files or commit. Write the review as markdown to a file "
                "and run `studio done --review <file>`.")

    def unit_active(self, job):
        r = subprocess.run(["systemctl", "is-active", "--quiet", job.unit], check=False)
        return r.returncode == 0

    def stop_requested(self, job):
        it = job["item"]
        try:
            cur = self.gl.item(it["kind"], it["project_id"], it["iid"])
        except GitLabError:
            return False
        return STOP in cur.get("labels", []) or cur.get("state") != "opened"

    def stop(self, job, why):
        subprocess.run(["systemctl", "stop", job.unit], capture_output=True, check=False)
        job.set(stop_reason=why)

    def scan_stream(self, job):
        """Session id and the vendor's usage from the job's event stream."""
        path = os.path.join(job.dir, "stream.jsonl")
        sid, rli, final = None, None, None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    sid = e.get("session_id") or e.get("thread_id") or sid
                    if e.get("type") == "rate_limit_event":
                        rli = e.get("rate_limit_info") or rli
                    if e.get("type") == "result":
                        final = e.get("result")
                    it = e.get("item") or {}
                    if it.get("type") == "agent_message":
                        final = it.get("text") or final
        except OSError:
            pass
        job.set(session_id=sid, rate_limit_info=rli, final_message=(final or "")[:20000])

    # --- studio done -----------------------------------------------------------------------------

    def done(self, job, args, out):
        """The job's last verb. implement: gates on HEAD, then the ledger. review: the notes."""
        wt = job["worktree"]
        # The CLI sends the file's text (read inside the job, whose /tmp is its own) and path.
        mode, path, text = (args + [None, None, None])[:3]
        if mode not in ("--summary", "--blocked", "--review") or text is None:
            raise ValueError("usage: studio done --summary <file> | --blocked <file> | --review <file>")
        if not text.strip():
            raise ValueError(f"{path} is empty")
        text = text[:20000]
        # A summary file left in the worktree doesn't count as uncommitted work.
        real = os.path.realpath(path or "")
        rel = os.path.relpath(real, os.path.realpath(wt)) if real.startswith(os.path.realpath(wt) + os.sep) else None
        if mode == "--review":
            job.set(result={"kind": "review", "text": text})
            return {"recorded": "review"}
        if mode == "--blocked":
            job.set(result={"kind": "blocked", "text": text})
            return {"recorded": "blocked"}
        g = ["git", "-C", wt]
        dirty = subprocess.run(g + ["status", "--porcelain"], capture_output=True, text=True).stdout
        dirty = [l for l in dirty.splitlines() if l.strip() and l[3:].strip('"') != rel]
        if dirty:
            raise ValueError(f"commit your work first: {len(dirty)} uncommitted change(s)")
        head = subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        base = subprocess.run(g + ["rev-parse", "origin/HEAD"], capture_output=True, text=True).stdout.strip()
        if head == base:
            raise ValueError("no commits on the branch yet")
        out(f"running the cold gates (full) on {head[:10]}")
        res = self.broker(["gate", "--worktree", f"{job['game']}/{job.id}", "full"], out)
        if res.get("rc") != 0:
            raise RuntimeError(json.dumps({"gate": "failed", "detail": res.get("result")})[:4000])
        gate = res.get("result") or {}
        ledger = {"revision": head, "gates": gate, "play": "not played yet"}
        job.set(result={"kind": "summary", "text": text, "revision": head}, ledger=ledger)
        return {"done": True, "revision": head, "gates": "passed"}

    def broker(self, args, out):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(common.UNITYD_SOCKET)
        common.send(s, {"verb": args[0], "args": args[1:]})
        final = {}
        for msg in common.read_lines(s):
            if "out" in msg:
                out(msg["out"])
            if "rc" in msg:
                final = msg
        return final

    # --- after a job ------------------------------------------------------------------------------

    def finish(self, job):
        self.scan_stream(job)
        res = job.get("result") or {}
        item = job["item"]
        try:
            if job.get("stop_reason"):
                self.fail(job, job["stop_reason"], label=None)
            elif job["kind"] == "implement" and res.get("kind") == "summary":
                self.publish(job, res)
            elif job["kind"] == "implement" and res.get("kind") == "blocked":
                self.push(job)
                self.gl.relabel(item["kind"], item["project_id"], item["iid"], add=[BLOCKED], remove=[RUNNING])
                self.gl.note(item["kind"], item["project_id"], item["iid"],
                             f"Blocked (job `{job.id}`):\n\n{res['text']}")
                job.set(state="blocked", finished_at=now().isoformat())
            elif job["kind"] == "review" and res.get("kind") == "review":
                self.gl.note("mr", item["project_id"], item["iid"],
                             f"**Review by {job['vendor']}** (job `{job.id}`)\n\n{res['text']}")
                self.gl.relabel("mr", item["project_id"], item["iid"], add=[REVIEW], remove=[RUNNING])
                job.set(state="done", finished_at=now().isoformat())
            else:
                self.push(job)
                self.fail(job, "the job ended without `studio done`")
        except (GitLabError, RuntimeError, subprocess.SubprocessError) as e:
            log(f"{job.id}: write-back failed: {e}")
            self.fail(job, f"write-back failed: {e}")
        self.cleanup(job)

    @staticmethod
    def remote_head(wt, branch):
        """The branch's commit on GitLab as the worktree's clone saw it, or "" if it had none."""
        return subprocess.run(["git", "-C", wt, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                              capture_output=True, text=True).stdout.strip()

    def push(self, job):
        """The job's branch to GitLab, if it has commits; as root, as the agent account.

        Leased on where the branch stood when the job began (job["lease"]; "" = it must still
        not exist), so the job may rewrite its own branch, and a continued branch (another
        approval on the same issue) moves on from its MR's head, but nobody else's push in the
        meantime is ever overwritten. A bare --force-with-lease can't do this: pushing to a
        URL, git has no remote-tracking ref to compare, and refuses any branch that exists."""
        wt = job["worktree"]
        ahead = subprocess.run(["git", "-C", wt, "rev-list", "--count", "origin/HEAD..HEAD"],
                               capture_output=True, text=True).stdout.strip()
        if ahead in ("", "0"):
            return None
        ref = f"refs/heads/{job['branch']}"
        url = f"{self.cfg['gitlab_url']}/{self.cfg['gitlab_group']}/{job['game']}.git"
        head = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        lease = job.get("lease")
        if lease is None:                     # a job journalled before leases were recorded
            lease = self.remote_head(wt, job["branch"])
        try:
            admin.git_root(self.cfg, ["push", "--quiet", f"--force-with-lease={ref}:{lease}", url, f"HEAD:{ref}"], cwd=wt)
        except RuntimeError as e:
            if "stale info" not in str(e):
                raise
            # A write-back retried after this push already went through finds its own commit.
            if admin.git_root(self.cfg, ["ls-remote", url, ref], cwd=wt).split()[:1] != [head]:
                raise RuntimeError(f"{job['branch']} changed on GitLab while the job ran (it expected "
                                   f"{lease[:10] or 'no such branch'}), so the job's work wasn't pushed over it") from None
        job.set(pushed=head)
        return head

    def unpushed(self, job):
        """A failed job whose commits never reached GitLab: its worktree is kept, for republish."""
        wt = job.get("worktree")
        if job.get("state") != "failed" or not wt or not os.path.isdir(wt):
            return False
        head = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if not head or head == job.get("pushed"):
            return False
        n = subprocess.run(["git", "-C", wt, "rev-list", "--count", f"{job.get('lease') or 'origin/HEAD'}..HEAD"],
                           capture_output=True, text=True).stdout.strip()
        return n not in ("", "0")

    def publish(self, job, res):
        item, pid = job["item"], job["item"]["project_id"]
        head = self.push(job)
        if head != res["revision"]:
            raise RuntimeError(f"HEAD {head} isn't the gated revision {res['revision']}: the job committed after `studio done`")
        ledger = job["ledger"]
        dep = self.deposit(job, head)
        if "object" in dep:
            ledger["play"] = f"not played yet. The build (`{dep['object'][:12]}`) is on the portal: {dep['url']}"
        else:
            ledger["play"] = f"none, the build didn't reach the depot ({dep['error']})"
        job.set(ledger=ledger)
        steps = ledger["gates"].get("steps", [])
        rows = "\n".join(f"| {s['step']} | {'passed' if s.get('passed') else 'FAILED'} | "
                         f"{(s.get('tests') or {}).get('passed', '')}/{(s.get('tests') or {}).get('total', '')} |"
                         for s in steps)
        body = (f"{res['text']}\n\n---\n\n**Ledger for `{head[:10]}`** (job `{job.id}`, {job['vendor']})\n\n"
                f"| Gate (cold) | Result | Tests |\n|---|---|---|\n{rows}\n\n"
                f"Play evidence: {ledger['play']}.\n\nCloses {item['ref']}. Approved by @{job['approval']['by']}.")
        mr = self.gl.mr_for_branch(pid, job["branch"])
        if mr:
            self.gl.update_mr(pid, mr["iid"], description=body)
            self.gl.note("mr", pid, mr["iid"], f"New revision `{head[:10]}` from job `{job.id}`; gates passed.")
        else:
            mr = self.gl.open_mr(pid, job["branch"], self.gl.project(pid)["default_branch"],
                                 f"{item['title']} ({item['ref'].split('/')[-1]})", body, [REVIEW])
        self.gl.status(pid, head, "success", "studio/gates", "cold gates passed on cell-" + self.cfg["owner"],
                       target_url=dep.get("url"))
        self.gl.relabel("issue", pid, item["iid"], add=[REVIEW], remove=[RUNNING])
        self.gl.note("issue", pid, item["iid"], f"Done: {mr['web_url']} (gates passed on `{head[:10]}`).")
        job.set(state="done", mr=mr["iid"], mr_url=mr["web_url"], finished_at=now().isoformat())
        log(f"{job.id}: published {mr['web_url']}")
        if "object" in dep:
            m = hubmod.manifest(job["game"], ledger["gates"], dep, job=job.id, mr=mr)
            job.set(hub={"manifest": m, "posted": False})
            try:
                self.post_manifest(job)
            except (hubmod.HubError, OSError, ssl.SSLError) as e:
                log(f"{job.id}: manifest not taken yet ({e}); the next poll retries")
        if self.a.get("review_after_implement", True):
            self.queue_review(job, mr)

    # --- the hub's depot and portal ----------------------------------------------------------------

    def deposit(self, job, head):
        """The gated Linux build into the depot, before the MR is written: its body links it."""
        if not self.hub:
            return {"error": "this cell has no hub settings"}
        try:
            dep = hubmod.deposit(self.hub, job["game"], job["ledger"]["gates"])
        except (hubmod.HubError, OSError, ValueError, ssl.SSLError) as e:
            log(f"{job.id}: deposit failed: {e}")
            return {"error": str(e)[:200]}
        dep["url"] = f"{self.hub.portal}/g/{job['game']}/r/{head}"
        log(f"{job.id}: build {dep['object'][:12]} in the depot")
        return dep

    def post_manifest(self, job, timeout=60):
        h = job["hub"]
        self.hub.call("POST", "/v1/builds", h["manifest"], timeout=timeout)
        h["posted"] = True
        job.set(hub=h)

    def sync_hub(self):
        """Manifests the hub hasn't taken yet; every 10 minutes, the state of MRs still open there.
        A hub that doesn't answer is tried again at the next poll."""
        if not self.hub:
            return
        try:
            self.post_plays()
        except (OSError, ssl.SSLError) as e:
            log(f"hub sync: the hub didn't answer ({e}); next poll")
            return
        except (hubmod.HubError, GitLabError) as e:
            log(f"hub sync: plays: {e}")
        check_mrs = time.time() - self.mr_synced > 600
        cutoff = now() - dt.timedelta(days=31)
        for job in Job.all():
            h = job.get("hub")
            if not h or not job.get("finished_at") or ts(job["finished_at"]) < cutoff:
                continue
            mr = h["manifest"].get("mr")
            try:
                if not h.get("posted"):
                    self.post_manifest(job, timeout=15)
                elif check_mrs and mr and mr.get("state") == "opened":
                    cur = self.gl.item("mr", mr["project_id"], mr["iid"])
                    if (cur["state"], cur["title"]) != (mr["state"], mr["title"]):
                        self.hub.call("POST", "/v1/mr-state", {"game": job["game"], "iid": mr["iid"],
                                                               "state": cur["state"], "title": cur["title"]},
                                      timeout=15)
                        mr.update(state=cur["state"], title=cur["title"])
                        job.set(hub=h)
            except (OSError, ssl.SSLError) as e:
                log(f"hub sync: the hub didn't answer ({e}); next poll")
                return
            except (hubmod.HubError, GitLabError) as e:
                log(f"{job.id}: hub sync: {e}")
        if check_mrs:
            self.mr_synced = time.time()

    def post_plays(self):
        """Play evidence: each session someone watched, on a build this cell uploaded, becomes a
        note on its MR and a studio/played status on the revision, once. The name is the
        person's Authentik one, written plainly so GitLab notifies no one by mistake."""
        for p in self.hub.call("GET", "/v1/plays", timeout=15) or []:
            mins = max(1, round(p["viewer_seconds"] / 60))
            url = f"{self.hub.portal}/g/{p['game']}/r/{p['revision']}"
            if p.get("project_id") and p.get("mr_iid"):
                self.gl.note("mr", p["project_id"], p["mr_iid"],
                             f"**Played** by {p['person']} for {mins} min on `{p['revision'][:10]}` "
                             f"(session `{p['id']}` on the Forge, ended: {p['end_reason']}). [The build]({url})")
                self.gl.status(p["project_id"], p["revision"], "success", "studio/played",
                               f"played by {p['person']}, {mins} min", target_url=url)
            self.hub.call("POST", f"/v1/plays/{p['id']}/reported", {}, timeout=15)
            log(f"play {p['id']}: {p['person']}, {mins} min on {p['game']}@{p['revision'][:10]} reported")

    def queue_review(self, job, mr):
        """The other vendor reviews the MR, without another approval: it's part of this one."""
        vendor = OTHER[job["vendor"]]
        if not self.preflight(vendor)[0]:
            log(f"{job.id}: review skipped, {vendor} is over its ceilings")
            return
        jid = f"{job['game']}-mr{mr['iid']}-{int(time.time()) % 10**7:07d}"
        rj = Job(jid, {"id": jid, "kind": "review", "vendor": vendor, "state": "queued", "game": job["game"],
                       "item": {"kind": "mr", "project_id": mr["project_id"], "iid": mr["iid"],
                                "ref": mr["references"]["full"], "title": mr["title"], "web_url": mr["web_url"],
                                "source_branch": mr["source_branch"], "target_branch": mr["target_branch"]},
                       "approval": job["approval"], "claimed_at": now().isoformat(), "after": job.id})
        rj.save()
        with open(os.path.join(rj.dir, "snapshot.md"), "w", encoding="utf-8") as f:
            f.write(f"# {mr['title']}\n\n{mr.get('description') or ''}\n")
        self.gl.relabel("mr", mr["project_id"], mr["iid"], add=[RUNNING])
        log(f"{jid}: review queued ({vendor})")

    def fail(self, job, why, label=FAILED):
        item = job["item"]
        job.set(state="failed", error=why, finished_at=now().isoformat())
        try:
            self.gl.relabel(item["kind"], item["project_id"], item["iid"],
                            add=[label] if label else [], remove=[RUNNING])
            tail = ""
            err = os.path.join(job.dir, "stderr.log")
            if os.path.exists(err):
                with open(err, encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-15:])
            kept = ""
            if self.unpushed(job):
                kept = (f"\n\nThe job's commits never reached GitLab, so they are kept on `cell-{self.cfg['owner']}` "
                        f"at `{job['worktree']}`. Once the cause is fixed, `studio-admin republish {job.id}` "
                        "(root on the cell) publishes them, with no new job.")
            self.gl.note(item["kind"], item["project_id"], item["iid"],
                         f"Job `{job.id}` ended: {why}." + kept
                         + (f"\n\n```\n{tail[-3000:]}\n```" if tail.strip() else ""))
        except GitLabError as e:
            log(f"{job.id}: couldn't report the failure: {e}")
        log(f"{job.id}: failed ({why})")

    def cleanup(self, job):
        """The worktree goes once the job's work is in GitLab; its editor stops with it. A failed
        job's commits that GitLab doesn't have stay (`studio-admin republish`)."""
        wt = job.get("worktree")
        if job.get("state") in ("done", "blocked", "failed") and wt and os.path.isdir(wt):
            subprocess.run(["systemctl", "stop", f"studio-editor-{job['game']}-{job.id}.service"],
                           capture_output=True, check=False)
            if self.a.get("keep_worktrees") or self.unpushed(job):
                return
            shutil.rmtree(wt, ignore_errors=True)

    # --- verbs ------------------------------------------------------------------------------------

    def job_of(self, caller):
        if not caller.job:
            raise PermissionError("`studio done` is for jobs; in a pair session, commit and push yourself")
        try:
            return Job(caller.job)
        except (OSError, ValueError):
            raise PermissionError(f"no job {caller.job}") from None

    def v_done(self, caller, args, out):
        return self.done(self.job_of(caller), args, out)

    def v_jobs(self, caller, args, out):
        return [{"id": j.id, "kind": j["kind"], "vendor": j["vendor"], "state": j["state"],
                 "item": j["item"]["ref"], "mr": j.get("mr_url"), "session": j.get("session_id")}
                for j in Job.all()[-20:]]

    def v_attach(self, caller, args, out):
        """Stop a running job's unit and hand its session to the person, as agent, in its worktree."""
        if caller.uid not in (0, self.owner_uid):
            raise PermissionError("attach is for this cell's person")
        if len(args) != 1:
            raise ValueError("usage: studio attach <job>")
        job = Job(args[0])
        self.scan_stream(job)
        if self.unit_active(job):
            subprocess.run(["systemctl", "kill", "--signal=SIGINT", job.unit], capture_output=True, check=False)
            time.sleep(5)
            subprocess.run(["systemctl", "stop", job.unit], capture_output=True, check=False)
        job.set(state="attached", stop_reason=None)
        resume = ("claude --resume " if job["vendor"] == "claude" else "codex resume ") + (job.get("session_id") or "<session>")
        return {"job": job.id, "worktree": job["worktree"],
                "take_over": f"sudo -iu agent; cd {job['worktree']}; {resume}",
                "hand_back": f"studio detach {job.id} (or `studio done` from your session)"}

    def v_detach(self, caller, args, out):
        if caller.uid not in (0, self.owner_uid):
            raise PermissionError("detach is for this cell's person")
        job = Job(args[0])
        if job["state"] != "attached":
            raise ValueError(f"{job.id} isn't attached")
        job.set(state="queued", resume=True)
        return {"job": job.id, "state": "queued; it resumes at the next poll"}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        conn, d = self.request, self.server.agentd
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
            if caller.uid not in (0, d.agent_uid, d.owner_uid):
                raise PermissionError(f"{caller.user} may not use studio-agentd")
            fn = getattr(d, f"v_{verb}", None)
            if not fn:
                raise ValueError(f"unknown verb {verb!r}")
            if verb in ("attach", "detach"):
                with d.lock:
                    result = fn(caller, args, out)
            else:  # `done` runs the gates for minutes; it touches only its own job
                result = fn(caller, args, out)
        except PermissionError as e:
            rc, result = 13, {"error": str(e)}
        except ValueError as e:
            rc, result = 2, {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            rc, result = 1, {"error": str(e)[:4000]}
        log(f"{caller} {verb} -> {rc}")
        try:
            common.send(conn, {"rc": rc, "result": result})
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    cfg = common.load_config()
    os.makedirs(JOBS_DIR, mode=0o700, exist_ok=True)
    d = Agentd(cfg)
    me = d.gl.me()["username"]
    if me != cfg["gitlab_agent"]:
        log(f"the token is {me}'s, not {cfg['gitlab_agent']}'s; refusing to start")
        return 1
    if os.path.exists(AGENTD_SOCKET):
        os.unlink(AGENTD_SOCKET)
    server = Server(AGENTD_SOCKET, Handler)
    os.chmod(AGENTD_SOCKET, 0o666)
    server.agentd = d
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"studio-agentd: {me}, approvers {d.a['approvers']}, polling every {d.a.get('poll_seconds', 60)} s")
    d.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
