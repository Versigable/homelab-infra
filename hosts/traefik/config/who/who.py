#!/usr/bin/env python3
"""who: which person is using which lab service (admin-only page).

Served at https://who.ninjaprivacy.org through Traefik + Authentik. It asks
each source only when someone opens the page (the answer is cached for 60 s),
so it costs nothing while nobody looks. It keeps no database and writes no
files; the only stored record of names is Traefik's minimal access log.

What it shows, per service: who is on now, and who used it in the last 7 days
(last seen). Never what anyone watched, played, opened or listened to, and no
IPs or devices. Vault: 1-Projects/lab-activity-monitoring.

Sources:
  traefik    /var/log/traefik/access.log* (JSON lines: time, host, router,
             X-Authentik-Username). Only routers that use authentik-forward
             are trusted, read from the dynamic config, so a client can't
             fake a name on a site without forward-auth.
  authentik  event log (authorize_application, login): apps with their own
             Authentik login (Nextcloud, GitLab, ...). Read-only token.
  jellyfin   /Sessions (now) and /Users LastActivityDate (7 days).
  navidrome  /api/user lastAccessAt only. Never library or now-playing.
  games      Steam A2S_PLAYER names, live.

Access: only through Traefik (the port isn't published) and only when
Authentik's X-authentik-groups contains WHO_ADMIN_GROUP.
"""
import concurrent.futures as cf, glob, gzip, html, json, os, re, socket, threading, time
import urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E = os.environ.get
HERE = os.path.dirname(os.path.abspath(__file__))
ADMIN_GROUP = E("WHO_ADMIN_GROUP", "Hokagis")
NOW_WINDOW = timedelta(minutes=15)
DAYS = timedelta(days=7)
CACHE_S = 60
TIMEOUT = 8
ACCESS_GLOB = "/var/log/traefik/access.log*"
DYNAMIC_DIR = "/etc/traefik/dynamic"
SKIP_HOSTS = {"who.ninjaprivacy.org"}

_cache = {"t": 0, "data": None}
_lock = threading.Lock()


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(s):
    if not s or s.startswith("0001"):
        return None
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # trim nanoseconds
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def get_json(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def note(rows, service, user, seen):
    """Keep the newest time per (service, user)."""
    if not user or not seen:
        return
    k = (service, user)
    if k not in rows or rows[k] < seen:
        rows[k] = seen


# ---- traefik ---------------------------------------------------------------
def forward_auth_routers():
    """Router names (as in the access log, name@file) that use authentik-forward."""
    out = set()
    for path in glob.glob(os.path.join(DYNAMIC_DIR, "*.y*ml")):
        cur, in_routers, rindent = None, False, None
        for line in open(path, errors="replace"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            ind = len(line) - len(line.lstrip())
            txt = line.strip()
            if txt == "routers:":
                in_routers, rindent, cur = True, ind, None
                continue
            if in_routers and ind <= rindent:
                in_routers, cur = False, None
            if in_routers and ind == rindent + 2 and txt.endswith(":"):
                cur = txt[:-1].strip("'\"")
            elif in_routers and cur and "authentik-forward" in txt:
                out.add(cur + "@file")
    return out


def traefik(rows):
    trusted = forward_auth_routers()
    since = utcnow() - DAYS
    for path in sorted(glob.glob(ACCESS_GLOB)):
        if os.path.getmtime(path) < since.timestamp():
            continue
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as f:
            for line in f:
                if "Authentik-Username" not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("RouterName") not in trusted:
                    continue
                host = j.get("RequestHost", "")
                if host in SKIP_HOSTS:
                    continue
                user = j.get("request_X-Authentik-Username")
                note(rows, host.split(".")[0], user, parse_time(j.get("StartUTC")))


# ---- authentik -------------------------------------------------------------
def authentik(rows):
    base, tok = E("AUTHENTIK_URL"), E("AUTHENTIK_TOKEN")
    if not (base and tok):
        raise RuntimeError("not configured")
    h = {"Authorization": "Bearer " + tok}
    since = (utcnow() - DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")
    for action in ("authorize_application", "login"):
        page = 1
        while page:
            q = urllib.parse.urlencode({"action": action, "created__gte": since, "ordering": "-created",
                                        "page_size": 500, "page": page})
            d = get_json(f"{base}/api/v3/events/events/?{q}", h)
            for e in d.get("results", []):
                user = (e.get("user") or {}).get("username")
                if action == "login":
                    svc = "Authentik sign-in"
                else:
                    svc = (e.get("context", {}).get("authorized_application") or {}).get("name") or "?"
                    if svc == "Proxmox":  # the domain-wide forward-auth app: Traefik says which site
                        continue
                note(rows, svc, user, parse_time(e.get("created")))
            page = d.get("pagination", {}).get("next") or 0


# ---- jellyfin --------------------------------------------------------------
def jellyfin(rows, now_users):
    base, key = E("JELLYFIN_URL"), E("JELLYFIN_KEY")
    if not (base and key):
        raise RuntimeError("not configured")
    h = {"Authorization": f'MediaBrowser Token="{key}"'}
    for u in get_json(f"{base}/Users", h):
        note(rows, "Jellyfin", u.get("Name"), parse_time(u.get("LastActivityDate")))
    for s in get_json(f"{base}/Sessions?activeWithinSeconds=900", h):
        if s.get("UserName"):
            now_users.setdefault("Jellyfin", set()).add(
                s["UserName"] + (" (playing)" if s.get("NowPlayingItem") else ""))


# ---- navidrome -------------------------------------------------------------
def navidrome(rows):
    base, user, pw = E("NAVIDROME_URL"), E("NAVIDROME_USER"), E("NAVIDROME_PASSWORD")
    if not (base and user and pw):
        raise RuntimeError("not configured")
    tok = get_json(f"{base}/auth/login", {"Content-Type": "application/json"},
                   json.dumps({"username": user, "password": pw}).encode())["token"]
    for u in get_json(f"{base}/api/user", {"x-nd-authorization": "Bearer " + tok}):
        if u.get("userName") != user:
            note(rows, "Navidrome", u.get("userName"), parse_time(u.get("lastAccessAt")))


# ---- games -----------------------------------------------------------------
def a2s_player_names(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        req = b"\xff\xff\xff\xffU"
        s.sendto(req + b"\xff\xff\xff\xff", (host, port))
        d, _ = s.recvfrom(4096)
        if d[4:5] == b"A":
            s.sendto(req + d[5:9], (host, port))
            d, _ = s.recvfrom(4096)
        if d[4:5] != b"D":
            raise ValueError
        n, p, names = d[5], 6, []
        for _ in range(n):
            p += 1
            end = d.index(b"\x00", p)
            names.append(d[p:end].decode(errors="replace"))
            p = end + 9  # score (4) + duration (4)
        return [x for x in names if x]
    finally:
        s.close()


def games(now_users):
    with open(os.path.join(HERE, "servers.json")) as f:
        servers = [x for x in json.load(f)["servers"] if x.get("kind", "a2s") == "a2s"]

    def one(srv):
        for port in srv["ports"]:
            try:
                return srv["name"], a2s_player_names(srv["host"], port)
            except (OSError, ValueError, IndexError):
                continue
        return srv["name"], None

    with cf.ThreadPoolExecutor(max(1, len(servers))) as ex:
        for name, players in ex.map(one, servers):
            if players:
                now_users.setdefault("Game: " + name, set()).update(players)


# ---- page ------------------------------------------------------------------
def collect():
    rows, now_users, errors = {}, {}, {}
    jobs = {"Traefik log": lambda: traefik(rows), "Authentik": lambda: authentik(rows),
            "Jellyfin": lambda: jellyfin(rows, now_users), "Navidrome": lambda: navidrome(rows),
            "Games": lambda: games(now_users)}
    with cf.ThreadPoolExecutor(len(jobs)) as ex:
        futs = {k: ex.submit(v) for k, v in jobs.items()}
        for k, f in futs.items():
            try:
                f.result(timeout=20)
            except Exception as e:
                errors[k] = type(e).__name__ + (": " + str(e) if isinstance(e, RuntimeError) else "")
    cut_now, cut_old = utcnow() - NOW_WINDOW, utcnow() - DAYS
    svc = {}
    for (service, user), seen in rows.items():
        if seen < cut_old:
            continue
        svc.setdefault(service, []).append((user, seen))
        if seen >= cut_now:
            now_users.setdefault(service, set()).add(user)
    return {"svc": svc, "now": now_users, "errors": errors, "at": utcnow()}


def ago(t):
    s = int((utcnow() - t).total_seconds())
    if s < 3600:
        return f"{max(s // 60, 0)} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--on:#1f7a4d;--onbg:#e3f3ea}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#9a9993;--card:#1f1f1d;--line:#33332f;--on:#6fd39f;--onbg:#173326}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 12px}
.card h2{font-size:16px;margin:0 0 8px;display:flex;justify-content:space-between;gap:8px}
.pill{background:var(--onbg);color:var(--on);border-radius:99px;padding:1px 9px;font-size:13px;font-weight:600;white-space:nowrap}
.idle{color:var(--muted);font-weight:400;font-size:13px}ul{margin:0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:6px 14px}
li{white-space:nowrap}li .t{color:var(--muted);font-size:13px}li.on{color:var(--on);font-weight:600}.err{color:#b4442c;font-size:13px}
"""


def render(d):
    names = sorted(set(d["svc"]) | set(d["now"]), key=lambda s: (-len(d["now"].get(s, ())), s.lower()))
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Who's on</title><style>{CSS}</style></head><body><main><h1>Who's on</h1>"
           f"<p class=sub>Per service: on now (last 15 min) in green, then everyone seen in the last 7 days. "
           f"As of {d['at'].strftime('%H:%M UTC')}; refreshes at most once a minute. Admins only.</p>"]
    for s in names:
        on = sorted(d["now"].get(s, ()))
        badge = f"<span class=pill>{len(on)} on now</span>" if on else "<span class=idle>nobody now</span>"
        items = [f"<li class=on>{html.escape(u)}</li>" for u in on]
        onbase = {u.split(" (")[0] for u in on}
        for u, t in sorted(d["svc"].get(s, []), key=lambda x: x[1], reverse=True):
            if u not in onbase:
                items.append(f"<li>{html.escape(u)} <span class=t>{ago(t)}</span></li>")
        out.append(f"<section class=card><h2>{html.escape(s)} {badge}</h2><ul>{''.join(items)}</ul></section>")
    if not names:
        out.append("<p class=sub>Nobody seen in the last 7 days.</p>")
    for k, v in d["errors"].items():
        out.append(f"<p class=err>{html.escape(k)}: couldn't read ({html.escape(v)})</p>")
    out.append("<p class=sub>Not covered by name: Ente, Bitwarden, SSH logins, Valheim/Windrose/Minecraft "
               "(counts only, on the Grafana Lab activity dashboard).</p></main></body></html>")
    return "".join(out).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, b"ok\n", "text/plain")
        groups = re.split(r"[|,]", self.headers.get("X-authentik-groups", ""))
        if ADMIN_GROUP not in [g.strip() for g in groups]:
            return self.reply(403, b"Admins only.\n", "text/plain")
        with _lock:
            if not _cache["data"] or time.time() - _cache["t"] > CACHE_S:
                _cache["data"], _cache["t"] = collect(), time.time()
            d = _cache["data"]
        self.reply(200, render(d), "text/html; charset=utf-8")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # no request logs: they'd hold names
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(E("WHO_PORT", "8080"))), Handler).serve_forever()
