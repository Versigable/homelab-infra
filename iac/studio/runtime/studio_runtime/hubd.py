"""studio-hubd: the hub's daemon (design: architecture §5; vault notes/step-2b-1-plan).

Runs as studio-hub (role studio_hub), with two listeners:

  :8443  cells and the Forge, over mutual TLS. A caller is the CN of a certificate the hub's
         CA signed, and must connect from that host's address (hub.json "clients", from the
         inventory). The depot lives here: immutable objects by sha256, and build manifests,
         each tied to the person whose cell uploaded it.
  :8080  the portal. Only Traefik reaches it (through studio-gw), after Authentik, so the
         person is X-authentik-username, trusted from Traefik's address only. Traefik also
         asks it (forwardAuth, /auth/stream) before every request to a play slot.

The broker: a person presses Play, the hub records a session on a free slot, and the Forge's
forge-playd, which syncs every few seconds, starts it and reports it ready and how many viewers
it has. A session ends when its person ends it, after idle_minutes with no viewer, at
cap_hours, or if it never starts. A session someone watched becomes play evidence, which the
uploading cell's studio-agentd posts to the MR.

State on the depot disk (/srv/depot): objects/<ab>/<sha256>, tmp/, and depot.db (SQLite).
MR builds expire after build_days; an object goes once no build refers to it.
"""

import datetime as dt
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import shutil
import socketserver
import sqlite3
import ssl
import sys
import threading
import time
import traceback
import urllib.parse

from . import VERSION
from .hub import SHA_RE

CONFIG_FILE = os.environ.get("STUDIO_HUB_CONFIG", "/etc/studio-hub/hub.json")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REV_RE = re.compile(r"^[0-9a-f]{40}$")
TARGETS = ("linux", "windows")

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
  sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL, by TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS builds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  game TEXT NOT NULL, revision TEXT NOT NULL, target TEXT NOT NULL,
  object TEXT NOT NULL REFERENCES objects(sha256), object_bytes INTEGER NOT NULL,
  exe TEXT NOT NULL, exe_sha256 TEXT, bytes INTEGER,
  owner TEXT NOT NULL, cell TEXT NOT NULL, job TEXT,
  project_id INTEGER, mr_iid INTEGER, mr_title TEXT, mr_url TEXT, mr_state TEXT,
  gates TEXT NOT NULL, at TEXT NOT NULL, expires TEXT,
  UNIQUE (game, revision, target, cell));
CREATE INDEX IF NOT EXISTS builds_mr ON builds (game, mr_iid);
-- A play session. What it played is copied in, so the record outlives the build's expiry.
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, build INTEGER NOT NULL, game TEXT NOT NULL, revision TEXT NOT NULL,
  object TEXT NOT NULL, exe TEXT NOT NULL, cell TEXT NOT NULL, project_id INTEGER, mr_iid INTEGER,
  person TEXT NOT NULL, slot INTEGER NOT NULL,
  state TEXT NOT NULL,             -- requested, starting, live, ended
  at TEXT NOT NULL, live_at TEXT, ended_at TEXT, end_reason TEXT, error TEXT,
  viewer_seconds INTEGER NOT NULL DEFAULT 0, last_viewer_at TEXT,
  reported INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sessions_state ON sessions (state);
"""
ACTIVE = ("requested", "starting", "live")


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.isoformat(timespec="seconds")


def log(msg):
    print(msg, flush=True)


class Forbidden(Exception):
    pass


class BadRequest(Exception):
    pass


# --- the depot ---------------------------------------------------------------------------------

class Depot:
    def __init__(self, cfg):
        self.root = cfg["depot"]
        self.build_days = int(cfg.get("build_days", 30))
        self.max_bytes = int(cfg.get("max_object_bytes", 4 << 30))
        for d in ("objects", "tmp"):
            os.makedirs(os.path.join(self.root, d), mode=0o750, exist_ok=True)
        self.db_path = os.path.join(self.root, "depot.db")
        with self.db() as db:
            db.executescript(SCHEMA)

    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def path(self, sha):
        return os.path.join(self.root, "objects", sha[:2], sha)

    def has(self, sha):
        return os.path.isfile(self.path(sha))

    def receive(self, sha, rfile, length, by):
        """Stream length bytes into the depot as object sha; refuse if the hash differs."""
        if length > self.max_bytes:
            raise BadRequest(f"object larger than {self.max_bytes} bytes")
        if self.has(sha):
            # Drain the body so the client sees a clean answer.
            while length > 0:
                chunk = rfile.read(min(length, 1 << 20))
                if not chunk:
                    break
                length -= len(chunk)
            return False
        tmp = os.path.join(self.root, "tmp", f"{sha}.{threading.get_ident()}")
        h, left = hashlib.sha256(), length
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise BadRequest("upload ended early")
                    h.update(chunk)
                    f.write(chunk)
                    left -= len(chunk)
            if h.hexdigest() != sha:
                raise BadRequest(f"content hashes to {h.hexdigest()}, not {sha}")
            os.makedirs(os.path.dirname(self.path(sha)), mode=0o750, exist_ok=True)
            os.chmod(tmp, 0o440)
            os.replace(tmp, self.path(sha))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO objects VALUES (?, ?, ?, ?)", (sha, length, by, iso(now())))
        return True

    def add_build(self, m, cell, owner):
        """A build manifest from a cell. The owner and cell come from the certificate."""
        for k in ("game", "revision", "target", "object", "exe", "gates"):
            if k not in m:
                raise BadRequest(f"manifest needs {k}")
        if not NAME_RE.match(str(m["game"])) or not REV_RE.match(str(m["revision"])):
            raise BadRequest("bad game or revision")
        if m["target"] not in TARGETS or not SHA_RE.match(str(m["object"])):
            raise BadRequest("bad target or object")
        exe = os.path.normpath(str(m["exe"]))
        if exe.startswith(("/", "..")) or "\0" in exe:
            raise BadRequest("exe must be a path inside the build")
        gates = m["gates"]
        if not isinstance(gates, dict) or not gates.get("passed"):
            raise BadRequest("the depot takes builds from passing gates only")
        if not self.has(m["object"]):
            raise BadRequest("upload the object first")
        mr = m.get("mr") or {}
        expires = iso(now() + dt.timedelta(days=self.build_days))
        size = os.path.getsize(self.path(m["object"]))
        with self.db() as db:
            db.execute(
                """INSERT INTO builds (game, revision, target, object, object_bytes, exe, exe_sha256, bytes,
                     owner, cell, job, project_id, mr_iid, mr_title, mr_url, mr_state, gates, at, expires)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (game, revision, target, cell) DO UPDATE SET
                     object=excluded.object, object_bytes=excluded.object_bytes, exe=excluded.exe,
                     exe_sha256=excluded.exe_sha256, bytes=excluded.bytes, job=excluded.job,
                     project_id=excluded.project_id, mr_iid=excluded.mr_iid, mr_title=excluded.mr_title,
                     mr_url=excluded.mr_url, mr_state=excluded.mr_state, gates=excluded.gates,
                     at=excluded.at, expires=excluded.expires""",
                (m["game"], m["revision"], m["target"], m["object"], size, exe, m.get("exe_sha256"),
                 m.get("bytes"), owner, cell, m.get("job"), mr.get("project_id"), mr.get("iid"),
                 mr.get("title"), mr.get("url"), mr.get("state"), json.dumps(gates), iso(now()), expires))
            row = db.execute("SELECT id FROM builds WHERE game=? AND revision=? AND target=? AND cell=?",
                             (m["game"], m["revision"], m["target"], cell)).fetchone()
        return row["id"]

    def mr_state(self, m, cell):
        """A cell reports an MR's state (opened, merged, closed) for the builds it uploaded."""
        if not NAME_RE.match(str(m.get("game", ""))) or not isinstance(m.get("iid"), int):
            raise BadRequest("needs game and iid")
        if m.get("state") not in ("opened", "merged", "closed", "locked"):
            raise BadRequest("bad state")
        with self.db() as db:
            n = db.execute("UPDATE builds SET mr_state=?, mr_title=COALESCE(?, mr_title) "
                           "WHERE game=? AND mr_iid=? AND cell=?",
                           (m["state"], m.get("title"), m["game"], m["iid"], cell)).rowcount
        return n

    def gc(self):
        """Expired builds go; then objects no build refers to (an hour's grace for an upload
        whose manifest is on its way); then stale temporary files."""
        t = now()
        with self.db() as db:
            gone = db.execute("DELETE FROM builds WHERE expires IS NOT NULL AND expires < ?", (iso(t),)).rowcount
            orphans = [r["sha256"] for r in db.execute(
                "SELECT sha256 FROM objects WHERE at < ? AND sha256 NOT IN (SELECT object FROM builds)",
                (iso(t - dt.timedelta(hours=1)),))]
            for sha in orphans:
                try:
                    os.unlink(self.path(sha))
                except FileNotFoundError:
                    pass
                db.execute("DELETE FROM objects WHERE sha256=?", (sha,))
        tmpdir = os.path.join(self.root, "tmp")
        for fn in os.listdir(tmpdir):
            p = os.path.join(tmpdir, fn)
            if time.time() - os.path.getmtime(p) > 86400:
                os.unlink(p)
        if gone or orphans:
            log(f"gc: {gone} expired build(s), {len(orphans)} object(s) removed")


# --- the broker: play sessions on the Forge ------------------------------------------------------

class Broker:
    def __init__(self, cfg, depot):
        self.depot = depot
        self.slots = int(cfg.get("slots", 4))
        self.idle = dt.timedelta(minutes=int(cfg.get("idle_minutes", 10)))
        self.cap = dt.timedelta(hours=float(cfg.get("cap_hours", 2)))
        self.start_limit = dt.timedelta(minutes=int(cfg.get("start_minutes", 5)))
        self.play_min = int(cfg.get("play_min_seconds", 30))
        self.lock = threading.Lock()          # slot choice and state changes, one at a time
        self.last_sync = None

    def session(self, sid):
        with self.depot.db() as db:
            return db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def active(self):
        with self.depot.db() as db:
            return db.execute(f"SELECT * FROM sessions WHERE state IN {ACTIVE} ORDER BY slot").fetchall()

    def request(self, build_id, person):
        """A person presses Play. Their session on this build if they have one; otherwise a
        new one on a free slot, ending their session on any other build. None: all busy."""
        with self.lock, self.depot.db() as db:
            b = db.execute("SELECT * FROM builds WHERE id=?", (build_id,)).fetchone()
            if not b or b["target"] != "linux":
                raise BadRequest("no such Linux build")
            mine = db.execute(f"SELECT * FROM sessions WHERE person=? AND state IN {ACTIVE}", (person,)).fetchall()
            for s in mine:
                if s["build"] == build_id:
                    return s["id"]
            for s in mine:
                self._end(db, s["id"], "replaced by another build")
            used = {r["slot"] for r in db.execute(f"SELECT slot FROM sessions WHERE state IN {ACTIVE}")}
            free = [n for n in range(self.slots) if n not in used]
            if not free:
                return None
            sid = secrets.token_hex(8)
            db.execute("INSERT INTO sessions (id, build, game, revision, object, exe, cell, project_id, mr_iid, "
                       "person, slot, state, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, b["id"], b["game"], b["revision"], b["object"], b["exe"], b["cell"], b["project_id"],
                        b["mr_iid"], person, free[0], "requested", iso(now())))
        log(f"broker: {sid} {b['game']}@{b['revision'][:10]} for {person} on slot {free[0]}")
        return sid

    @staticmethod
    def _end(db, sid, reason, error=None):
        db.execute(f"UPDATE sessions SET state='ended', ended_at=?, end_reason=?, error=? "
                   f"WHERE id=? AND state IN {ACTIVE}", (iso(now()), reason, error, sid))
        log(f"broker: {sid} ended ({reason}{': ' + error if error else ''})")

    def end(self, sid, person, reason="ended by its person"):
        with self.lock, self.depot.db() as db:
            s = db.execute("SELECT person FROM sessions WHERE id=?", (sid,)).fetchone()
            if not s or s["person"] != person:
                raise Forbidden("not your session")
            self._end(db, sid, reason)

    def sweep(self, db=None):
        """End sessions past their limits: never started, idle, or at the cap."""
        t = now()
        own = db is None
        db = db or self.depot.db()
        try:
            for s in db.execute(f"SELECT * FROM sessions WHERE state IN {ACTIVE}").fetchall():
                at = dt.datetime.fromisoformat(s["at"])
                if s["state"] != "live" and t - at > self.start_limit:
                    self._end(db, s["id"], "didn't start")
                elif s["state"] == "live":
                    seen = max(dt.datetime.fromisoformat(s["live_at"]),
                               dt.datetime.fromisoformat(s["last_viewer_at"]) if s["last_viewer_at"] else at)
                    if t - seen > self.idle:
                        self._end(db, s["id"], f"no viewer for {int(self.idle.total_seconds() // 60)} minutes")
                    elif t - at > self.cap:
                        self._end(db, s["id"], f"the {self.cap.total_seconds() / 3600:g}-hour cap")
            if own:
                db.commit()
        finally:
            if own:
                db.close()

    def sync(self, report):
        """The Forge says what runs (ready or not, viewers) and what failed; the hub answers with
        the sessions that should run. Anything not in the answer, the Forge removes."""
        running = report.get("running") or {}
        failed = report.get("failed") or {}
        t = now()
        step = min((t - self.last_sync).total_seconds(), 10) if self.last_sync else 0
        self.last_sync = t
        with self.lock, self.depot.db() as db:
            for sid, msg in failed.items():
                self._end(db, str(sid), "failed on the Forge", str(msg)[:2000])
            for sid, st in running.items():
                s = db.execute("SELECT * FROM sessions WHERE id=?", (str(sid),)).fetchone()
                if not s or s["state"] not in ACTIVE:
                    continue
                if st.get("ready") and s["state"] != "live":
                    db.execute("UPDATE sessions SET state='live', live_at=? WHERE id=?", (iso(t), s["id"]))
                    log(f"broker: {s['id']} live on slot {s['slot']}")
                elif s["state"] == "requested":
                    db.execute("UPDATE sessions SET state='starting' WHERE id=?", (s["id"],))
                if int(st.get("viewers") or 0) > 0:
                    db.execute("UPDATE sessions SET viewer_seconds = viewer_seconds + ?, last_viewer_at=? WHERE id=?",
                               (int(step), iso(t), s["id"]))
            self.sweep(db)
            rows = db.execute(f"SELECT * FROM sessions WHERE state IN {ACTIVE} ORDER BY slot").fetchall()
        return {"sessions": [{"id": s["id"], "slot": s["slot"], "object": s["object"], "exe": s["exe"],
                              "game": s["game"], "revision": s["revision"]} for s in rows]}

    def stream_ok(self, slot, person):
        with self.depot.db() as db:
            s = db.execute("SELECT person FROM sessions WHERE slot=? AND state='live'", (slot,)).fetchone()
        return bool(s) and s["person"] == person

    def plays(self, cell):
        """Ended sessions of this cell's builds that someone watched, not yet reported."""
        with self.depot.db() as db:
            rows = db.execute("SELECT * FROM sessions WHERE cell=? AND state='ended' AND reported=0 "
                              "AND viewer_seconds >= ?", (cell, self.play_min)).fetchall()
        return [{k: s[k] for k in ("id", "game", "revision", "project_id", "mr_iid", "person", "viewer_seconds",
                                   "at", "ended_at", "end_reason")} for s in rows]

    def reported(self, sid, cell):
        with self.depot.db() as db:
            n = db.execute("UPDATE sessions SET reported=1 WHERE id=? AND cell=?", (sid, cell)).rowcount
        if not n:
            raise BadRequest("no such play of yours")


# --- :8443, cells and the Forge ------------------------------------------------------------------

class ApiHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"studio-hubd/{VERSION}"

    def log_message(self, fmt, *args):
        log(f"api {getattr(self, 'who', '?')} {fmt % args}")

    def log_request(self, code="-", size="-"):
        # The Forge syncs every 2 s. The broker logs what a sync changes (live, ended, failed);
        # a sync that went through is not news. Refused or failed syncs are still logged.
        if self.path == "/v1/forge/sync" and code == 200:
            return
        super().log_request(code, size)

    def identity(self):
        cert = self.connection.getpeercert() or {}
        cn = None
        for rdn in cert.get("subject", ()):
            for k, v in rdn:
                if k == "commonName":
                    cn = v
        c = self.server.cfg["clients"].get(cn or "")
        if not c or c["ip"] != self.client_address[0]:
            raise Forbidden(f"{cn} from {self.client_address[0]} isn't a studio client")
        self.who = cn
        return cn, c

    def reply(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def run(self, fn):
        try:
            fn(*self.identity())
        except Forbidden as e:
            self.reply(403, {"error": str(e)})
        except BadRequest as e:
            self.reply(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log("api error:\n" + traceback.format_exc())
            self.reply(500, {"error": str(e)[:300]})

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise BadRequest("request too large")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise BadRequest("bad JSON") from None

    def object_sha(self):
        m = re.fullmatch(r"/v1/objects/([0-9a-f]{64})", self.path)
        if not m:
            raise BadRequest("no such object path")
        return m.group(1)

    def do_GET(self):
        def go(cn, c):
            depot = self.server.depot
            if self.path == "/v1/whoami":
                return self.reply(200, {"name": cn, **{k: v for k, v in c.items() if k != "ip"}})
            if self.path == "/v1/plays":
                if c["kind"] != "cell":
                    raise Forbidden("plays are reported to the cells that uploaded the builds")
                return self.reply(200, self.server.broker.plays(cn))
            sha = self.object_sha()
            if c["kind"] != "forge":
                raise Forbidden("only the Forge downloads objects")
            if not depot.has(sha):
                return self.reply(404, {"error": "no such object"})
            p = depot.path(sha)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(p)))
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
        self.run(go)

    def do_HEAD(self):
        def go(cn, c):
            self.reply(200 if self.server.depot.has(self.object_sha()) else 404)
        self.run(go)

    def do_PUT(self):
        def go(cn, c):
            if c["kind"] != "cell":
                raise Forbidden("only cells upload")
            sha = self.object_sha()
            if "Content-Length" not in self.headers:
                raise BadRequest("Content-Length required")
            new = self.server.depot.receive(sha, self.rfile, int(self.headers["Content-Length"]), cn)
            log(f"depot: {sha[:12]} from {cn}" + ("" if new else " (already held)"))
            self.reply(201 if new else 200, {"object": sha})
        self.run(go)

    def do_POST(self):
        def go(cn, c):
            if self.path == "/v1/forge/sync":
                if c["kind"] != "forge":
                    raise Forbidden("only the Forge syncs sessions")
                return self.reply(200, self.server.broker.sync(self.body_json()))
            if c["kind"] != "cell":
                raise Forbidden("only cells post builds and plays")
            depot = self.server.depot
            m = re.fullmatch(r"/v1/plays/([0-9a-f]{16})/reported", self.path)
            if m:
                self.server.broker.reported(m.group(1), cn)
                return self.reply(200, {"reported": m.group(1)})
            if self.path == "/v1/builds":
                m = self.body_json()
                bid = depot.add_build(m, cn, c["owner"])
                url = f"{self.server.cfg['portal_url']}/g/{m['game']}/r/{m['revision']}"
                log(f"depot: build {bid} {m['game']}@{m['revision'][:10]} {m['target']} from {cn} ({c['owner']})")
                return self.reply(201, {"id": bid, "url": url})
            if self.path == "/v1/mr-state":
                return self.reply(200, {"builds": depot.mr_state(self.body_json(), cn)})
            raise BadRequest("no such path")
        self.run(go)


class TLSServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """The handshake happens in the request's own thread, so a slow client holds up no one."""
    daemon_threads = True

    def finish_request(self, request, client_address):
        request.settimeout(30)
        try:
            request = self.ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError) as e:
            log(f"tls: {client_address[0]}: {e}")
            return
        # The TLS socket took over the connection; the server closes only the plain one it saw.
        try:
            request.settimeout(1800)
            super().finish_request(request, client_address)
        finally:
            request.close()


# --- :8080, the portal -------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#101418;color:#e6e6e6}}
header{{padding:12px 24px;background:#1a2028;border-bottom:1px solid #2a323c}}
header a{{color:#e6e6e6;text-decoration:none;font-weight:600}} header span{{float:right;color:#9aa4ae}}
main{{padding:16px 24px;max-width:1100px}} a{{color:#7cc4ff}} h1{{font-size:22px}} h2{{font-size:18px;margin-top:28px}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #2a323c}}
th{{color:#9aa4ae;font-weight:500}} .ok{{color:#5fd38d}} .bad{{color:#ff7b72}} .dim{{color:#9aa4ae}}
code{{font:13px ui-monospace,monospace}} .tag{{padding:1px 8px;border-radius:9px;background:#2a323c;font-size:13px}}
form{{display:inline;margin:0}} button{{font:inherit;padding:2px 14px;border-radius:6px;border:1px solid #3b82c4;
background:#1f4f7a;color:#fff;cursor:pointer}} button.end{{background:#5a2a2a;border-color:#8a3a3a}}
</style>{head}</head><body><header><a href="/">Studio</a><span>{who}</span></header><main>{body}</main></body></html>"""


def e(s):
    return html.escape(str(s if s is not None else ""))


def size(n):
    return f"{(n or 0) / 1e6:.1f} MB"


def age(at):
    s = (now() - dt.datetime.fromisoformat(at)).total_seconds()
    return f"{int(s // 60)} min ago" if s < 3600 else f"{int(s // 3600)} h ago" if s < 86400 else f"{int(s // 86400)} d ago"


def gates_cell(gates):
    g = json.loads(gates)
    parts = []
    for s in g.get("steps", []):
        t = s.get("tests") or {}
        n = f" {t.get('passed')}/{t.get('total')}" if t else ""
        parts.append(f'<span class="{"ok" if s.get("passed") else "bad"}">{e(s.get("step"))}{e(n)}</span>')
    return " · ".join(parts)


class PortalHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"studio-hubd/{VERSION}"

    def log_message(self, fmt, *args):
        log(f"portal {getattr(self, 'person', '-')} {fmt % args}")

    def log_request(self, code="-", size="-"):
        # Traefik asks before every request to a slot. Who watched which session is in the
        # sessions table; the journal keeps only the refusals.
        if self.path == "/auth/stream" and code == 200:
            return
        super().log_request(code, size)

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def page(self, title, body, code=200, head=""):
        self.send(code, PAGE.format(title=e(title), who=e(self.person), body=body, head=head))

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def who(self):
        """The person, or None after answering: only Traefik, after Authentik, says who."""
        self.person = None
        if self.client_address[0] not in self.server.cfg["portal_proxies"]:
            self.send(403, "forbidden\n", "text/plain")
            return None
        self.person = self.headers.get("X-authentik-username")
        if not self.person:
            self.send(401, "no person\n", "text/plain")
        return self.person

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            return self.send(200, "ok\n", "text/plain")
        if not self.who():
            return None
        try:
            if path == "/auth/stream":
                return self.stream_auth()
            if path == "/auth/term":
                return self.term_auth()
            if path == "/":
                return self.home()
            m = re.fullmatch(r"/g/([a-z0-9-]+)/(mr|r)/([0-9a-f]{40}|\d+)", path)
            if m and m.group(2) == "mr" and m.group(3).isdigit():
                return self.mr(m.group(1), int(m.group(3)))
            if m and m.group(2) == "r" and REV_RE.match(m.group(3)):
                return self.revision(m.group(1), m.group(3))
            m = re.fullmatch(r"/s/([0-9a-f]{16})", path)
            if m:
                return self.session_page(m.group(1))
            self.page("Not found", "<h1>Not found</h1>", 404)
        except Exception:  # noqa: BLE001
            log("portal error:\n" + traceback.format_exc())
            self.page("Error", "<h1>Something broke</h1><p class=dim>It's in the hub's journal.</p>", 500)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.who():
            return None
        # A form posted from another site carries its own Origin; the portal's forms carry ours.
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") != self.server.cfg["portal_url"].rstrip("/"):
            return self.send(403, "cross-site request refused\n", "text/plain")
        try:
            m = re.fullmatch(r"/play/(\d+)", path)
            if m:
                sid = self.server.broker.request(int(m.group(1)), self.person)
                if not sid:
                    return self.page("All slots busy", f"<h1>All {self.server.broker.slots} play slots are busy</h1>"
                                     "<p>Try again when a session ends.</p>" + self.sessions_table(), 503)
                return self.redirect(f"/s/{sid}")
            m = re.fullmatch(r"/s/([0-9a-f]{16})/end", path)
            if m:
                self.server.broker.end(m.group(1), self.person)
                return self.redirect(f"/s/{m.group(1)}")
            self.page("Not found", "<h1>Not found</h1>", 404)
        except Forbidden as x:
            self.page("Not yours", f"<h1>{e(x)}</h1>", 403)
        except BadRequest as x:
            self.page("Bad request", f"<h1>{e(x)}</h1>", 400)
        except Exception:  # noqa: BLE001
            log("portal error:\n" + traceback.format_exc())
            self.page("Error", "<h1>Something broke</h1><p class=dim>It's in the hub's journal.</p>", 500)

    def forwarded_path(self):
        """The path Traefik asks about, or "" if it could mean another path once cleaned or
        decoded (a dot segment, a backslash, any %-escape): Traefik may route on the cleaned
        path while this check reads the raw one. Streams and terminals use none of those."""
        path = self.headers.get("X-Forwarded-Uri", "").split("?", 1)[0]
        if "%" in path or "\\" in path or any(seg in (".", "..") for seg in path.split("/")):
            return ""
        return path

    def stream_auth(self):
        """Traefik's forwardAuth for /play/s<n>/: only the person whose live session is on slot n."""
        m = re.match(r"/play/s(\d+)/", self.forwarded_path())
        ok = bool(m) and self.server.broker.stream_ok(int(m.group(1)), self.person)
        self.send(200 if ok else 403, "ok\n" if ok else "not your session\n", "text/plain")

    def cell_owners(self):
        return {c.get("owner") for c in self.server.cfg["clients"].values() if c.get("kind") == "cell"} - {None}

    def term_auth(self):
        """Traefik's forwardAuth for /term/<owner>/, a cell's browser terminal: only that owner."""
        uri = self.forwarded_path()
        m = re.match(r"/term/([a-z][a-z0-9-]*)(/|$)", uri)
        ok = bool(m) and m.group(1) == self.person and self.person in self.cell_owners()
        if not ok:
            log(f"terminal refused: {self.person} for {self.headers.get('X-Forwarded-Uri', '')[:80]!r}")
        self.send(200 if ok else 403, "ok\n" if ok else "not your cell\n", "text/plain")

    def rows(self, sql, args=()):
        with self.server.depot.db() as db:
            return db.execute(sql, args).fetchall()

    def sessions_table(self):
        ss = self.server.broker.active()
        if not ss:
            return "<p class=dim>No live sessions.</p>"
        out = ["<table><tr><th>Slot</th><th>Game</th><th>Revision</th><th>Person</th><th>State</th>"
               "<th>Started</th><th>Watched</th><th></th></tr>"]
        for s in ss:
            mine = s["person"] == self.person
            link = f'<a href="/s/{s["id"]}">open</a> ' if mine else ""
            end = (f'<form method="post" action="/s/{s["id"]}/end"><button class="end">End</button></form>'
                   if mine else "")
            out.append(f'<tr><td>s{s["slot"]}</td><td>{e(s["game"])}</td><td><code>{e(s["revision"][:10])}</code></td>'
                       f'<td>{e(s["person"])}</td><td><span class="tag">{e(s["state"])}</span></td>'
                       f'<td>{age(s["at"])}</td><td>{s["viewer_seconds"] // 60} min</td><td>{link}{end}</td></tr>')
        out.append("</table>")
        return "".join(out)

    def session_page(self, sid):
        s = self.server.broker.session(sid)
        if not s or s["person"] != self.person:
            return self.page("Not found", "<h1>No such session of yours</h1>", 404)
        what = f'{e(s["game"])} <code>{e(s["revision"][:10])}</code>'
        end = f'<form method="post" action="/s/{sid}/end"><button class="end">End the session</button></form>'
        if s["state"] == "live":
            stream = f'/play/s{s["slot"]}/'
            return self.page(f"Playing {s['game']}", f'<h1>{what} is live on slot s{s["slot"]}</h1>'
                             f'<p><a href="{stream}">Open the stream</a> (opening now) · {end}</p>'
                             f'<p class=dim>It ends after {int(self.server.broker.idle.total_seconds() // 60)} '
                             'minutes with nobody watching. Come back here to end it sooner.</p>',
                             head=f'<meta http-equiv="refresh" content="1;url={stream}">')
        if s["state"] in ACTIVE:
            return self.page(f"Starting {s['game']}", f"<h1>Starting {what} on the Forge…</h1>"
                             f'<p class=dim>Slot s{s["slot"]}, {e(s["state"])}. This page refreshes itself.</p>'
                             f"<p>{end}</p>", head='<meta http-equiv="refresh" content="2">')
        err = f'<pre class=dim>{e(s["error"])}</pre>' if s["error"] else ""
        return self.page(f"{s['game']} ended", f"<h1>{what}: session ended</h1>"
                         f'<p>{e(s["end_reason"])} · watched {s["viewer_seconds"] // 60} min</p>{err}'
                         f'<p><a href="/g/{e(s["game"])}/r/{e(s["revision"])}">Back to the build</a></p>')

    def home(self):
        builds = self.rows("SELECT * FROM builds ORDER BY at DESC")
        games = {}
        for b in builds:
            games.setdefault(b["game"], []).append(b)
        out = []
        if self.person in self.cell_owners():
            out.append(f'<p><a href="/term/{e(self.person)}/" target="_blank">Your cell&#39;s terminal</a>'
                       f' <span class="dim">(cell-{e(self.person)}: sign-ins, pairing with your agents)</span></p>')
        out += ["<h1>Live sessions</h1>", self.sessions_table(), "<h1>Games</h1>"]
        if not games:
            out.append("<p class=dim>No builds yet. They arrive when an agent's gates pass.</p>")
        for game, bs in sorted(games.items()):
            out.append(f"<h2>{e(game)}</h2><table><tr><th>MR</th><th>State</th><th>Latest revision</th>"
                       "<th>Gates</th><th>Size</th><th>Built</th></tr>")
            seen = set()
            # Open MRs first; newest first within each (the sort is stable).
            for b in sorted(bs, key=lambda b: b["mr_state"] != "opened"):
                key = b["mr_iid"] or b["revision"]
                if key in seen:
                    continue
                seen.add(key)
                mr = (f'<a href="/g/{e(game)}/mr/{b["mr_iid"]}">!{b["mr_iid"]} {e(b["mr_title"])}</a>'
                      if b["mr_iid"] else '<span class="dim">no MR</span>')
                out.append(f'<tr><td>{mr}</td><td><span class="tag">{e(b["mr_state"] or "-")}</span></td>'
                           f'<td><a href="/g/{e(game)}/r/{e(b["revision"])}"><code>{e(b["revision"][:10])}</code></a></td>'
                           f'<td>{gates_cell(b["gates"])}</td><td>{size(b["bytes"])}</td><td>{age(b["at"])}</td></tr>')
            out.append("</table>")
        self.page("Studio", "".join(out))

    def build_table(self, bs):
        out = ["<table><tr><th>Revision</th><th>Target</th><th>Gates</th><th>Size</th><th>Owner</th>"
               "<th>Object</th><th>Built</th><th>Play</th></tr>"]
        for b in bs:
            out.append(f'<tr><td><code>{e(b["revision"][:10])}</code></td><td>{e(b["target"])}</td>'
                       f'<td>{gates_cell(b["gates"])}</td><td>{size(b["bytes"])}</td><td>{e(b["owner"])}</td>'
                       f'<td><code>{e(b["object"][:12])}</code></td><td>{age(b["at"])}</td>'
                       + (f'<td><form method="post" action="/play/{b["id"]}"><button>Play</button></form></td></tr>'
                          if b["target"] == "linux" else '<td class=dim>download (2b-2)</td></tr>'))
        out.append("</table>")
        return "".join(out)

    def mr(self, game, iid):
        bs = self.rows("SELECT * FROM builds WHERE game=? AND mr_iid=? ORDER BY at DESC", (game, iid))
        if not bs:
            return self.page("Not found", f"<h1>{e(game)} !{iid}</h1><p class=dim>No builds for this MR.</p>", 404)
        b = bs[0]
        head = (f'<h1>{e(game)} <a href="{e(b["mr_url"])}">!{iid}</a> {e(b["mr_title"])}</h1>'
                f'<p><span class="tag">{e(b["mr_state"] or "-")}</span></p>')
        self.page(f"{game} !{iid}", head + self.build_table(bs))

    def revision(self, game, rev):
        bs = self.rows("SELECT * FROM builds WHERE game=? AND revision=? ORDER BY at DESC", (game, rev))
        if not bs:
            return self.page("Not found", f"<h1>{e(game)} <code>{e(rev[:10])}</code></h1>"
                             "<p class=dim>No build of this revision in the depot (yet).</p>", 404)
        b = bs[0]
        mr = (f' · <a href="/g/{e(game)}/mr/{b["mr_iid"]}">!{b["mr_iid"]} {e(b["mr_title"])}</a>'
              if b["mr_iid"] else "")
        self.page(f"{game} {rev[:10]}", f"<h1>{e(game)} <code>{e(rev[:10])}</code>{mr}</h1>" + self.build_table(bs))


class PortalServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# --- main --------------------------------------------------------------------------------------

def listen(addr):
    host, _, port = addr.rpartition(":")
    return host, int(port)


def gc_loop(depot, hours):
    while True:
        try:
            depot.gc()
        except Exception:  # noqa: BLE001
            log("gc failed:\n" + traceback.format_exc())
        time.sleep(hours * 3600)


def sweep_loop(broker, seconds):
    """Session limits hold even when the Forge has stopped syncing."""
    while True:
        time.sleep(seconds)
        try:
            with broker.lock:
                broker.sweep()
        except Exception:  # noqa: BLE001
            log("sweep failed:\n" + traceback.format_exc())


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    depot = Depot(cfg)
    broker = Broker(cfg, depot)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(cfg["tls"]["cert"], cfg["tls"]["key"])
    ctx.load_verify_locations(cfg["tls"]["ca"])
    ctx.verify_mode = ssl.CERT_REQUIRED

    api = TLSServer(listen(cfg["listen_api"]), ApiHandler)
    api.ctx, api.cfg, api.depot, api.broker = ctx, cfg, depot, broker
    portal = PortalServer(listen(cfg["listen_portal"]), PortalHandler)
    portal.cfg, portal.depot, portal.broker = cfg, depot, broker

    threading.Thread(target=api.serve_forever, daemon=True).start()
    threading.Thread(target=gc_loop, args=(depot, 6), daemon=True).start()
    threading.Thread(target=sweep_loop, args=(broker, 30), daemon=True).start()
    log(f"studio-hubd {VERSION}: api {cfg['listen_api']} (mTLS, {len(cfg['clients'])} clients), "
        f"portal {cfg['listen_portal']}, depot {cfg['depot']}, {broker.slots} play slots")
    portal.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
