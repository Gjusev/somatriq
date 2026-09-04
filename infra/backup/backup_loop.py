"""Backup service stub (ADR 0016).

The real service runs: pg_dump with TimescaleDB pre/post hooks → encryption →
external S3-compatible destination → verification (checksum + periodic restore
drill) → raw-archive sync. This stub keeps the container alive and healthy so
M0 deploys the full topology; real logic lands with the backup milestone.
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("BACKUP_HEALTH_PORT", "8205"))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_backup","mode":"stub"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
