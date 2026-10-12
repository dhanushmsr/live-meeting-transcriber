"""Local report server: serves the online report page from this computer.

Reports live in ~/.inkwake/reports/<id>.json and are deleted 72 hours after they were created.
The page is http://localhost:<port>/r/<id> and only works while Ink is running. It is bound to
127.0.0.1, so nobody else on the network can open it. The same viewer.html is used by the
Cloudflare Worker in cloud/, so a report looks and exports identically in both places.
"""

import json
import re
import secrets
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

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


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
        if path.startswith("/api/report/"):
            rec = load(path[len("/api/report/"):])
            if rec is None:
                return self._send(404, b'{"error":"Not found or expired"}', "application/json")
            return self._send(200, json.dumps(rec).encode("utf-8"), "application/json")
        self._send(200, b"Ink local report server is running.", "text/plain")


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
                srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
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
