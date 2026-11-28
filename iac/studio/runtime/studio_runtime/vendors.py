"""The AI vendors a cell's jobs may run, in the order its person prefers them.

Two are built in, Claude and Codex: studio-job-run knows their flags, and they count once the
user agent is signed in to them (`claude auth status`, `codex login status`). Any other the
person adds themselves, as a command (`studio vendor add <name> -- <command...>`): a job runs
it headless in its worktree, with the prompt on stdin or wherever `{prompt}` appears. The list
is studio-agentd's, in /var/lib/studio/vendors.json (root's), so a job can't change what later
jobs run. The order says who does the work: an implement job goes to the first vendor the cell
has, a review to the next one, or to a fresh session of the same one on a cell with one.

The person may also say which model a built-in runs and how hard it thinks (`studio vendor set
claude --model fable --effort high`); unset, each tool uses its own default. An added vendor's
command carries its own flags. What a job actually ran with is read back afterwards (ran()),
so its result can say.
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
# A model's name as the tools take it: an alias (fable, opus) or a full id (gpt-6.2-sol).
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"),
           "codex": ("minimal", "low", "medium", "high", "xhigh")}
SESSION_RE = re.compile(r"^[0-9a-f-]{20,40}$")


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
        d.setdefault("settings", {})         # built-in name -> {"model": ..., "effort": ...}
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
                s = self.settings(n)
                v["model"] = s.get("model") or "its default"
                v["effort"] = s.get("effort") or "its default"
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

    def settings(self, name):
        """{"model", "effort"} the person set for a built-in; a key is missing when unset."""
        s = (self.data.get("settings") or {}).get(name) or {}
        return {k: v for k, v in s.items() if k in ("model", "effort") and v}

    def ran(self, name, model=None, session=None):
        """What a finished job ran with, for its result's label: {"model", "effort", "effort_from"}.
        The model is the one the tool reported (Claude names it in its stream; Codex in its
        session file), else the one set. The effort is the one set, else what the agent user's
        own settings say for that model, else the model's own default where the tool records
        it. Anything not known is None. The files read are agent's, so nothing in them is
        trusted beyond a short name."""
        asked = self.settings(name)
        out = {"model": model or None, "effort": asked.get("effort"), "effort_from": "set" if asked.get("effort") else None}
        if name not in BUILTIN:
            return out
        home = self._home()
        try:
            if name == "codex" and session and SESSION_RE.match(session):
                # Codex's own record of the session names the model and the effort it used.
                model, effort = self._codex_session(home, session)
                out["model"] = out["model"] or model
                if effort:
                    out.update(effort=effort, effort_from="set" if asked.get("effort") == effort else None)
            out["model"] = out["model"] or asked.get("model")
            if out["model"] and not MODEL_RE.match(out["model"]):
                out["model"] = None
            if not out["effort"] and out["model"]:
                if name == "claude":
                    e = ((self._json(os.path.join(home, ".claude", "settings.json")).get("modelSettings") or {})
                         .get(out["model"]) or {}).get("effortLevel")
                    out.update(effort=e, effort_from="agent's own setting") if isinstance(e, str) else None
                else:
                    m = re.search(r'(?m)^\s*model_reasoning_effort\s*=\s*"([a-z]{1,12})"', self._text(os.path.join(home, ".codex", "config.toml")))
                    if m:
                        out.update(effort=m.group(1), effort_from="agent's own setting")
                    else:
                        for row in self._json(os.path.join(home, ".codex", "models_cache.json")).get("models") or []:
                            if isinstance(row, dict) and row.get("slug") == out["model"] and isinstance(row.get("default_reasoning_level"), str):
                                out.update(effort=row["default_reasoning_level"], effort_from="the model's default")
            if out["effort"] and not re.fullmatch(r"[a-z]{1,12}", out["effort"]):
                out.update(effort=None, effort_from=None)
        except Exception as e:  # noqa: BLE001 - a label is never worth a job's write-back
            print(f"vendors: couldn't read what {name} ran with: {e}", flush=True)
        return out

    def _home(self):
        import pwd
        return pwd.getpwnam(self.cfg["agent_user"]).pw_dir

    @staticmethod
    def _text(path, limit=4 << 20):
        """A file of agent's, read without following a link in its last step; "" if it can't be."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return ""
        with os.fdopen(fd, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace")

    @classmethod
    def _json(cls, path):
        try:
            d = json.loads(cls._text(path))
        except (ValueError, RecursionError):
            return {}
        return d if isinstance(d, dict) else {}

    @classmethod
    def _codex_session(cls, home, session):
        """(model, effort) from Codex's own record of a session (its rollout file); None for
        what it doesn't say."""
        import glob
        for path in glob.glob(os.path.join(home, ".codex", "sessions", "*", "*", "*", f"rollout-*-{session}.jsonl"))[:3]:
            text = cls._text(path, 2 << 20)
            m = re.search(r'"model":"([A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79})"', text)
            e = re.search(r'"(?:reasoning_effort|effort)":"([a-z]{1,12})"', text)
            if m or e:
                return (m.group(1) if m else None), (e.group(1) if e else None)
        return None, None

    # --- the person's changes --------------------------------------------------------------

    def set(self, name, model=None, effort=None):
        """A built-in's model and effort. None leaves one as it is; "default" unsets it."""
        if name not in BUILTIN:
            raise ValueError(f"{name} is a vendor you added: its model and effort are flags in its own command "
                             "(`studio vendor add` it again with them)" if name in self.data["added"]
                             else f"no built-in vendor {name!r} (claude, codex)")
        s = dict((self.data.get("settings") or {}).get(name) or {})
        if model is not None:
            if model != "default" and not MODEL_RE.match(model):
                raise ValueError("a model is its name as the tool takes it, e.g. fable, opus, claude-fable-5-2, gpt-6.2-sol")
            s["model"] = None if model == "default" else model
        if effort is not None:
            if effort != "default" and effort not in EFFORTS[name]:
                raise ValueError(f"{name}'s efforts are {', '.join(EFFORTS[name])} (or default)")
            s["effort"] = None if effort == "default" else effort
        self.data.setdefault("settings", {})[name] = {k: v for k, v in s.items() if v}
        self.save()

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
