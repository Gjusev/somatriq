"""MCP service stub — health endpoint + placeholder (ADR 0010, milestone M9)."""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MCP_HEALTH_PORT", "8100"))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_mcp"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: object) -> None:  # silence default stderr noise
        return


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # FastMCP server over HTTPS at /mcp with OAuth 2.1 lands at M9 (ADR 0010).
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
