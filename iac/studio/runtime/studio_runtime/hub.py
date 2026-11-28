"""The hub, from a cell or the Forge: studio-hubd's :8443, over mutual TLS (role studio_hub_client).

Each host proves who it is with its own key, which never leaves it; the hub's CA signed its
certificate. The hub decides what a caller may do from the certificate's name and the address
it connects from, never from what a request says. Only root reads the key: on a cell that's
studio-agentd and studio-admin, never a job.

Builds travel as a gzipped tar of the build directory, packed deterministically (sorted, no
owners, plain modes, gzip without a timestamp or file name), so the same build always has the
same sha256 and the depot keeps it once.
"""

import datetime as dt
import gzip
import hashlib
import http.client
import json
import os
import re
import ssl
import tarfile
import urllib.parse

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
OUTBOX = os.path.join(os.environ.get("STUDIO_STATE_DIR", "/var/lib/studio"), "outbox")

# --- the Stage: the Studio Kit's contract with the platform ------------------------------------
# A game's build carries its catalogue of Stage assets (the kit writes the committed
# Assets/StreamingAssets/studio/catalog.json), the player takes -studio-open <target>, and with
# STUDIO_OUT set the kit writes the session's tweaks to $STUDIO_OUT/tweaks.json. Everything that
# crosses a trust boundary (cell -> hub, Forge -> hub, hub -> GitLab) goes through these.
CATALOG_PATH = "Assets/StreamingAssets/studio/catalog.json"
ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TWEAK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
OPEN_RE = re.compile(r"^(game|stage|stage:[a-z0-9][a-z0-9-]{0,47})$")
MAX_ASSETS, MAX_TWEAKS, MAX_NAME = 200, 64, 64
MAX_TWEAK_ASSETS, MAX_CHANGES, MAX_TWEAKS_BYTES = 50, 100, 64 << 10


def check_catalog(c):
    """The catalogue, normalised, or None if it isn't one: version 1, ids and tweak names
    as the contract says, names at most 64 characters, at most 200 assets. Keeps "new"."""
    if not isinstance(c, dict) or c.get("version") != 1 or not isinstance(c.get("assets"), list):
        return None
    if len(c["assets"]) > MAX_ASSETS:
        return None
    out, seen = [], set()
    for a in c["assets"]:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not ASSET_ID_RE.match(a["id"]):
            return None
        name = a.get("name", a["id"])
        tweaks = a.get("tweaks", [])
        if (not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME or a["id"] in seen
                or not isinstance(tweaks, list) or len(tweaks) > MAX_TWEAKS
                or not all(isinstance(t, str) and TWEAK_RE.match(t) for t in tweaks)):
            return None
        seen.add(a["id"])
        e = {"id": a["id"], "name": name.strip(), "tweaks": list(tweaks)}
        if a.get("new") is True:
            e["new"] = True
        out.append(e)
    kit = c.get("kit")
    return {"version": 1, "kit": kit if isinstance(kit, str) and len(kit) <= 32 else None, "assets": out}


def catalog_ids(c):
    return {a["id"] for a in (c or {}).get("assets", [])}


def _scalar(v):
    return isinstance(v, bool) or (isinstance(v, (int, float)) and v == v and abs(v) < 1e12)


def check_tweaks(t):
    """A session's tweaks.json, normalised, or None if it isn't one (version 1, at most 50
    assets with at most 100 changes each, names as the contract says, numbers or booleans)."""
    if not isinstance(t, dict) or t.get("version") != 1 or not isinstance(t.get("assets"), list):
        return None
    if len(t["assets"]) > MAX_TWEAK_ASSETS:
        return None
    out = []
    for a in t["assets"]:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not ASSET_ID_RE.match(a["id"]):
            return None
        ch = a.get("changes")
        if not isinstance(ch, list) or len(ch) > MAX_CHANGES:
            return None
        rows = []
        for c in ch:
            if (not isinstance(c, dict) or not isinstance(c.get("name"), str) or not TWEAK_RE.match(c["name"])
                    or not _scalar(c.get("from")) or not _scalar(c.get("to"))):
                return None
            rows.append({"name": c["name"], "from": c["from"], "to": c["to"]})
        if rows:
            out.append({"id": a["id"], "changes": rows})
    if len(json.dumps(out)) > MAX_TWEAKS_BYTES:
        return None
    return {"version": 1, "assets": out}


def check_open(value, catalog=None):
    """What a session opens: game, stage or stage:<id>; with a catalogue, the id must be in it."""
    if not isinstance(value, str) or not OPEN_RE.match(value):
        return None
    if value.startswith("stage:") and catalog is not None and value[6:] not in catalog_ids(catalog):
        return None
    return value


# --- a session's files: its recording, its player log, the kit's timeline ---------------------
# forge-playd records every session and, when it ends, puts the recording (MP4), the player log
# and $STUDIO_OUT/timeline.jsonl in the depot, then names them in its sync. The timeline is the
# game's own account of the session, one JSON object per line: "t" (seconds since the player
# started) and "kind", plus a few short fields; the first line is kind "start" with "wall", the
# wall-clock time at t = 0, which places the events on the recording's clock.
MAX_LOG_BYTES, MAX_TIMELINE_BYTES, MAX_EVENTS = 5 << 20, 1 << 20, 5000
KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_FIELDS, MAX_FIELD_TEXT = 8, 300


def _number(v, low, high):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and v == v and low <= v < high


def check_files(f):
    """The Forge's report of a session's files, normalised, or None if it isn't one."""
    if not isinstance(f, dict):
        return None
    out = {}
    for k in ("recording", "log", "timeline", "voice", "transcript"):
        v = f.get(k)
        if v is not None and (not isinstance(v, str) or not SHA_RE.match(v)):
            return None
        out[k] = v
    secs, start, note = f.get("recording_seconds"), f.get("recording_start"), f.get("note")
    out["voice_start"] = float(f["voice_start"]) if _number(f.get("voice_start"), 1, 1e11) else None
    out["recording_seconds"] = float(secs) if _number(secs, 0, 1e6) else None
    out["recording_start"] = float(start) if _number(start, 1, 1e11) else None
    out["note"] = note[:200] if isinstance(note, str) and note else None
    return out


MAX_TRANSCRIPT_BYTES, MAX_LINES, MAX_LINE_TEXT = 1 << 20, 5000, 500


def read_transcript(raw):
    """A voice's transcript from its file's bytes: {"model": name or None, "lines": [{"from",
    "to", "text"}]}, times in seconds on the voice file's clock. The words are whatever a
    speech model heard a player say, so they're data: short lines of printable text, at most
    5000 of them, anything else dropped. A file that isn't a transcript gives no lines."""
    try:
        doc = json.loads(raw[:MAX_TRANSCRIPT_BYTES].decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return {"model": None, "lines": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list):
        return {"model": None, "lines": []}
    model, lines = doc.get("model"), []
    for ln in doc["lines"]:
        if len(lines) >= MAX_LINES:
            break
        if not isinstance(ln, dict) or not _number(ln.get("from"), 0, 1e6) or not _number(ln.get("to"), 0, 1e6):
            continue
        text = ln.get("text")
        if not isinstance(text, str):
            continue
        text = " ".join("".join(c if c.isprintable() else " " for c in text).split())[:MAX_LINE_TEXT]
        if text:
            lines.append({"from": float(ln["from"]), "to": float(ln["to"]), "text": text})
    return {"model": model[:80] if isinstance(model, str) and model.isprintable() else None, "lines": lines}


def _wall(v):
    """An ISO time as epoch seconds, or None."""
    if not isinstance(v, str) or len(v) > 40:
        return None
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


def read_timeline(raw):
    """A timeline from its file's bytes: {"wall": epoch seconds at t = 0 or None, "events":
    [{"t", "kind", "fields"}]}. Lines that aren't events are skipped (a session that was cut
    leaves a half-written last line); at most 5000 events, and short text only."""
    wall, events = None, []
    for line in raw[:MAX_TIMELINE_BYTES].decode("utf-8", errors="replace").splitlines():
        if len(events) >= MAX_EVENTS:
            break
        try:
            ev = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(ev, dict) or not _number(ev.get("t"), 0, 1e6):
            continue
        kind = ev.get("kind")
        if not isinstance(kind, str) or not KIND_RE.match(kind):
            continue
        if kind == "start" and wall is None:
            wall = _wall(ev.get("wall"))
        fields = {}
        for k, v in ev.items():
            if k in ("t", "kind", "wall") or not isinstance(k, str) or not FIELD_RE.match(k) or len(fields) >= MAX_FIELDS:
                continue
            if isinstance(v, str):
                fields[k] = v[:MAX_FIELD_TEXT]
            elif _scalar(v):
                fields[k] = v
        events.append({"t": float(ev["t"]), "kind": kind, "fields": fields})
    return {"wall": wall, "events": events}


class HubError(Exception):
    def __init__(self, status, body):
        super().__init__(f"hub HTTP {status}: {body!s:.300}")
        self.status = status


class Hub:
    def __init__(self, cfg):
        h = cfg["hub"]
        u = urllib.parse.urlsplit(h["url"])
        self.host, self.port = u.hostname, u.port or 443
        self.portal = h["portal"].rstrip("/")
        self.files = (h["ca"], h["cert"], h["key"])
        self._context()                  # fails now, not at first use, if the files are missing

    def _context(self):
        """Read fresh for each connection, so a renewed certificate needs no restart."""
        ca, cert, key = self.files
        ctx = ssl.create_default_context(cafile=ca)
        ctx.load_cert_chain(cert, key)
        return ctx

    def _conn(self, timeout):
        return http.client.HTTPSConnection(self.host, self.port, context=self._context(), timeout=timeout,
                                           blocksize=1 << 20)

    def call(self, method, path, data=None, timeout=60):
        body = json.dumps(data).encode() if data is not None else None
        c = self._conn(timeout)
        try:
            c.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
            r = c.getresponse()
            raw = r.read()
            if r.status >= 400:
                raise HubError(r.status, raw[:500].decode(errors="replace"))
            return json.loads(raw) if raw else None
        finally:
            c.close()

    def whoami(self):
        return self.call("GET", "/v1/whoami")

    def has(self, sha):
        c = self._conn(60)
        try:
            c.request("HEAD", f"/v1/objects/{sha}")
            r = c.getresponse()
            r.read()
            if r.status not in (200, 404):
                raise HubError(r.status, "HEAD")
            return r.status == 200
        finally:
            c.close()

    def put(self, path, sha):
        """Upload a file as object sha (skipped if the depot has it). The hub checks the hash."""
        if self.has(sha):
            return False
        size = os.path.getsize(path)
        c = self._conn(1800)
        try:
            with open(path, "rb") as f:
                c.request("PUT", f"/v1/objects/{sha}", body=f,
                          headers={"Content-Length": str(size), "Content-Type": "application/octet-stream"})
            r = c.getresponse()
            raw = r.read()
            if r.status >= 400:
                raise HubError(r.status, raw[:500].decode(errors="replace"))
            return True
        finally:
            c.close()

    def get(self, sha, dest):
        """Download object sha to dest, checking the hash before it lands."""
        if not SHA_RE.match(sha):
            raise ValueError("bad object id")
        tmp = dest + ".part"
        h = hashlib.sha256()
        c = self._conn(1800)
        try:
            c.request("GET", f"/v1/objects/{sha}")
            r = c.getresponse()
            if r.status != 200:
                raise HubError(r.status, r.read()[:300].decode(errors="replace"))
            with open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    h.update(chunk)
                    f.write(chunk)
        finally:
            c.close()
        if h.hexdigest() != sha:
            os.unlink(tmp)
            raise HubError(502, f"object {sha[:12]} arrived with hash {h.hexdigest()[:12]}")
        os.replace(tmp, dest)
        return dest


# --- builds ------------------------------------------------------------------------------------

def _norm(ti):
    """No owners, and plain modes: the cell's setgid, group-writable work-group files become
    0755 (directories and executables) or 0644, which a play container's user can read."""
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() or ti.mode & 0o111 else 0o644
    return ti


def pack(build_dir, dest):
    """build_dir's contents as a deterministic .tar.gz at dest; returns (sha256, bytes)."""
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for dp, dns, fns in os.walk(build_dir):
            dns.sort()
            rel = os.path.relpath(dp, build_dir)
            if rel != ".":
                tar.add(dp, arcname=rel, recursive=False, filter=_norm)
            for fn in sorted(fns):
                p = os.path.join(dp, fn)
                tar.add(p, arcname=os.path.normpath(os.path.join(rel, fn)), recursive=False, filter=_norm)
    h = hashlib.sha256()
    with open(tmp, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    os.replace(tmp, dest)
    return h.hexdigest(), os.path.getsize(dest)


def build_step(gate):
    """The Linux build step of a passing `gate full` result, or None."""
    if not gate.get("passed"):
        return None
    for s in gate.get("steps", []):
        if s.get("step") == "build-linux" and s.get("passed") and os.path.isdir(s.get("path") or ""):
            return s
    return None


def read_catalog(raw):
    """A catalogue from its file's bytes, or None (unreadable, not JSON, not a catalogue)."""
    try:
        return check_catalog(json.loads(raw))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


def catalog_of(step):
    """The Stage catalogue a Linux build carries (<stem>_Data/StreamingAssets/studio/catalog.json),
    or None. A build without one, or with a bad one, is still a build: it just has no Stage."""
    exe = os.path.basename(step.get("binary") or "")
    stem = os.path.splitext(exe)[0]
    if not stem:
        return None
    p = os.path.join(step["path"], f"{stem}_Data", "StreamingAssets", "studio", "catalog.json")
    try:
        if os.path.getsize(p) > 1 << 20:
            return None
        with open(p, "rb") as f:
            return read_catalog(f.read())
    except OSError:
        return None


def deposit(hub, game, gate):
    """Pack and upload the gate's Linux build. Returns what the manifest needs about it."""
    step = build_step(gate)
    if not step:
        raise ValueError("no passing Linux build in this gate result")
    rev = gate["revision"]
    dest = os.path.join(OUTBOX, f"{game}-{rev}-linux.tar.gz")
    sha, size = pack(step["path"], dest)
    try:
        hub.put(dest, sha)
    finally:
        os.unlink(dest)
    return {"object": sha, "object_bytes": size, "bytes": step.get("bytes"), "target": "linux",
            "exe": os.path.relpath(step["binary"], step["path"]), "exe_sha256": step.get("sha256"),
            "catalog": catalog_of(step)}


def mark_new(catalog, base_ids):
    """The catalogue with "new": true on the assets the base revision didn't have."""
    if not catalog:
        return catalog
    return {**catalog, "assets": [{**a, "new": True} if a["id"] not in base_ids else
                                  {k: v for k, v in a.items() if k != "new"} for a in catalog["assets"]]}


def gate_summary(gate):
    """What the portal shows of a gate: each step's result and test counts, no paths."""
    rows = []
    for s in gate.get("steps", []):
        t = s.get("tests") or {}
        rows.append({"step": s.get("step"), "passed": bool(s.get("passed")),
                     "tests": {k: t.get(k) for k in ("passed", "failed", "total") if k in t} or None})
    return {"kind": gate.get("kind"), "passed": bool(gate.get("passed")), "steps": rows}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def manifest(game, gate, dep, job=None, mr=None):
    """The build manifest the hub takes (POST /v1/builds). mr is GitLab's MR object."""
    m = {"game": game, "revision": gate["revision"], "target": dep["target"], "object": dep["object"],
         "exe": dep["exe"], "exe_sha256": dep.get("exe_sha256"), "bytes": dep.get("bytes"), "job": job,
         "gates": gate_summary(gate)}
    if dep.get("catalog"):
        m["catalog"] = dep["catalog"]
    if mr:
        m["mr"] = {"project_id": mr["project_id"], "iid": mr["iid"], "title": mr["title"],
                   "url": mr["web_url"], "state": mr["state"]}
    return m
