"""Outbox drain: telegram + ntfy delivery with capped retries (spec §105).

The notifications service owns DELIVERY only — the scheduler enqueues, this
module sends. Telegram sends go through the same TelegramClient the bot
uses (one Bot API implementation, not two divergent HTTP paths). ntfy POSTs
target the container-internal ntfy (NTFY_INTERNAL_URL, topic = channel
target — spec §27 networking: everything stays on the private network).

Retry contract: a failed send increments attempts and stays pending until
the cap; at the cap the row becomes failed with last_error recorded. No
notification is ever silently dropped (spec §158 spirit applied to the
engine itself).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("somatriq_notifications")

DEFAULT_ATTEMPT_CAP = 5
DEFAULT_BATCH = 20
DEFAULT_NTFY_URL = "http://somatriq_ntfy:80"


class NtfyError(RuntimeError):
    """ntfy delivery failure — carries the topic-free URL and status only."""


class NtfySender:
    """POST message bodies to the internal ntfy server (topic in the path)."""

    def __init__(
        self,
        base_url: str = DEFAULT_NTFY_URL,
        *,
        timeout: float = 10.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener()

    def send(self, topic: str, message: str) -> None:
        url = f"{self._base_url}/{quote(topic, safe='')}"
        request = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise NtfyError(f"ntfy POST returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise NtfyError(f"ntfy POST unreachable: {exc.reason}") from exc


@dataclass(frozen=True)
class DrainReport:
    sent: int = 0
    deferred: int = 0
    failed: int = 0


async def drain_once(
    session: AsyncSession,
    senders: dict[str, Any],
    *,
    attempt_cap: int = DEFAULT_ATTEMPT_CAP,
    batch: int = DEFAULT_BATCH,
) -> DrainReport:
    """Deliver up to ``batch`` pending outbox rows.

    ``senders`` maps channel kind -> sync callable(target, text); the calls
    run in worker threads so a slow HTTP send never blocks the loop.
    """
    result = await session.execute(
        text(
            "SELECT o.id, o.kind, o.payload, o.attempts, c.kind AS channel_kind, c.target "
            "FROM notifications.outbox o "
            "JOIN notifications.channels c ON c.id = o.channel_id "
            "WHERE o.status = 'pending' "
            "ORDER BY o.created_at "
            "LIMIT :batch"
        ),
        {"batch": batch},
    )
    rows = result.all()

    report = DrainReport()
    for row_id, _kind, payload, attempts, channel_kind, target in rows:
        text_body = payload_text(payload)
        sender = senders.get(str(channel_kind))
        try:
            if sender is None:
                raise NtfyError(f"no sender configured for channel kind {channel_kind}")
            await asyncio.to_thread(sender, target, text_body)
        except Exception as exc:  # noqa: BLE001 - any send error becomes a retry/failed
            new_attempts = int(attempts) + 1
            if new_attempts >= attempt_cap:
                await session.execute(
                    text(
                        "UPDATE notifications.outbox SET status = 'failed', "
                        "attempts = :attempts, last_error = :error WHERE id = :id"
                    ),
                    {"attempts": new_attempts, "error": _error_text(exc), "id": row_id},
                )
                report = replace(report, failed=report.failed + 1)
                log.error("outbox row %s failed permanently: %s", row_id, _error_text(exc))
            else:
                await session.execute(
                    text(
                        "UPDATE notifications.outbox SET attempts = :attempts, "
                        "last_error = :error WHERE id = :id"
                    ),
                    {"attempts": new_attempts, "error": _error_text(exc), "id": row_id},
                )
                report = replace(report, deferred=report.deferred + 1)
        else:
            await session.execute(
                text(
                    "UPDATE notifications.outbox SET status = 'sent', sent_at = now() "
                    "WHERE id = :id"
                ),
                {"id": row_id},
            )
            report = replace(report, sent=report.sent + 1)
    await session.commit()
    return report


def _error_text(exc: BaseException) -> str:
    """Error summary for last_error — never includes secrets or chat ids."""
    return f"{type(exc).__name__}: {exc}"[:500]


def telegram_sender(client: Any) -> Any:
    """Adapter: outbox (target, text) -> TelegramClient.send_message."""

    def _send(target: str, message: str) -> None:
        client.send_message(target, message)

    return _send


def payload_text(payload: str | dict[str, Any]) -> str:
    """Outbox payload jsonb -> message text ("" when absent; never crashes)."""
    if isinstance(payload, dict):
        return str(payload.get("text", ""))
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return ""
    return str(parsed.get("text", "")) if isinstance(parsed, dict) else ""
