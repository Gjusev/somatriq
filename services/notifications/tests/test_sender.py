"""Outbox drain with a mocked HTTP layer (spec §105): delivery, capped
retries, failure recording. Senders are fakes/opener-injected — never the
network (127.0.0.1-only development).
"""

import email.message
import io
import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from somatriq_db.testing import requires_db
from somatriq_notifications.sender import (
    NtfyError,
    NtfySender,
    drain_once,
    payload_text,
    telegram_sender,
)
from somatriq_telegram.telegram_client import TelegramClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC = ZoneInfo("UTC")


# ── fakes ─────────────────────────────────────────────────────────────────


class _RecordingSender:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, target: str, message: str) -> None:
        self.calls.append((target, message))
        if self.fail:
            raise NtfyError("scripted failure")


class _MockResponse(io.BytesIO):
    code = 200
    status = 200
    msg = "OK"

    def info(self) -> Any:
        return email.message.Message()

    def geturl(self) -> str:
        return "mock://ntfy.test"


class _FakeHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, respond: Callable[[urllib.request.Request], tuple[int, bytes]]):
        super().__init__()
        self.respond = respond
        self.requests: list[urllib.request.Request] = []

    def http_open(self, req: urllib.request.Request) -> Any:
        self.requests.append(req)
        status, body = self.respond(req)
        if status >= 400:
            raise urllib.error.HTTPError(
                req.full_url, status, "error", email.message.Message(), io.BytesIO(body)
            )
        return _MockResponse(body)


def _opener(
    respond: Callable[[urllib.request.Request], tuple[int, bytes]],
) -> tuple[urllib.request.OpenerDirector, _FakeHTTPHandler]:
    handler = _FakeHTTPHandler(respond)
    return urllib.request.build_opener(handler, urllib.request.ProxyHandler({})), handler


# ── ntfy sender ───────────────────────────────────────────────────────────


def test_ntfy_posts_to_topic_with_body() -> None:
    opener, handler = _opener(lambda req: (200, b'{"id":"x"}'))
    sender = NtfySender("http://ntfy.test", opener=opener)
    sender.send("somatriq-alerts", "Sync warning: too old")
    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request.full_url == "http://ntfy.test/somatriq-alerts"
    assert cast("bytes", request.data) == b"Sync warning: too old"
    assert request.get_method() == "POST"


def test_ntfy_http_error_becomes_ntfy_error() -> None:
    opener, _ = _opener(lambda req: (503, b""))
    sender = NtfySender("http://ntfy.test", opener=opener)
    with pytest.raises(NtfyError, match="HTTP 503"):
        sender.send("topic", "x")


def test_ntfy_unreachable_becomes_ntfy_error() -> None:
    def _unreachable(_: urllib.request.Request) -> tuple[int, bytes]:
        raise urllib.error.URLError("refused")

    opener, _ = _opener(_unreachable)
    sender = NtfySender("http://ntfy.test", opener=opener)
    with pytest.raises(NtfyError, match="unreachable"):
        sender.send("topic", "x")


# ── telegram adapter uses the same client ────────────────────────────────


def test_telegram_sender_delegates_to_telegram_client() -> None:
    opener, handler = _opener(lambda req: (200, json.dumps({"ok": True, "result": {}}).encode()))
    client = TelegramClient("tok", api_base="http://telegram.test", opener=opener)
    send = telegram_sender(client)
    send("12345", "hello")
    assert len(handler.requests) == 1
    assert handler.requests[0].full_url == "http://telegram.test/bottok/sendMessage"
    assert json.loads(cast("bytes", handler.requests[0].data)) == {
        "chat_id": "12345",
        "text": "hello",
    }


# ── payload_text ──────────────────────────────────────────────────────────


def test_payload_text_variants() -> None:
    assert payload_text({"text": "a"}) == "a"
    assert payload_text('{"text": "b"}') == "b"
    assert payload_text('{"no_text": 1}') == ""
    assert payload_text("not json") == ""
    assert payload_text('"just a string"') == ""


# ── drain_once against the DB ─────────────────────────────────────────────


async def _seed_channel(db: AsyncSession, kind: str, target: str) -> uuid.UUID:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    channel_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO notifications.channels (id, user_id, kind, target) "
            "VALUES (:id, :user_id, :kind, :target)"
        ),
        {"id": channel_id, "user_id": user_id, "kind": kind, "target": target},
    )
    await db.commit()
    return channel_id


async def _seed_outbox(
    db: AsyncSession,
    channel_id: uuid.UUID,
    kind: str = "test",
    payload: dict[str, Any] | None = None,
    attempts: int = 0,
) -> uuid.UUID:
    outbox_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO notifications.outbox (id, channel_id, kind, payload, attempts) "
            "VALUES (:id, :channel_id, :kind, CAST(:payload AS jsonb), :attempts)"
        ),
        {
            "id": outbox_id,
            "channel_id": channel_id,
            "kind": kind,
            "payload": json.dumps(payload if payload is not None else {"text": "hi"}),
            "attempts": attempts,
        },
    )
    await db.commit()
    return outbox_id


async def _row(db: AsyncSession, outbox_id: uuid.UUID) -> tuple[str, int, str | None]:
    result = await db.execute(
        text("SELECT status, attempts, last_error FROM notifications.outbox WHERE id = :id"),
        {"id": outbox_id},
    )
    row = result.one()
    return str(row[0]), int(row[1]), None if row[2] is None else str(row[2])


@requires_db
async def test_drain_sends_and_marks_sent(db: AsyncSession) -> None:
    channel = await _seed_channel(db, "telegram", "111")
    outbox_id = await _seed_outbox(db, channel, payload={"text": "Good morning"})
    sender = _RecordingSender()
    report = await drain_once(db, {"telegram": sender})
    assert report.sent == 1 and report.deferred == 0 and report.failed == 0
    assert sender.calls == [("111", "Good morning")]
    status, attempts, error = await _row(db, outbox_id)
    assert status == "sent"
    assert attempts == 0
    assert error is None


@requires_db
async def test_drain_failure_increments_and_defers(db: AsyncSession) -> None:
    channel = await _seed_channel(db, "ntfy", "somatriq-alerts")
    outbox_id = await _seed_outbox(db, channel)
    report = await drain_once(db, {"ntfy": _RecordingSender(fail=True)})
    assert report.deferred == 1 and report.sent == 0
    status, attempts, error = await _row(db, outbox_id)
    assert status == "pending"
    assert attempts == 1
    assert error is not None and "scripted failure" in error


@requires_db
async def test_drain_caps_attempts_and_fails(db: AsyncSession) -> None:
    channel = await _seed_channel(db, "ntfy", "somatriq-alerts")
    outbox_id = await _seed_outbox(db, channel, attempts=4)  # one try from the cap
    report = await drain_once(db, {"ntfy": _RecordingSender(fail=True)}, attempt_cap=5)
    assert report.failed == 1 and report.deferred == 0
    status, attempts, error = await _row(db, outbox_id)
    assert status == "failed"
    assert attempts == 5
    assert error is not None


@requires_db
async def test_drain_failed_row_not_redrained(db: AsyncSession) -> None:
    channel = await _seed_channel(db, "ntfy", "somatriq-alerts")
    await _seed_outbox(db, channel, attempts=4)
    await drain_once(db, {"ntfy": _RecordingSender(fail=True)}, attempt_cap=5)
    sender = _RecordingSender()
    report = await drain_once(db, {"ntfy": sender})
    assert report.sent == 0
    assert sender.calls == []  # failed rows stay failed — no zombie sends


@requires_db
async def test_drain_no_sender_configured_defers(db: AsyncSession) -> None:
    """A vocabulary kind with no sender registered (e.g. telegram while the
    token is unset) defers with a clear error instead of crashing the drain."""
    channel = await _seed_channel(db, "ntfy", "somatriq-alerts")
    outbox_id = await _seed_outbox(db, channel)
    report = await drain_once(db, {})
    assert report.deferred == 1
    status, attempts, error = await _row(db, outbox_id)
    assert status == "pending"
    assert error is not None and "no sender configured" in error


@requires_db
async def test_drain_orders_oldest_first(db: AsyncSession) -> None:
    channel = await _seed_channel(db, "telegram", "111")
    first = await _seed_outbox(db, channel, payload={"text": "one"})
    second = await _seed_outbox(db, channel, payload={"text": "two"})
    sender = _RecordingSender()
    await drain_once(db, {"telegram": sender})
    assert [c[1] for c in sender.calls] == ["one", "two"]
    for row_id in (first, second):
        status, _, _ = await _row(db, row_id)
        assert status == "sent"
