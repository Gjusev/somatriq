"""Notifications service (M7): /health + the outbox drain loop (spec §105).

Delivers pending notifications.outbox rows: telegram via the same Bot API
client the bot service uses, ntfy via POST to the container-internal server
(NTFY_INTERNAL_URL, topic = channel target). Retries are capped; failures
land as status=failed with last_error (spec §105 engine contract).
"""

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from somatriq_db.engine import get_session_factory
from somatriq_telegram.telegram_client import TelegramClient

from .sender import (
    DEFAULT_NTFY_URL,
    NtfySender,
    drain_once,
    telegram_sender,
)

PORT = int(os.environ.get("NOTIFICATIONS_HEALTH_PORT", "8204"))

log = logging.getLogger("somatriq_notifications")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_notifications"}'
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


def build_senders() -> dict[str, object]:
    """Channel kind -> sync sender. Telegram requires TELEGRAM_BOT_TOKEN;
    without it telegram rows fail their first drain with a clear error (the
    service itself stays healthy — spec §105 engine keeps running)."""
    senders: dict[str, object] = {
        "ntfy": NtfySender(os.environ.get("NTFY_INTERNAL_URL", DEFAULT_NTFY_URL)),
    }
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        senders["telegram"] = telegram_sender(TelegramClient(token))
    return senders


async def drain_loop(
    *,
    poll_seconds: float = 5.0,
    stop: asyncio.Event | None = None,
) -> None:
    session_factory = get_session_factory()
    senders = build_senders()
    log.info(
        "drain loop started (senders: %s)", ", ".join(sorted(senders)) or "none"
    )
    while stop is None or not stop.is_set():
        try:
            async with session_factory() as session:
                report = await drain_once(session, senders)
            if report.sent or report.failed:
                log.info(
                    "drain: %d sent, %d deferred, %d failed",
                    report.sent,
                    report.deferred,
                    report.failed,
                )
        except Exception:  # noqa: BLE001 - the drain must survive any pass
            log.exception("drain pass failed")
        await asyncio.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(drain_loop(
            poll_seconds=float(os.environ.get("NOTIFICATIONS_POLL_SECONDS", "5"))
        ))
    except KeyboardInterrupt:
        log.info("notifications stopped")


if __name__ == "__main__":
    main()
