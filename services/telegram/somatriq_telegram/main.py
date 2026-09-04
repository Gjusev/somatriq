"""Telegram bot service (M7): /health + outbound long-poll bot (spec §101, §27).

The health endpoint stays up no matter what — the compose healthcheck
depends on it. When TELEGRAM_BOT_TOKEN is unset the bot logs once and idles
(the service runs without secrets; spec §45: tokens only ever come from the
environment and are never logged).
"""

import asyncio
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from somatriq_telegram.bot import run_bot
from somatriq_telegram.telegram_client import TelegramClient

PORT = int(os.environ.get("TELEGRAM_HEALTH_PORT", "8203"))

log = logging.getLogger("somatriq_telegram")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_telegram"}'
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
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        # Deliberate single line, no secret material — compose keeps running.
        log.warning("TELEGRAM_BOT_TOKEN not set; bot idling (health endpoint only)")
        while True:
            time.sleep(3600)

    tz = ZoneInfo(os.environ.get("USER_TIMEZONE", "UTC"))
    from somatriq_db.engine import get_session_factory  # after env is read

    try:
        asyncio.run(run_bot(TelegramClient(token), get_session_factory(), tz=tz))
    except KeyboardInterrupt:
        log.info("bot stopped")


if __name__ == "__main__":
    main()
