"""Slack for the studio desk (vault decision 2026-11-15, "the Slack desk"): Socket Mode over a
small WebSocket client, and the Web API over urllib. Standard library only, like the rest of
the runtime.

Socket Mode: the cell connects out to Slack (apps.connections.open with the app-level token
gives a one-time wss URL) and Slack sends events down that connection. Nothing listens for
Slack anywhere.

The WebSocket client (RFC 6455, client side): TLS through ssl, the HTTP/1.1 upgrade with its
Sec-WebSocket-Accept check, masked frames out, text and continuation frames in (a message
split over several frames is joined), ping answered with pong, close answered with close.
recv() takes a timeout, so the caller's loop can notice a shutdown between messages.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://slack.com/api"
USER_AGENT = "studio-slackd/1.0"
# The bot token goes only to Slack's own hosts: a file's download URL must start with one of
# these (tests add their fake).
FILES_PREFIXES = ("https://files.slack.com/",)
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 16 << 20

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class SlackError(Exception):
    def __init__(self, method, error, detail=None):
        super().__init__(f"Slack {method}: {error}" + (f" ({detail})" if detail else ""))
        self.method, self.error = method, error


class WebSocketClosed(Exception):
    pass


# --- the WebSocket client ------------------------------------------------------------------------

class WebSocket:
    """One client connection. send_text() and recv() may be used from different threads."""

    def __init__(self, url, timeout=30, context=None):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError(f"not a WebSocket URL: {u.scheme}://")
        host = u.hostname
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.io_timeout = timeout
        sock = socket.create_connection((host, port), timeout=timeout)
        if u.scheme == "wss":
            sock = (context or ssl.create_default_context()).wrap_socket(sock, server_hostname=host)
        self.sock = sock
        self.buf = b""
        self.closed = False
        self._send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        hostport = host if u.port is None else f"{host}:{port}"
        req = (f"GET {path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: {USER_AGENT}\r\n\r\n")
        sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketClosed("the server closed the connection during the upgrade")
            head += chunk
            if len(head) > 65536:
                raise WebSocketClosed("the upgrade response is too large")
        head, self.buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ", 2)
        if len(parts) < 2 or parts[1] != "101":
            raise WebSocketClosed(f"upgrade refused: {lines[0]}")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            raise WebSocketClosed("the upgrade's Sec-WebSocket-Accept doesn't match the key")

    # framing

    def _frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return head + mask + body

    def _send(self, opcode, payload):
        with self._send_lock:
            self.sock.sendall(self._frame(opcode, payload))

    def send_text(self, text):
        if self.closed:
            raise WebSocketClosed("closed")
        self._send(OP_TEXT, text.encode("utf-8"))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                self.closed = True
                raise WebSocketClosed("the server closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read_frame(self):
        b0, b1 = self._need(2)
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        masked, n = bool(b1 & 0x80), b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._need(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._need(8))[0]
        if n > MAX_MESSAGE:
            raise WebSocketClosed(f"a frame of {n} bytes is too large")
        mask = self._need(4) if masked else None
        payload = self._need(n) if n else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv(self, timeout=None):
        """The next text message, or None if nothing started arriving within timeout seconds.
        Raises WebSocketClosed when the connection ends."""
        if self.closed:
            raise WebSocketClosed("closed")
        parts, kind = [], None
        while True:
            if not self.buf and not parts:
                self.sock.settimeout(timeout)
                try:
                    chunk = self.sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    return None
                except ssl.SSLWantReadError:
                    return None
                if not chunk:
                    self.closed = True
                    raise WebSocketClosed("the server closed the connection")
                self.buf += chunk
            self.sock.settimeout(self.io_timeout)
            fin, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                try:
                    self._send(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                self.closed = True
                raise WebSocketClosed(f"closed by the server ({code})")
            if opcode in (OP_TEXT, OP_BINARY):
                parts, kind = [payload], opcode
            elif opcode == OP_CONT and kind is not None:
                parts.append(payload)
                if sum(len(p) for p in parts) > MAX_MESSAGE:
                    raise WebSocketClosed("a message is too large")
            else:
                raise WebSocketClosed(f"unexpected frame opcode {opcode}")
            if fin:
                data = b"".join(parts)
                return data.decode("utf-8") if kind == OP_TEXT else data.decode("utf-8", "replace")

    def close(self):
        if not self.closed:
            try:
                self._send(OP_CLOSE, struct.pack(">H", 1000))
            except OSError:
                pass
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


# --- the Web API --------------------------------------------------------------------------------

class Api:
    """Slack's Web API with one token (the bot's, or the app-level one for Socket Mode)."""

    def __init__(self, token, base=None, retries=3):
        self.token = token
        self.base = (base or API).rstrip("/")
        self.retries = retries

    def call(self, method, **params):
        """POST form-encoded (lists and dicts as JSON, as Slack takes them); returns the reply.
        A 429 waits Retry-After and tries again; ok:false raises SlackError."""
        form = {}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            elif isinstance(v, bool):
                v = "true" if v else "false"
            form[k] = str(v)
        data = urllib.parse.urlencode(form).encode()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(f"{self.base}/{method}", data=data, method="POST")
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
            req.add_header("User-Agent", USER_AGENT)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    res = json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.retries:
                    time.sleep(min(int(e.headers.get("Retry-After") or 1), 60))
                    continue
                raise SlackError(method, f"HTTP {e.code}", e.read()[:200].decode(errors="replace")) from None
            except urllib.error.URLError as e:
                raise SlackError(method, "unreachable", str(e.reason)) from None
            if not res.get("ok"):
                if res.get("error") == "ratelimited" and attempt < self.retries:
                    time.sleep(1)
                    continue
                raise SlackError(method, res.get("error") or "not ok", res.get("needed"))
            return res
        raise SlackError(method, "ratelimited")

    def download(self, url, limit):
        """A file someone shared, with the bot token: only from Slack's file host, at most limit
        bytes (more raises SlackError)."""
        if not any(url.startswith(p) for p in FILES_PREFIXES):
            raise SlackError("download", "not a Slack file URL", url[:80])
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if not any(r.geturl().startswith(p) for p in FILES_PREFIXES):
                    raise SlackError("download", "redirected off Slack", r.geturl()[:80])
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise SlackError("download", f"HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise SlackError("download", "unreachable", str(e.reason)) from None
        if len(data) > limit:
            raise SlackError("download", "too large", f"over {limit >> 20} MB")
        return data


def socket_mode_url(app_token, base=None):
    """A fresh Socket Mode URL (one use each) from the app-level token."""
    return Api(app_token, base=base).call("apps.connections.open")["url"]
