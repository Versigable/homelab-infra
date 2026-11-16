"""The desk's working copy of the studio brain, for studio-slackd (vault decision 2026-11-15,
"the Slack desk").

The person's desk in Slack works in /srv/studio/desk/brain: plain files its Claude reads and
edits as the user desk. studio-slackd (root) keeps the copy in step with the brain's CouchDB
between turns, signed in as the desk's own account (studio-desk-<owner>, a member: it writes
with the person's rights; its password in /etc/studio/slack/brain-password, root's). The copy
holds every note except other people's desk memory.

  pull    the brain's changes since the last pull, into the files (desk:desk 0640); a file the
          desk deleted comes back
  push    every file the desk wrote or changed since, as a LiveSync note, against the revision
          the copy pulled: a note changed in Obsidian meanwhile is never overwritten (a
          conflict, reported). It never deletes, and never writes CLAUDE.md, a hidden path,
          another person's memory or a file over the size limit.

LiveSync's documents (no encryption, no path obfuscation; read from the live brain on
2026-11-15): a note's _id is its path in lower case, with "/" before a leading "_"; the note
keeps the path as written, its chunk ids in `children` (`eden` empty), ctime and mtime in ms
and its size in bytes; `deleted: true` marks a deleted one. Text (`plain`) chunks hold pieces of
the text, binary (`newnote`) chunks pieces of its base64. A chunk is a `leaf` doc
{_id: "h:...", type, data}; this module's chunk ids are longer than LiveSync's own, so the two
never collide.

Root reads and writes the copy without following links: every path component is opened with
O_NOFOLLOW from the one above, and only regular files the desk owns, with one link, are read.
"""

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import urllib.parse

from . import VERSION, brainsync, common

STATE_DIR = os.path.join(common.STATE_DIR, "slack", "brain")
PASSWORD_FILE = "/etc/studio/slack/brain-password"
NOTE_TYPES = brainsync.NOTE_TYPES
TEXT_EXT = (".md", ".txt", ".canvas", ".json", ".css", ".csv", ".yaml", ".yml", ".svg", ".html")
TEXT_CHUNK = 1000            # characters of text per chunk (LiveSync's own are a few hundred)
B64_CHUNK = 64 * 1024        # characters of base64 per chunk of a binary note
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
TMP_PREFIX = ".deskbrain-"
# Tests replace this (a test can't chown to another user).
fchown = os.fchown


def log(msg):
    print(msg, flush=True)


class Refused(Exception):
    pass


def path2id(path):
    """LiveSync's document id for a path (case-insensitive ids, "/" before a leading "_")."""
    return ("/" + path if path.startswith("_") else path).lower()


def _base36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


def chunk_id(piece):
    return "h:" + _base36(int.from_bytes(hashlib.sha256(piece.encode("utf-8")).digest()[:16], "big"))


def is_text(path, data):
    if not path.lower().endswith(TEXT_EXT):
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def pieces(path, data):
    """(type, [chunk text...]) for a file's bytes, the way the note stores them."""
    if is_text(path, data):
        s = data.decode("utf-8")
        return "plain", [s[i:i + TEXT_CHUNK] for i in range(0, len(s), TEXT_CHUNK)] or [""]
    s = base64.b64encode(data).decode("ascii")
    return "newnote", [s[i:i + B64_CHUNK] for i in range(0, len(s), B64_CHUNK)] or [""]


def note_doc(path, data, mtime_ms, ctime_ms=None, rev=None):
    """The LiveSync note for a file, and its chunks: (note, {chunk id: chunk text})."""
    kind, parts = pieces(path, data)
    chunks = {}
    for p in parts:
        chunks.setdefault(chunk_id(p), p)
    doc = {"_id": path2id(path), "path": path, "children": [chunk_id(p) for p in parts],
           "ctime": int(ctime_ms if ctime_ms is not None else mtime_ms), "mtime": int(mtime_ms),
           "size": len(data), "type": kind, "eden": {}}
    if rev:
        doc["_rev"] = rev
    return doc, chunks


def load_password(path=PASSWORD_FILE):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


class DeskCopy:
    """The working copy (files under desk_brain.dir) and its index (state dir: each note's id,
    path, revision, the sha256 of its bytes, its chunk ids; the chunks themselves)."""

    def __init__(self, cfg, password, owner_uid, owner_gid, state_dir=None):
        b = cfg["desk_brain"]
        self.brain = brainsync.Brain({"brain": b, "owner": cfg["owner"]}, password)
        self.brain.ua = f"studio-slackd/{VERSION} (cell-{cfg['owner']} desk)"
        self.db = b["db"]
        self.dir = b["dir"].rstrip("/")
        self.memory = (b.get("memory") or "").lower()
        if self.memory and not self.memory.endswith("/"):
            self.memory += "/"
        self.uid, self.gid = owner_uid, owner_gid
        self.max_bytes = int((cfg.get("slack") or {}).get("max_file_mb", 5)) << 20
        self.state_dir = state_dir or STATE_DIR
        self.chunks = os.path.join(self.state_dir, "chunks")
        os.makedirs(self.chunks, mode=0o700, exist_ok=True)
        self.state_file = os.path.join(self.state_dir, "state.json")
        try:
            with open(self.state_file, encoding="utf-8") as f:
                self.state = json.load(f)
        except (FileNotFoundError, ValueError):
            self.state = {}
        self.state.setdefault("seq", "0")
        self.state.setdefault("notes", {})
        self.lock = threading.RLock()

    def save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_file)

    # --- which paths -----------------------------------------------------------------------

    @staticmethod
    def valid(path):
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            return False
        if any(ord(c) < 32 for c in path):
            return False
        return not any(p in ("", ".", "..") or p.startswith(".") for p in path.split("/"))

    def wanted(self, path):
        """In the copy: every note but other people's desk memory."""
        if not self.valid(path):
            return False
        low = path.lower()
        return not low.startswith("memory/") or bool(self.memory) and low.startswith(self.memory)

    def refusal(self, path):
        """Why the push won't write this path, or None."""
        if not self.valid(path):
            return "hidden or not a plain path"
        if path.lower() == "claude.md":
            return "CLAUDE.md is changed by people, in Obsidian"
        if not self.wanted(path):
            return "another person's memory"
        return None

    # --- the files, never through a link ------------------------------------------------------

    def _root(self, create=True):
        parent, name = os.path.split(self.dir)
        pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                return os.open(name, DIR_FLAGS, dir_fd=pfd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o750, dir_fd=pfd)
                fd = os.open(name, DIR_FLAGS, dir_fd=pfd)
                fchown(fd, self.uid, self.gid)
                return fd
            except OSError as e:
                raise Refused(f"{self.dir} is a link or not a folder") from e
        finally:
            os.close(pfd)

    def _dir(self, parts, create=False):
        """A descriptor for the folder at parts under the copy, opened one name at a time."""
        fd = self._root()
        try:
            for p in parts:
                try:
                    nfd = os.open(p, DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(p, 0o750, dir_fd=fd)
                    nfd = os.open(p, DIR_FLAGS, dir_fd=fd)
                    fchown(nfd, self.uid, self.gid)
                except OSError as e:
                    raise Refused(f"{'/'.join(parts)}: a link or a file where a folder should be") from e
                os.close(fd)
                fd = nfd
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _write(self, rel, data, mtime_ms=None):
        """The file, whole, through a temporary file renamed over it (desk:desk 0640)."""
        parts = rel.split("/")
        dfd = self._dir(parts[:-1], create=True)
        try:
            name = parts[-1]
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                    raise Refused(f"{rel}: something other than a file is there")
            except FileNotFoundError:
                pass
            tmp = f"{TMP_PREFIX}{os.getpid()}-{secrets.token_hex(4)}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=dfd)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    fchown(f.fileno(), self.uid, self.gid)
                    os.fchmod(f.fileno(), 0o640)
                    if isinstance(mtime_ms, (int, float)) and mtime_ms > 0:
                        t = int(mtime_ms) * 1_000_000
                        os.utime(f.fileno(), ns=(t, t))
                # A link in the way is replaced, not followed: rename acts on the name.
                os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except BaseException:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass
                raise
        finally:
            os.close(dfd)

    def _read(self, rel):
        """(bytes, stat, None), or (None, None, why not): a regular file the desk owns, with
        one link, under the size limit; never through a link."""
        parts = rel.split("/")
        try:
            dfd = self._dir(parts[:-1])
        except FileNotFoundError:
            return None, None, "gone"
        except Refused as e:
            return None, None, str(e)
        try:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except FileNotFoundError:
                return None, None, "gone"
            except OSError:
                return None, None, "a link"
            with os.fdopen(fd, "rb") as f:
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode):
                    return None, None, "not a regular file"
                if st.st_uid != self.uid:
                    return None, None, "not the desk's file"
                if st.st_nlink != 1:
                    return None, None, "it has other links"
                data = f.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    return None, None, f"over {self.max_bytes >> 20} MB"
                return data, st, None
        finally:
            os.close(dfd)

    def _remove(self, rel):
        parts = rel.split("/")
        try:
            dfd = self._dir(parts[:-1])
        except (FileNotFoundError, Refused):
            return False
        try:
            st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                os.unlink(parts[-1], dir_fd=dfd)
                return True
            return False
        except FileNotFoundError:
            return False
        finally:
            os.close(dfd)

    def _walk(self):
        """[(path, kind)] for everything in the copy, never through a link. kind: file, link,
        other (not a file or folder), hidden."""
        out = []

        def rec(dfd, prefix):
            with os.scandir(dfd) as it:
                names = sorted(e.name for e in it)
            for name in names:
                rel = prefix + name
                if name.startswith("."):
                    if not name.startswith(TMP_PREFIX):
                        out.append((rel, "hidden"))
                    continue
                try:
                    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    try:
                        nfd = os.open(name, DIR_FLAGS, dir_fd=dfd)
                    except OSError:
                        out.append((rel, "link"))
                        continue
                    try:
                        rec(nfd, rel + "/")
                    finally:
                        os.close(nfd)
                elif stat.S_ISREG(st.st_mode):
                    out.append((rel, "file"))
                elif stat.S_ISLNK(st.st_mode):
                    out.append((rel, "link"))
                else:
                    out.append((rel, "other"))

        root = self._root()
        try:
            rec(root, "")
        finally:
            os.close(root)
        return out

    # --- chunks -------------------------------------------------------------------------------

    def _chunk_path(self, cid):
        return os.path.join(self.chunks, hashlib.sha256(cid.encode()).hexdigest())

    def _put_chunk(self, cid, data):
        p = self._chunk_path(cid)
        if not os.path.exists(p):
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(p + ".tmp", p)

    def _fetch(self, docs):
        for d in docs:
            for cid, e in (d.get("eden") or {}).items():
                if isinstance(e, dict) and isinstance(e.get("data"), str):
                    self._put_chunk(cid, e["data"])
        need = sorted({c for d in docs for c in d.get("children") or []
                       if not os.path.exists(self._chunk_path(c))})
        for i in range(0, len(need), 200):
            for doc in self.brain.docs(need[i:i + 200]):
                if doc.get("type") == "leaf" and isinstance(doc.get("data"), str):
                    self._put_chunk(doc["_id"], doc["data"])

    def _content(self, kind, children):
        parts = []
        for c in children:
            try:
                with open(self._chunk_path(c), encoding="utf-8") as f:
                    parts.append(f.read())
            except FileNotFoundError:
                return None
        s = "".join(parts)
        try:
            return base64.b64decode(s) if kind == "newnote" else s.encode("utf-8")
        except ValueError:
            return None

    def _gc(self):
        used = {os.path.basename(self._chunk_path(c)) for n in self.state["notes"].values() for c in n["children"]}
        for name in os.listdir(self.chunks):
            if name not in used and not name.endswith(".tmp"):
                os.unlink(os.path.join(self.chunks, name))

    # --- pull ---------------------------------------------------------------------------------

    def _live(self, doc):
        return (not doc.get("_deleted") and not doc.get("deleted") and doc.get("type") in NOTE_TYPES
                and isinstance(doc.get("path"), str) and self.wanted(doc["path"]))

    def _drop(self, did, rec, res):
        """A note gone from the copy: its file goes too, unless the desk changed it since."""
        self.state["notes"].pop(did, None)
        data, _, why = self._read(rec["path"])
        if why == "gone":
            return
        if data is not None and hashlib.sha256(data).hexdigest() != rec["sha"]:
            res["kept_local"].append(rec["path"])
            return
        if self._remove(rec["path"]):
            res["removed"].append(rec["path"])

    def pull(self):
        """The brain's changes since the last pull into the files. Between turns only."""
        with self.lock:
            res = {"written": [], "removed": [], "kept_local": []}
            changed = {}
            while True:
                body = self.brain.changes(self.state["seq"])
                for row in body.get("results", []):
                    doc = row.get("doc") or {"_id": row.get("id")}
                    if row.get("deleted"):
                        doc["_deleted"] = True
                    if doc.get("_id"):
                        changed[doc["_id"]] = doc
                self.state["seq"] = body.get("last_seq", self.state["seq"])
                if not body.get("results") or not body.get("pending"):
                    break
            live = [d for d in changed.values() if self._live(d)]
            self._fetch(live)
            for did, doc in changed.items():
                rec = self.state["notes"].get(did)
                if not self._live(doc):
                    if rec:
                        self._drop(did, rec, res)
                    continue
                if rec and rec.get("rev") == doc.get("_rev"):
                    continue                        # the push's own write, coming back
                data = self._content(doc["type"], doc.get("children") or [])
                if data is None:
                    log(f"desk brain: {doc['path']} waits for chunks")
                    continue
                if len(data) > self.max_bytes:
                    log(f"desk brain: {doc['path']} is over {self.max_bytes >> 20} MB, left out")
                    continue
                if rec and rec["path"] != doc["path"]:
                    self._drop(did, rec, res)       # renamed in Obsidian (a case change)
                try:
                    self._write(doc["path"], data, doc.get("mtime"))
                except (Refused, OSError) as e:
                    log(f"desk brain: {doc['path']} not written ({e})")
                    continue
                self.state["notes"][did] = {"path": doc["path"], "rev": doc.get("_rev"), "type": doc["type"],
                                            "sha": hashlib.sha256(data).hexdigest(),
                                            "children": list(doc.get("children") or []),
                                            "ctime": doc.get("ctime"), "mtime": doc.get("mtime")}
                res["written"].append(doc["path"])
            # What the desk deleted comes back: the copy is the brain's, and the push never deletes.
            on_disk = {rel for rel, kind in self._walk() if kind == "file"}
            for did, rec in list(self.state["notes"].items()):
                if rec["path"] in on_disk:
                    continue
                data = self._content(rec["type"], rec["children"])
                if data is None:
                    continue
                try:
                    self._write(rec["path"], data, rec.get("mtime"))
                    res["written"].append(rec["path"])
                except (Refused, OSError) as e:
                    log(f"desk brain: {rec['path']} not put back ({e})")
            self._gc()
            self.save()
            if res["written"] or res["removed"] or res["kept_local"]:
                log(f"desk brain pulled: {len(res['written'])} written, {len(res['removed'])} removed"
                    + (f", kept the desk's {res['kept_local']}" if res["kept_local"] else ""))
            return res

    # --- push ---------------------------------------------------------------------------------

    def _doc(self, did):
        code, body = self.brain.call("GET", f"/{self.db}/{urllib.parse.quote(did, safe='')}")
        if code == 404:
            return None
        if code != 200:
            raise brainsync.BrainError(f"GET a note: {code} {body.get('error')}")
        return body

    def _put(self, did, body):
        return self.brain.call("PUT", f"/{self.db}/{urllib.parse.quote(did, safe='')}", body)

    def push(self):
        """Every file the desk wrote or changed, into the brain as the person. After a turn."""
        with self.lock:
            res = {"written": [], "conflicts": [], "refused": {}}
            for rel, kind in self._walk():
                if kind != "file":
                    res["refused"][rel] = {"hidden": "hidden", "link": "a link",
                                           "other": "not a regular file"}[kind]
                    continue
                did = path2id(rel)
                rec = self.state["notes"].get(did)
                data, st, why = self._read(rel)
                if why:
                    res["refused"][rel] = why
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if rec and rec["path"] == rel and rec["sha"] == sha:
                    continue
                why = self.refusal(rel)
                if why:
                    res["refused"][rel] = why
                    if rec and rec["path"] == rel:  # put the brain's version back
                        old = self._content(rec["type"], rec["children"])
                        if old is not None:
                            self._write(rel, old, rec.get("mtime"))
                    continue
                if rec and rec["path"] != rel:
                    res["refused"][rel] = f"{rec['path']} differs only in case; the brain keeps one"
                    continue
                try:
                    self._push_one(rel, did, rec, data, st, sha, res)
                except brainsync.BrainError as e:
                    res["refused"][rel] = f"the brain didn't take it ({e})"[:200]
            self.save()
            if res["written"] or res["conflicts"] or res["refused"]:
                log(f"desk brain pushed: {len(res['written'])} written"
                    + (f", conflicts {res['conflicts']}" if res["conflicts"] else "")
                    + (f", refused {sorted(res['refused'])}" if res["refused"] else ""))
            return res

    def _push_one(self, rel, did, rec, data, st, sha, res):
        cur = self._doc(did)
        base = None
        if rec:
            if cur is not None and cur.get("_rev") != rec.get("rev"):
                res["conflicts"].append(rel)       # changed in the brain since the pull
                return
            base = cur.get("_rev") if cur else None
        elif cur is not None:
            if not cur.get("deleted"):
                res["conflicts"].append(rel)       # someone made it in the brain meanwhile
                return
            base = cur["_rev"]                     # a deleted note, written again
        mtime = st.st_mtime_ns // 1_000_000
        if cur and not cur.get("deleted") and isinstance(cur.get("ctime"), int):
            ctime = cur["ctime"]
        elif rec and isinstance(rec.get("ctime"), int):
            ctime = rec["ctime"]
        else:
            ctime = int(time.time() * 1000)
        doc, chunks = note_doc(rel, data, mtime, ctime, base)
        for cid, piece in chunks.items():
            code, body = self._put(cid, {"_id": cid, "type": "leaf", "data": piece})
            if code not in (201, 202, 409):
                raise brainsync.BrainError(f"chunk: {code} {body.get('error')}")
        code, body = self._put(did, doc)
        if code == 409:
            res["conflicts"].append(rel)
            return
        if code not in (201, 202):
            raise brainsync.BrainError(f"note: {code} {body.get('error')}")
        for cid, piece in chunks.items():
            self._put_chunk(cid, piece)
        self.state["notes"][did] = {"path": rel, "rev": body.get("rev"), "type": doc["type"], "sha": sha,
                                    "children": doc["children"], "ctime": doc["ctime"], "mtime": doc["mtime"]}
        res["written"].append(rel)

    # --- a new file from Slack ----------------------------------------------------------------

    def write_file(self, rel, data):
        """A new file in the copy (a picture from Slack, say), under a free name; the next push
        writes it to the brain. Returns the path used."""
        with self.lock:
            why = self.refusal(rel)
            if why:
                raise Refused(f"{rel}: {why}")
            if len(data) > self.max_bytes:
                raise Refused(f"{rel}: over {self.max_bytes >> 20} MB")
            head, _, name = rel.rpartition("/")
            stem, dot, ext = name.rpartition(".")
            if not dot:
                stem, ext = name, ""
            n, cand = 1, rel
            while True:
                taken = path2id(cand) in self.state["notes"]
                if not taken:
                    parts = cand.split("/")
                    try:
                        dfd = self._dir(parts[:-1])
                    except FileNotFoundError:
                        dfd = None
                    if dfd is not None:
                        try:
                            os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
                            taken = True
                        except FileNotFoundError:
                            pass
                        finally:
                            os.close(dfd)
                if not taken:
                    break
                n += 1
                cand = (head + "/" if head else "") + f"{stem}-{n}" + (f".{ext}" if dot else "")
            self._write(cand, data)
            return cand
