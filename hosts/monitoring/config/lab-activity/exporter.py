#!/usr/bin/env python3
"""lab-activity exporter: how busy is the lab right now? (counts only)

Serves Prometheus metrics on :9469/metrics. Each scrape queries the game
servers live (Steam A2S and Minecraft Bedrock pings, UDP, no credentials),
in parallel, with a 2 s timeout per server. A server that doesn't answer
is reported as lab_game_up 0 (usually: not running).

Never exports names, titles or IPs: only counts. Plan and privacy rules:
vault 1-Projects/lab-activity-monitoring.

Servers come from servers.json next to this file, so adding a game is a
config edit, not a code change.
"""
import concurrent.futures as cf, json, os, socket, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LAB_ACTIVITY_PORT", "9469"))
TIMEOUT = 2.0


def a2s_players(host, port):
    """Players and max players from an A2S_INFO reply (handles the challenge)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        req = b"\xff\xff\xff\xffTSource Engine Query\x00"
        s.sendto(req, (host, port))
        d, _ = s.recvfrom(4096)
        if d[4:5] == b"A":  # challenge: resend with the 4-byte token
            s.sendto(req + d[5:9], (host, port))
            d, _ = s.recvfrom(4096)
        if d[4:5] != b"I":
            raise ValueError("not an A2S_INFO reply")
        p = 6  # header(4) + type(1) + protocol(1), then name, map, folder, game
        for _ in range(4):
            p = d.index(b"\x00", p) + 1
        return d[p + 2], d[p + 3]  # after the 2-byte app id
    finally:
        s.close()


def bedrock_players(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        magic = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
        s.sendto(b"\x01" + b"\0" * 8 + magic + b"\0" * 8, (host, port))
        d, _ = s.recvfrom(4096)
        f = d[35:].decode(errors="replace").split(";")
        return int(f[4]), int(f[5])
    finally:
        s.close()


def probe(srv):
    """(server dict, players, max) or (server, None, None) when down."""
    fn = bedrock_players if srv.get("kind") == "bedrock" else a2s_players
    for port in srv["ports"]:
        try:
            n, mx = fn(srv["host"], port)
            return srv, n, mx
        except (OSError, ValueError, IndexError):
            continue
    return srv, None, None


def load_servers():
    with open(os.path.join(HERE, "servers.json")) as f:
        return json.load(f)["servers"]


def lbl(srv):
    return 'server="%s",host="%s"' % (srv["name"], srv["where"])


def render():
    t0 = time.time()
    servers = load_servers()
    with cf.ThreadPoolExecutor(max(1, len(servers))) as ex:
        results = list(ex.map(probe, servers))
    out = [
        "# HELP lab_game_up 1 if the game server answered its status query.",
        "# TYPE lab_game_up gauge",
    ]
    out += ["lab_game_up{%s} %d" % (lbl(s), n is not None) for s, n, _ in results]
    out += ["# HELP lab_game_players Players connected now.", "# TYPE lab_game_players gauge"]
    out += ["lab_game_players{%s} %d" % (lbl(s), n) for s, n, _ in results if n is not None]
    out += ["# HELP lab_game_max_players Player slots.", "# TYPE lab_game_max_players gauge"]
    out += ["lab_game_max_players{%s} %d" % (lbl(s), m) for s, n, m in results if n is not None]
    out += [
        "# HELP lab_activity_scrape_duration_seconds Time to query every source.",
        "# TYPE lab_activity_scrape_duration_seconds gauge",
        "lab_activity_scrape_duration_seconds %.3f" % (time.time() - t0),
    ]
    return ("\n".join(out) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/metrics":
            try:
                body, code = render(), 200
            except Exception as e:  # config error etc.: say so, don't die
                body, code = ("error: %s\n" % type(e).__name__).encode(), 500
            ctype = "text/plain; version=0.0.4"
        elif self.path == "/healthz":
            body, code, ctype = b"ok\n", 200, "text/plain"
        else:
            body, code, ctype = b"not found\n", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # no per-request logs
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
