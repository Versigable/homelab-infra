"""The AI vendors a cell's jobs may run, in the order its person prefers them.

Two are built in, Claude and Codex: studio-job-run knows their flags, and they count once the
user agent is signed in to them (`claude auth status`, `codex login status`). Any other the
person adds themselves, as a command (`studio vendor add <name> -- <command...>`): a job runs
it headless in its worktree, with the prompt on stdin or wherever `{prompt}` appears. The list
is studio-agentd's, in /var/lib/studio/vendors.json (root's), so a job can't change what later
jobs run. The order says who does the work: an implement job goes to the first vendor the cell
has, a review to the next one, or to a fresh session of the same one on a cell with one.
"""

import datetime as dt
import json
import os
import re
import shlex
import time

from . import common

BUILTIN = ("claude", "codex")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
FILE = os.path.join(common.STATE_DIR, "vendors.json")
SIGNIN_TTL = 300                     # seconds a built-in's sign-in check is trusted
LABEL_COLOR = "#5843AD"


def label(name):
    return f"vendor:{name}"


class Vendors:
    def __init__(self, cfg, path=FILE):
        self.cfg = cfg
        self.path = path
        self._signin = {}            # built-in name -> (checked at, signed in)
        self.data = self._load()

    # --- the list --------------------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            d = {}
        d.setdefault("order", list(BUILTIN))
        d.setdefault("added", {})
        d.setdefault("disabled", [])
        return d

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def names(self):
        """Every vendor the cell knows, in the person's order (Claude first unless they say)."""
        known = list(BUILTIN) + [n for n in self.data["added"] if n not in BUILTIN]
        return [n for n in self.data["order"] if n in known] + [n for n in known if n not in self.data["order"]]

    def signed_in(self, name, fresh=False):
        """A built-in counts once agent is signed in to it; an added one once it's added."""
        if name not in BUILTIN:
            return name in self.data["added"]
        at, ok = self._signin.get(name, (0, False))
        if fresh or time.time() - at > SIGNIN_TTL:
            argv = ["claude", "auth", "status"] if name == "claude" else ["codex", "login", "status"]
            try:
                r = common.run_as(self.cfg["agent_user"], argv, timeout=60)
                ok = r.returncode == 0
                if ok and name == "claude":
                    try:
                        ok = bool(json.loads(r.stdout).get("loggedIn", True))
                    except ValueError:
                        pass
            except Exception as e:  # noqa: BLE001 - a missing CLI, a timeout: not signed in
                ok = False
                print(f"vendors: {name}'s sign-in check failed: {e}", flush=True)
            self._signin[name] = (time.time(), ok)
        return ok

    def available(self, fresh=False):
        """The vendors a job may run with now, in order."""
        return [n for n in self.names() if n not in self.data["disabled"] and self.signed_in(n, fresh)]

    def command(self, name):
        """An added vendor's {run, resume} (argv lists), or None for a built-in."""
        return self.data["added"].get(name)

    def describe(self, fresh=True):
        have = self.available(fresh)
        out = []
        for n in self.names():
            v = {"name": n, "kind": "built in" if n in BUILTIN else "added"}
            if n in BUILTIN:
                v["signed_in"] = self.signed_in(n)
            else:
                a = self.data["added"][n]
                v["command"] = shlex.join(a["run"])
                if a.get("resume"):
                    v["resume"] = shlex.join(a["resume"])
                v["added"] = f"{a.get('by')} at {a.get('at')}"
            if n in self.data["disabled"]:
                v["removed"] = True
            v["label"] = label(n)
            v["does"] = ("implements" if have and have[0] == n else
                         "reviews" if len(have) > 1 and have[1] == n else
                         "on request (label)" if n in have else "nothing until it's available")
            out.append(v)
        return out

    # --- the person's changes --------------------------------------------------------------

    def _check_name(self, name):
        if not NAME_RE.match(name or ""):
            raise ValueError("a vendor name is lowercase letters, digits, '.', '_' or '-', up to 32 (it becomes "
                             "the label vendor:<name>)")

    def add(self, name, argv, by):
        self._check_name(name)
        if name in BUILTIN:
            if argv:
                raise ValueError(f"{name} is built in; give your own command another name "
                                 f"(e.g. `studio vendor add {name}-mine -- ...`)")
            if name in self.data["disabled"]:
                self.data["disabled"].remove(name)
            self.save()
            return
        if not argv:
            raise ValueError("usage: studio vendor add <name> -- <command...> (the prompt arrives on stdin, "
                             "or where {prompt} appears)")
        old = self.data["added"].get(name) or {}
        self.data["added"][name] = {"run": list(argv), "resume": old.get("resume"), "by": by,
                                    "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        if name in self.data["disabled"]:
            self.data["disabled"].remove(name)
        if name not in self.data["order"]:
            self.data["order"] = self.names()           # an added vendor goes last
            if name not in self.data["order"]:
                self.data["order"].append(name)
        self.save()

    def set_resume(self, name, argv):
        if name in BUILTIN:
            raise ValueError(f"{name} is built in; studio-job-run already resumes it")
        if name not in self.data["added"]:
            raise ValueError(f"no vendor {name!r} here (studio vendor list)")
        self.data["added"][name]["resume"] = list(argv) or None
        self.save()

    def remove(self, name):
        if name in BUILTIN:
            if name not in self.data["disabled"]:
                self.data["disabled"].append(name)
        elif name in self.data["added"]:
            del self.data["added"][name]
            self.data["order"] = [n for n in self.data["order"] if n != name]
        else:
            raise ValueError(f"no vendor {name!r} here (studio vendor list)")
        self.save()

    def reorder(self, names):
        known = self.names()
        bad = [n for n in names if n not in known]
        if bad or not names:
            raise ValueError(f"usage: studio vendor order <name>... (known: {', '.join(known)})")
        self.data["order"] = list(dict.fromkeys(names)) + [n for n in known if n not in names]
        self.save()
