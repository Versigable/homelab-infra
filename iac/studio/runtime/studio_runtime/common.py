"""Shared plumbing: paths, the socket protocol, who is calling, and running things as a user."""

import json
import os
import pwd
import re
import socket
import struct
import subprocess

# Sockets live in /run/studio (the daemons' RuntimeDirectory); tests point elsewhere.
RUN_DIR = os.environ.get("STUDIO_RUN_DIR", "/run/studio")
UNITYD_SOCKET = os.path.join(RUN_DIR, "unityd.sock")
# /srv/studio/<game>/{mirror.git, work/<name>, gate, builds/<rev>, results/<rev>}
STUDIO_ROOT = os.environ.get("STUDIO_ROOT", "/srv/studio")
STATE_DIR = os.environ.get("STUDIO_STATE_DIR", "/var/lib/studio")
CONFIG_FILE = os.environ.get("STUDIO_CONFIG", "/etc/studio/cell.json")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
JOB_UNIT_RE = re.compile(r"/studio-job-([a-z0-9-]+)\.service$")


def load_config():
    """The cell's settings, written by Ansible (role studio_cell)."""
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


# --- the socket protocol --------------------------------------------------------------------
# One request per connection: a JSON line {"verb": ..., "args": [...]}. The daemon answers
# with JSON lines: {"out": text} for progress, then {"rc": n, "result": ...} last.

def send(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode())


def read_lines(conn):
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield json.loads(line)
    if buf.strip():
        yield json.loads(buf)


def read_request(conn, limit=1 << 20):
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise ValueError("request too large")
    req = json.loads(buf.split(b"\n", 1)[0])
    if not isinstance(req, dict) or not isinstance(req.get("verb"), str):
        raise ValueError("bad request")
    args = req.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    return req["verb"], args


# --- who is calling -------------------------------------------------------------------------

class Caller:
    """The peer of a connection, from the kernel: uid, pid, its job (if any), its directory."""

    def __init__(self, conn):
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        self.pid, self.uid, self.gid = struct.unpack("3i", creds)
        self.user = pwd.getpwuid(self.uid).pw_name
        self.job = None
        try:
            with open(f"/proc/{self.pid}/cgroup", encoding="utf-8") as f:
                for line in f:
                    m = JOB_UNIT_RE.search(line.strip())
                    if m:
                        self.job = m.group(1)
        except OSError:
            pass
        try:
            self.cwd = os.path.realpath(os.readlink(f"/proc/{self.pid}/cwd"))
        except OSError:
            self.cwd = None

    def __str__(self):
        return f"{self.user}[{self.pid}]" + (f" job {self.job}" if self.job else " (pair)")


class Worktree:
    """A game checkout an agent works in: /srv/studio/<game>/work/<name>."""

    def __init__(self, game, name):
        if not (NAME_RE.match(game) and NAME_RE.match(name)):
            raise ValueError("bad game or worktree name")
        self.game, self.name = game, name
        self.path = os.path.join(STUDIO_ROOT, game, "work", name)
        self.game_dir = os.path.join(STUDIO_ROOT, game)

    @property
    def key(self):
        return f"{self.game}-{self.name}"

    def project_version(self):
        with open(os.path.join(self.path, "ProjectSettings", "ProjectVersion.txt"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("m_EditorVersion:"):
                    return line.split(":", 1)[1].strip()
        return None


def worktree_of(caller):
    """The worktree the caller is standing in, or an error: verbs act on the caller's own
    checkout, never on a path it names. A job may use only its own worktree."""
    root = os.path.realpath(STUDIO_ROOT) + os.sep
    if not caller.cwd or not caller.cwd.startswith(root):
        raise PermissionError("run studio inside a worktree (/srv/studio/<game>/work/<name>)")
    parts = caller.cwd[len(root):].split(os.sep)
    if len(parts) < 3 or parts[1] != "work":
        raise PermissionError("run studio inside a worktree (/srv/studio/<game>/work/<name>)")
    wt = Worktree(parts[0], parts[2])
    if caller.job and wt.name != caller.job:
        raise PermissionError(f"job {caller.job} may use only its own worktree")
    if not os.path.isfile(os.path.join(wt.path, "ProjectSettings", "ProjectVersion.txt")):
        raise PermissionError(f"{wt.path} is not a Unity project")
    return wt


# --- running things as a user ---------------------------------------------------------------

def user_env(user, extra=None):
    pw = pwd.getpwnam(user)
    env = {"HOME": pw.pw_dir, "USER": user, "LOGNAME": user, "LANG": "C.UTF-8",
           "PATH": "/usr/local/bin:/usr/bin:/bin"}
    env.update(extra or {})
    return env


def run_as(user, argv, env=None, cwd=None, timeout=None):
    """Run argv as user, with all of its groups (the work group among them), capturing output."""
    pw = pwd.getpwnam(user)
    return subprocess.run(argv, user=user, group=pw.pw_gid, extra_groups=os.getgrouplist(user, pw.pw_gid),
                          env=user_env(user, env), cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)
