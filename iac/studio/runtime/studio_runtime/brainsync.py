"""studio-brain-sync: the studio brain, read-only, as plain files on a cell (vault decision
2026-11-15, "the cells read the studio brain").

Root, a service. It waits on the brain's CouchDB for changes (as this cell's read-only
account, studio-cell-<owner>, whose writes the database refuses; its password in
/etc/studio/brain-password, root's) and, a few seconds after something changes, rebuilds the
copy at /srv/studio/_brain: every note LiveSync keeps, except the desk agents' memory and the
archive. A new copy is built beside the old one and swapped in with one rename, so a reader
never sees half of one. Files are root:agent 0640: jobs and the person's sessions as agent
read them; nothing on a cell writes the brain.

LiveSync's documents (no encryption, no path obfuscation): a note is a doc of type `plain`
(text) or `newnote` (base64 of the bytes) with its `path`, its chunk ids in `children` and
fresh chunks inline in `eden`; each chunk is a `leaf` doc whose `data` is its piece. The
changes feed is filtered to notes on the server; chunks are fetched only for the notes the
copy keeps, and kept (content-addressed) in /var/lib/studio/brain. The rest of the brain is
readable by the account but never stored here.

    studio-brain-sync            the service
    studio-brain-sync --once     one sync, then exit
    studio-brain-sync --check    the account can read and not write, other brains refuse it,
                                 and the copy is there (JSON; exit 1 on a failure)
"""

import base64
import hashlib
import json
import os
import pwd
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import VERSION, common

PASSWORD_FILE = "/etc/studio/brain-password"
STATE_DIR = os.path.join(common.STATE_DIR, "brain")
NOTE_TYPES = ("plain", "newnote")
MAX_FILE = 20 << 20                  # a note bigger than this isn't copied
SETTLE = 5                           # seconds after a change before the copy is rebuilt
KEEP_GENERATIONS = 2                 # the copy in use and the one before it


def log(msg):
    print(msg, flush=True)


class BrainError(Exception):
    pass


class Brain:
    """The brain's database, as this cell's read-only account."""

    def __init__(self, cfg, password):
        b = cfg["brain"]
        self.base = b["url"].rstrip("/")
        self.db = b["db"]
        self.user = b["user"]
        self.auth = "Basic " + base64.b64encode(f"{self.user}:{password}".encode()).decode()
        self.ua = f"studio-brain-sync/{VERSION} (cell-{cfg['owner']})"
        # Root on a cell has the internet directly; no proxy, whatever the environment says.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("User-Agent", self.ua)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except ValueError:   # not CouchDB answering (an edge page)
                return e.code, {"error": "non-json", "reason": raw[:120].decode(errors="replace")}

    def get(self, path, timeout=60):
        code, body = self.call("GET", path, timeout=timeout)
        if code != 200:
            raise BrainError(f"GET {path.split('?')[0]}: {code} {body.get('error')} {body.get('reason', '')}"[:300])
        return body

    # Notes and their deletions only: the chunks a note needs are fetched by id.
    SELECTOR = {"selector": {"$or": [{"type": {"$in": list(NOTE_TYPES)}}, {"_deleted": True}]}}

    def changes(self, since, longpoll=False):
        q = {"since": since, "style": "main_only", "filter": "_selector"}
        if longpoll:
            q.update(feed="longpoll", timeout=50000, limit=1)
        else:
            q.update(include_docs="true", limit=200)
        path = f"/{self.db}/_changes?" + urllib.parse.urlencode(q)
        code, body = self.call("POST", path, self.SELECTOR, timeout=80 if longpoll else 120)
        if code != 200:
            raise BrainError(f"_changes: {code} {body.get('error')} {body.get('reason', '')}"[:300])
        return body

    def ids(self):
        """Every doc id in the brain (no contents): to notice deletions the feed didn't show."""
        return {r["id"] for r in self.get(f"/{self.db}/_all_docs", timeout=120).get("rows", [])}

    def docs(self, ids):
        code, body = self.call("POST", f"/{self.db}/_all_docs?include_docs=true", {"keys": ids}, timeout=120)
        if code != 200:
            raise BrainError(f"_all_docs: {code} {body.get('error')}")
        return [r["doc"] for r in body.get("rows", []) if r.get("doc")]


class Copy:
    """The cell's copy: the notes' index and chunk cache in STATE_DIR, the files under dir."""

    def __init__(self, cfg, brain):
        self.brain = brain
        self.dir = cfg["brain"]["dir"]
        self.gens = self.dir + ".d"
        self.exclude = [e.lower() for e in cfg["brain"].get("exclude", [])]
        self.gid = pwd.getpwnam(cfg["agent_user"]).pw_gid
        self.chunks = os.path.join(STATE_DIR, "chunks")
        os.makedirs(self.chunks, mode=0o700, exist_ok=True)
        self.state_file = os.path.join(STATE_DIR, "state.json")
        try:
            with open(self.state_file, encoding="utf-8") as f:
                self.state = json.load(f)
        except (FileNotFoundError, ValueError):
            self.state = {}
        self.state.setdefault("seq", "0")
        self.state.setdefault("notes", {})
        self.incomplete = not os.path.isdir(self.dir)

    def save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_file)

    # --- the index and the chunks ----------------------------------------------------------

    def _chunk_path(self, cid):
        return os.path.join(self.chunks, hashlib.sha256(cid.encode()).hexdigest())

    def _put_chunk(self, cid, data):
        p = self._chunk_path(cid)
        if not os.path.exists(p):
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(p + ".tmp", p)

    def take(self, doc):
        """One note from the changes feed into the index. True if the copy changes. A note the
        copy leaves out (or a deleted one) isn't kept at all."""
        did = doc.get("_id", "")
        gone = (doc.get("_deleted") or doc.get("deleted") or doc.get("type") not in NOTE_TYPES
                or not isinstance(doc.get("path"), str) or not self.wanted(doc["path"]))
        if gone:
            return self.state["notes"].pop(did, None) is not None
        eden = {k: v["data"] for k, v in (doc.get("eden") or {}).items()
                if isinstance(v, dict) and isinstance(v.get("data"), str)}
        self.state["notes"][did] = {"path": doc["path"], "type": doc["type"],
                                    "children": list(doc.get("children") or []), "eden": eden,
                                    "mtime": doc.get("mtime"), "size": doc.get("size")}
        return True

    def reconcile(self):
        """Notes whose docs are gone from the brain (a purge the feed can't show). True if any."""
        ids = self.brain.ids()
        gone = [d for d in self.state["notes"] if d not in ids]
        for d in gone:
            del self.state["notes"][d]
        if gone:
            self.save()
        return bool(gone)

    def pull(self):
        """Everything since the last sync. True if a note changed."""
        changed = False
        while True:
            res = self.brain.changes(self.state["seq"])
            for row in res.get("results", []):
                doc = row.get("doc") or {"_id": row.get("id"), "_deleted": row.get("deleted", False)}
                if row.get("deleted"):
                    doc["_deleted"] = True
                changed |= self.take(doc)
            self.state["seq"] = res.get("last_seq", self.state["seq"])
            if not res.get("results") or not res.get("pending"):
                break
        self.save()
        return changed

    def wanted(self, path):
        if not path or path.startswith("/") or "\\" in path or "\x00" in path:
            return False
        parts = path.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            return False
        low = path.lower()
        return not any(low == e.rstrip("/") or low.startswith(e if e.endswith("/") else e + "/") for e in self.exclude)

    def notes(self):
        return [n for n in self.state["notes"].values() if self.wanted(n["path"])]

    def fetch_missing(self, notes):
        need = sorted({c for n in notes for c in n["children"]
                       if c not in n["eden"] and not os.path.exists(self._chunk_path(c))})
        for i in range(0, len(need), 200):
            for doc in self.brain.docs(need[i:i + 200]):
                if doc.get("type") == "leaf" and isinstance(doc.get("data"), str):
                    self._put_chunk(doc["_id"], doc["data"])

    def content(self, note):
        parts = []
        for c in note["children"]:
            if c in note["eden"]:
                parts.append(note["eden"][c])
                continue
            try:
                with open(self._chunk_path(c), encoding="utf-8") as f:
                    parts.append(f.read())
            except FileNotFoundError:
                return None
        s = "".join(parts)
        return base64.b64decode(s) if note["type"] == "newnote" else s.encode("utf-8")

    # --- the files ---------------------------------------------------------------------------

    def rebuild(self):
        notes = self.notes()
        self.fetch_missing(notes)
        os.makedirs(self.gens, mode=0o750, exist_ok=True)
        os.chown(self.gens, 0, self.gid)
        gen = os.path.join(self.gens, time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 10**9:09d}")
        os.makedirs(gen, mode=0o750)
        written, missing, skipped = 0, [], []
        for n in sorted(notes, key=lambda x: x["path"]):
            data = self.content(n)
            if data is None:
                missing.append(n["path"])
                continue
            if len(data) > MAX_FILE:
                skipped.append(n["path"])
                continue
            dest = os.path.join(gen, *n["path"].split("/"))
            parent = os.path.dirname(dest)
            if not os.path.isdir(parent):
                os.makedirs(parent, mode=0o750)
            with open(dest, "wb") as f:
                f.write(data)
            if isinstance(n.get("mtime"), (int, float)) and n["mtime"] > 0:
                os.utime(dest, (n["mtime"] / 1000, n["mtime"] / 1000))
            written += 1
        for root, dirs, files in os.walk(gen):
            os.chown(root, 0, self.gid)
            os.chmod(root, 0o750)
            for name in files:
                p = os.path.join(root, name)
                os.chown(p, 0, self.gid)
                os.chmod(p, 0o640)
        # Swap: a new symlink renamed over the old one (one rename, atomic).
        link_tmp = self.dir + ".new"
        if os.path.lexists(link_tmp):
            os.unlink(link_tmp)
        os.symlink(os.path.relpath(gen, os.path.dirname(self.dir)), link_tmp)
        if os.path.isdir(self.dir) and not os.path.islink(self.dir):
            shutil.rmtree(self.dir)
        os.replace(link_tmp, self.dir)
        for old in sorted(os.listdir(self.gens))[:-KEEP_GENERATIONS]:
            shutil.rmtree(os.path.join(self.gens, old), ignore_errors=True)
        self.gc()
        self.incomplete = bool(missing)
        log(f"copy rebuilt: {written} notes" + (f"; {len(missing)} waiting for chunks" if missing else "")
            + (f"; {len(skipped)} over {MAX_FILE >> 20} MB left out" if skipped else ""))

    def gc(self):
        """Chunks no note uses any more."""
        used = {os.path.basename(self._chunk_path(c)) for n in self.state["notes"].values() for c in n["children"]}
        for name in os.listdir(self.chunks):
            if name not in used and not name.endswith(".tmp"):
                os.unlink(os.path.join(self.chunks, name))


def load():
    cfg = common.load_config()
    if not cfg.get("brain"):
        raise SystemExit("cell.json has no brain section")
    try:
        with open(PASSWORD_FILE, encoding="utf-8") as f:
            pw = f.read().strip()
    except FileNotFoundError:
        raise SystemExit(f"{PASSWORD_FILE} is missing: this cell has no brain account yet") from None
    return cfg, Brain(cfg, pw)


def sync_once(copy):
    if copy.pull() or copy.incomplete:
        copy.rebuild()


def serve():
    cfg, brain = load()
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    copy = Copy(cfg, brain)
    log(f"studio-brain-sync: {brain.user} -> {copy.dir} (leaving out {', '.join(copy.exclude) or 'nothing'})")
    backoff, reconciled = 0, 0.0
    while True:
        try:
            sync_once(copy)
            if time.time() - reconciled > 3600:
                reconciled = time.time()
                if copy.reconcile():
                    copy.rebuild()
            backoff = 0
            # Wait for the next change, then let a burst of edits settle.
            res = brain.changes(copy.state["seq"], longpoll=True)
            if res.get("results"):
                time.sleep(SETTLE)
        except (BrainError, OSError, ValueError) as e:
            backoff = min(300, max(30, backoff * 2))
            log(f"sync failed ({e}); again in {backoff} s")
            time.sleep(backoff)


def check():
    """The account reads and can't write; the infra brains refuse it; the copy is there."""
    cfg, brain = load()
    out, ok = [], True

    def item(name, good, detail):
        nonlocal ok
        ok &= bool(good)
        out.append({"check": name, "ok": bool(good), "detail": detail})

    code, s = brain.call("GET", "/_session")
    ctx = s.get("userCtx", {})
    item("signs in as the cell's reader", code == 200 and ctx.get("name") == brain.user,
         f"{code} {ctx.get('name')} roles={ctx.get('roles')}")
    code, body = brain.call("GET", f"/{brain.db}")
    item("reads the brain", code == 200, f"{code} docs={body.get('doc_count')}")
    probe = f"/{brain.db}/studio-cell-write-probe-{cfg['owner']}"
    code, body = brain.call("PUT", probe, {"probe": "a cell's read-only account must not write"})
    if code in (201, 202):
        brain.call("DELETE", f"{probe}?rev={body.get('rev')}")
    item("can't write the brain", code in (401, 403), f"{code} {body.get('error')} {body.get('reason', '')}"[:160])
    for db in cfg["brain"].get("refused", []):
        code, body = brain.call("GET", f"/{db}")
        item(f"refused on {db}", code in (401, 403) and body.get("error") != "non-json", f"{code} {body.get('error')}")
    d = cfg["brain"]["dir"]
    files = sum(len(f) for _, _, f in os.walk(d)) if os.path.isdir(d) else 0
    item("the copy is there", files > 0, f"{d}: {files} files")
    excluded = [e for e in cfg["brain"].get("exclude", []) if os.path.exists(os.path.join(d, e.rstrip("/")))]
    item("left-out folders are left out", not excluded, f"present: {excluded}" if excluded else "none present")
    print(json.dumps(out, indent=1))
    return 0 if ok else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--check"]:
        return check()
    if argv == ["--once"]:
        cfg, brain = load()
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        sync_once(Copy(cfg, brain))
        return 0
    if argv:
        print(__doc__.split("\n\n")[-1], file=sys.stderr)
        return 2
    serve()
    return 0
