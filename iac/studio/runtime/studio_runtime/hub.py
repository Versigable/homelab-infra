"""The hub, from a cell or the Forge: studio-hubd's :8443, over mutual TLS (role studio_hub_client).

Each host proves who it is with its own key, which never leaves it; the hub's CA signed its
certificate. The hub decides what a caller may do from the certificate's name and the address
it connects from, never from what a request says. Only root reads the key: on a cell that's
studio-agentd and studio-admin, never a job.

Builds travel as a gzipped tar of the build directory, packed deterministically (sorted, no
owners, gzip without a timestamp), so the same build always has the same sha256 and the depot
keeps it once.
"""

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
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    return ti


def pack(build_dir, dest):
    """build_dir's contents as a deterministic .tar.gz at dest; returns (sha256, bytes)."""
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz, \
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
            "exe": os.path.relpath(step["binary"], step["path"]), "exe_sha256": step.get("sha256")}


def gate_summary(gate):
    """What the portal shows of a gate: each step's result and test counts, no paths."""
    rows = []
    for s in gate.get("steps", []):
        t = s.get("tests") or {}
        rows.append({"step": s.get("step"), "passed": bool(s.get("passed")),
                     "tests": {k: t.get(k) for k in ("passed", "failed", "total") if k in t} or None})
    return {"kind": gate.get("kind"), "passed": bool(gate.get("passed")), "steps": rows}


def manifest(game, gate, dep, job=None, mr=None):
    """The build manifest the hub takes (POST /v1/builds). mr is GitLab's MR object."""
    m = {"game": game, "revision": gate["revision"], "target": dep["target"], "object": dep["object"],
         "exe": dep["exe"], "exe_sha256": dep.get("exe_sha256"), "bytes": dep.get("bytes"), "job": job,
         "gates": gate_summary(gate)}
    if mr:
        m["mr"] = {"project_id": mr["project_id"], "iid": mr["iid"], "title": mr["title"],
                   "url": mr["web_url"], "state": mr["state"]}
    return m
