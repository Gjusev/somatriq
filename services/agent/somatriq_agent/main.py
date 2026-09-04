"""Agent service — health endpoint only (M8).

The coach engine (somatriq_agent.coach) is an on-demand library consumed by
the api's /api/v1/coach/ask; this process runs no background work (ADR 0009:
deterministic tools first, nothing to poll).
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("AGENT_HEALTH_PORT", "8202"))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_agent"}'
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
    # AI coach orchestrator lands M8 (ADR 0009: deterministic tools first).
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
