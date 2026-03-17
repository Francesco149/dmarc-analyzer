#!/usr/bin/env python3
"""
dmarc-server — minimal static file server for the DMARC analyzer.

Serves:
  /                       → index.html  (from DMARC_WEB_ROOT)
  /data/reports.json      → reports.json (from DMARC_DATA_DIR)

Listens on 127.0.0.1:DMARC_PORT (default 8741).
Intended to sit behind a reverse proxy — no TLS, no domain logic.

Environment variables (set by systemd unit via the Nix module):
  DMARC_WEB_ROOT   path to the directory containing index.html
  DMARC_DATA_DIR   path to the directory containing reports.json
  DMARC_PORT       port to listen on (default: 8741)
  DMARC_HOST       host to bind to   (default: 127.0.0.1)
"""

import http.server
import os
import sys
from pathlib import Path

WEB_ROOT = Path(os.environ.get("DMARC_WEB_ROOT", "."))
DATA_DIR = Path(os.environ.get("DMARC_DATA_DIR", "."))
PORT     = int(os.environ.get("DMARC_PORT", "8741"))
HOST     = os.environ.get("DMARC_HOST", "127.0.0.1")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".ico":  "image/x-icon",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quiet logging — systemd/journald captures stderr anyway
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def send_file(self, path: Path, content_type: str, extra_headers=None):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, f"Not found: {path.name}")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Don't cache anything — reports update frequently
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path == "/" or path == "/index.html":
            self.send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")

        elif path == "/data/reports.json":
            self.send_file(
                DATA_DIR / "reports.json",
                "application/json",
                # Allow the reverse proxy / browser to fetch this from a
                # different origin if needed (e.g. during development)
                {"Access-Control-Allow-Origin": "*"},
            )

        else:
            self.send_error(404, "Not found")

    def do_HEAD(self):
        # Some proxies send HEAD for health checks
        self.do_GET()


if __name__ == "__main__":
    server = http.server.HTTPServer((HOST, PORT), Handler)
    print(f"DMARC analyzer listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
