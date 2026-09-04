"""TelegramClient against a mocked HTTP layer — no network, no real token.

The fake replaces the opener's HTTP handler (tests run 127.0.0.1-only);
responses are scripted per test. Security property under test: errors carry
method/status/description only — never the token-bearing URL.
"""

import email.message
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, cast

from somatriq_telegram.telegram_client import TelegramClient, TelegramError

BASE = "http://telegram.test"


class _MockResponse(io.BytesIO):
    """BytesIO dressed as a urllib response (code/msg/status are required by
    HTTPErrorProcessor)."""

    code = 200
    status = 200
    msg = "OK"

    def info(self) -> Any:
        return email.message.Message()

    def geturl(self) -> str:
        return "mock://telegram.test"


class _FakeHTTPHandler(urllib.request.HTTPHandler):
    """Scripted responder: (status, body) per request; records every request."""

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


def _client(respond: Callable[[urllib.request.Request], tuple[int, bytes]]) -> tuple[
    TelegramClient, _FakeHTTPHandler
]:
    handler = _FakeHTTPHandler(respond)
    opener = urllib.request.build_opener(handler, urllib.request.ProxyHandler({}))
    return TelegramClient("secret-token", api_base=BASE, opener=opener), handler


def _ok(result: Any) -> tuple[int, bytes]:
    return 200, json.dumps({"ok": True, "result": result}).encode()


def test_send_message_posts_json_body() -> None:
    client, handler = _client(lambda req: _ok({"message_id": 1}))
    client.send_message(12345, "hello")
    assert len(handler.requests) == 1
    request = handler.requests[0]
    assert request.full_url == f"{BASE}/botsecret-token/sendMessage"
    payload = json.loads(cast("bytes", request.data).decode())
    assert payload == {"chat_id": 12345, "text": "hello"}


def test_get_updates_returns_updates_in_order() -> None:
    updates = [{"update_id": 7}, {"update_id": 8}]
    client, handler = _client(lambda req: _ok(updates))
    assert client.get_updates(offset=7) == updates
    payload = json.loads(cast("bytes", handler.requests[0].data).decode())
    assert payload["offset"] == 7
    assert payload["allowed_updates"] == ["message"]


def test_http_error_raises_telegram_error_without_token() -> None:
    client, _ = _client(lambda req: (401, b'{"ok": false}'))
    try:
        client.send_message(1, "x")
    except TelegramError as exc:
        assert exc.http_status == 401
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("expected TelegramError")


def test_ok_false_raises_with_description() -> None:
    client, _ = _client(
        lambda req: (200, b'{"ok": false, "description": "chat not found"}')
    )
    try:
        client.send_message(1, "x")
    except TelegramError as exc:
        assert "chat not found" in str(exc)
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("expected TelegramError")


def test_network_error_raises_telegram_error() -> None:
    def _unreachable(_: urllib.request.Request) -> tuple[int, bytes]:
        raise urllib.error.URLError("connection refused")

    client, _ = _client(_unreachable)
    try:
        client.get_updates(0)
    except TelegramError as exc:
        assert exc.http_status == 0
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("expected TelegramError")
