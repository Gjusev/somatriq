"""Scheduler service (M7): /health + the daily morning-brief trigger
(spec §101-102, §135). The scheduler creates jobs (outbox rows); it never
performs heavy work itself (spec §135).

Emits at MORNING_BRIEF_TIME in USER_TIMEZONE; the [brief_time, 12:00)
window doubles as the backfill rule (emit on startup within the same
morning when the configured moment was missed — grill decision: emit with
marker beats skipping). Also enqueues sync warnings when the newest
observation is older than SYNC_STALE_HOURS at brief time (spec §101).
"""

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dt_time
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from somatriq_db.engine import get_session_factory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .jobs import morning_tick
from .schedule import parse_brief_time

PORT = int(os.environ.get("SCHEDULER_HEALTH_PORT", "8201"))

log = logging.getLogger("somatriq_scheduler")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok","service":"somatriq_scheduler"}'
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


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    brief_time: dt_time,
    stale_hours: int,
    ntfy_topic: str = "",
    poll_seconds: float = 30.0,
    now: Callable[[], datetime] = _utc_now,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll clock; each pass is one idempotent morning_tick."""
    while stop is None or not stop.is_set():
        try:
            async with session_factory() as session:
                enqueued = await morning_tick(
                    session,
                    tz=tz,
                    brief_time=brief_time,
                    stale_hours=stale_hours,
                    now=now(),
                    ntfy_topic=ntfy_topic,
                )
            if enqueued:
                log.info("morning tick enqueued %d outbox row(s)", enqueued)
        except Exception:  # noqa: BLE001 - a failed pass must never kill the clock
            log.exception("scheduler pass failed")
        await asyncio.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tz = ZoneInfo(os.environ.get("USER_TIMEZONE", "UTC"))
    brief_time = parse_brief_time(os.environ.get("MORNING_BRIEF_TIME", "07:00"))
    stale_hours = int(os.environ.get("SYNC_STALE_HOURS", "6"))
    poll_seconds = float(os.environ.get("SCHEDULER_POLL_SECONDS", "30"))
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    log.info(
        "scheduler started: brief at %s %s, sync stale after %dh",
        brief_time.isoformat(),
        tz.key,
        stale_hours,
    )
    try:
        asyncio.run(
            scheduler_loop(
                get_session_factory(),
                tz=tz,
                brief_time=brief_time,
                stale_hours=stale_hours,
                ntfy_topic=ntfy_topic,
                poll_seconds=poll_seconds,
            )
        )
    except KeyboardInterrupt:
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()
