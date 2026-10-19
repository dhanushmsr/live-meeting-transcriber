"""Local report server: serves the online report page from this computer.

Reports live in ~/.inkwake/reports/<id>.json and are deleted 72 hours after they were created.
The page is http://localhost:<port>/r/<id> and only works while Ink is running. It is bound to
127.0.0.1, so nobody else on the network can open it. The same viewer.html is used by the
Cloudflare Worker in cloud/, so a report looks and exports identically in both places.
"""

import json
import re
import secrets
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import HOME

TTL_SECONDS = 72 * 3600
REPORTS = HOME / "reports"
VIEWER = Path(__file__).parent / "assets" / "viewer.html"
FIRST_PORT = 8731
ID_RE = re.compile(r"^[a-f0-9]{8,64}$")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cache-Control": "private, no-store",
}

class _Server(ThreadingHTTPServer):
    """Refuses a port another program already holds. By default Windows lets two sockets share a port, so a second
    Ink (or any other app) would silently steal requests and report links would open the wrong server."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_asker = None  # callable(record: dict, question: str) -> str, set by the app (it owns the Gemini key)
_ask_slots = threading.BoundedSemaphore(2)  # at most two answers being generated at once
MAX_ASK_BYTES = 20_000


def set_asker(fn) -> None:
    global _asker
    _asker = fn


def _path(report_id: str) -> Path:
    return REPORTS / f"{report_id}.json"


def purge_expired() -> None:
    """Delete reports older than 72 hours."""
    now = time.time()
    for f in REPORTS.glob("*.json"):
        try:
            if now - f.stat().st_mtime > TTL_SECONDS:
                f.unlink()
        except OSError:
            pass


def load(report_id: str) -> dict | None:
    if not ID_RE.match(report_id):
        return None
    purge_expired()
    try:
        return json.loads(_path(report_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.split("?")[0] == "/api/ask":
            return _handle_ask(self)
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_GET(self) -> None:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1", "[::1]"):  # blocks DNS-rebinding tricks
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path.startswith("/r/"):  # the viewer page; it loads the data from /api/report/<id>
            try:
                return self._send(200, VIEWER.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, b"viewer missing", "text/plain")
        if path == "/api/capabilities":
            return self._send(200, json.dumps({"ask": _asker is not None}).encode(), "application/json")
        if path.startswith("/api/report/"):
            rec = load(path[len("/api/report/"):])
            if rec is None:
                return self._send(404, b'{"error":"Not found or expired"}', "application/json")
            return self._send(200, json.dumps(rec).encode("utf-8"), "application/json")
        self._send(200, b"Ink local report server is running.", "text/plain")


def _same_origin(handler: BaseHTTPRequestHandler) -> bool:
    """Reject requests triggered by other websites: the Origin, when sent, must be this server itself."""
    origin = handler.headers.get("Origin")
    if not origin:
        return True
    return re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d+)?", origin) is not None


def _handle_ask(self: BaseHTTPRequestHandler) -> None:
    def reply(status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    host = (self.headers.get("Host") or "").split(":")[0]
    if host not in ("localhost", "127.0.0.1", "[::1]") or not _same_origin(self):
        return reply(403, {"error": "forbidden"})
    if _asker is None:
        return reply(404, {"error": "Asking is not available"})
    try:
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > MAX_ASK_BYTES:
            return reply(413, {"error": "Question too large"})
        body = json.loads(self.rfile.read(size).decode("utf-8"))
        question = str(body.get("question", "")).strip()[:500]
        record = load(str(body.get("id", "")))
    except (ValueError, OSError):
        return reply(400, {"error": "Bad request"})
    if not question:
        return reply(400, {"error": "Empty question"})
    if record is None:
        return reply(404, {"error": "Report not found or expired"})
    if not _ask_slots.acquire(blocking=False):
        return reply(429, {"error": "Busy, try again in a moment"})
    try:
        reply(200, {"answer": _asker(record, question)})
    except Exception as e:  # Gemini busy, bad key, ...
        reply(502, {"error": f"Could not answer right now ({str(e)[:100]})"})
    finally:
        _ask_slots.release()


def ensure_started() -> int:
    """Start the server once (in a background thread) and return its port."""
    global _server
    with _lock:
        if _server is not None:
            return _server.server_address[1]
        REPORTS.mkdir(parents=True, exist_ok=True)
        purge_expired()
        for port in range(FIRST_PORT, FIRST_PORT + 10):
            try:
                srv = _Server(("127.0.0.1", port), _Handler)
            except OSError:
                continue
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _server = srv
            return port
        raise RuntimeError(f"No free port in {FIRST_PORT}-{FIRST_PORT + 9} for the local report server")


def store(record: dict) -> str:
    """Save a report and return its local URL."""
    port = ensure_started()
    report_id = secrets.token_hex(12)
    now = datetime.now(timezone.utc)
    record = {**record, "id": report_id, "created_at": now.isoformat(),
              "expires_at": (now + timedelta(seconds=TTL_SECONDS)).isoformat()}
    REPORTS.mkdir(parents=True, exist_ok=True)
    _path(report_id).write_text(json.dumps(record), encoding="utf-8")
    return f"http://localhost:{port}/r/{report_id}"
