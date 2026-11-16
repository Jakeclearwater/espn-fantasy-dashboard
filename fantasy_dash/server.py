"""A small local web server for the dashboard, so its Refresh button can rerun the export.

It listens on 127.0.0.1 only. Refreshing needs a token that is only ever
sent inside the served page, in a custom header, so another website open in
the browser can't trigger a refresh: it can't read the token, and the
custom header makes the browser ask permission first, which this server
never gives.
"""

import json
import secrets
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_HEADER = "X-Refresh-Token"


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, render, refresh, host="127.0.0.1", port=8765):
        """
        render(token) -> the dashboard HTML for the latest data, with the token embedded.
        refresh() -> reruns the export; returns the new generated_at or raises.
        """
        self.render = render
        self.refresh = refresh
        self.token = secrets.token_urlsafe(24)
        self.refresh_lock = threading.Lock()
        super().__init__((host, port), DashboardHandler)

    def server_bind(self):
        # HTTPServer.server_bind looks up the machine's full DNS name, which can
        # take several seconds on some networks and isn't needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def url(self):
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def allowed_hosts(self):
        port = self.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "FantasyDashboard"

    def _send(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json")

    def _host_ok(self):
        # Rejecting other Host names stops DNS rebinding tricks.
        return self.headers.get("Host", "") in self.server.allowed_hosts()

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "Forbidden", "text/plain; charset=utf-8")
        if self.path not in ("/", "/dashboard.html"):
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        self._send(200, self.server.render(self.server.token), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok() or self.path != "/refresh":
            return self._json(403, {"ok": False, "error": "Not allowed."})
        token = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(token, self.server.token):
            return self._json(403, {"ok": False, "error": "This page is out of date. Reload it and try again."})
        if not self.server.refresh_lock.acquire(blocking=False):
            return self._json(409, {"ok": False, "error": "A refresh is already running."})
        try:
            generated_at = self.server.refresh()
        except Exception as exc:  # report it in the page rather than crash the server
            return self._json(500, {"ok": False, "error": str(exc) or type(exc).__name__})
        finally:
            self.server.refresh_lock.release()
        self._json(200, {"ok": True, "generated_at": generated_at})

    def log_message(self, format, *args):
        sys.stderr.write(f"[server] {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")
