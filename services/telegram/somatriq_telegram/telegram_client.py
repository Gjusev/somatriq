"""Minimal Telegram Bot API client over stdlib urllib (spec §101, §27).

Deliberately NOT python-telegram-bot: the compose image installs the locked
workspace (``uv sync --frozen`` in services/api/Dockerfile), so a new
third-party dependency would force lockfile churn for a service whose entire
Bot API surface is getUpdates + sendMessage. A zero-dependency urllib
long-poll is smaller than the dependency it would replace, and the HTTP
layer is mockable through the injectable opener (tests never hit the
network; 127.0.0.1-only development, outbound-only polling per spec §27).

SECURITY (spec §45, §221): the bot token is a secret. The Bot API carries it
in the URL path by design, so this module NEVER logs URLs, requests or
errors verbatim — TelegramError carries only the method name, HTTP status
and Telegram's error description. Chat ids are treated the same way by the
callers (never logged at info level).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 35.0  # > long-poll wait, so reads are not cut short
POLL_TIMEOUT_SECONDS = 25  # getUpdates long-poll window


class TelegramError(RuntimeError):
    """Bot API failure — description only; never carries the token or URL."""

    def __init__(self, method: str, http_status: int, description: str) -> None:
        super().__init__(
            f"telegram {method} failed: HTTP {http_status}: {description[:200]}"
        )
        self.method = method
        self.http_status = http_status
        self.description = description[:200]


class TelegramClient:
    """Blocking Bot API client; callers wrap it in asyncio.to_thread."""

    def __init__(
        self,
        token: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._url_base = f"{api_base.rstrip('/')}/bot{token}"
        self._timeout = timeout
        self._opener = opener or urllib.request.build_opener()

    def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._url_base}/{method}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramError(method, exc.code, exc.read().decode("utf-8", "replace")) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(method, 0, str(exc.reason)) from exc
        except (ValueError, OSError) as exc:
            raise TelegramError(method, 0, repr(exc)) from exc
        if not isinstance(body, dict) or not body.get("ok"):
            description = (
                str(body.get("description", "unexpected response"))
                if isinstance(body, dict)
                else "non-object response"
            )
            raise TelegramError(method, 200, description)
        return body

    def get_updates(
        self, offset: int, *, timeout: int = POLL_TIMEOUT_SECONDS
    ) -> list[dict[str, Any]]:
        """Long-poll updates; returns the raw update objects, oldest first."""
        body = self._request(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
        )
        result = body.get("result", [])
        return list(result) if isinstance(result, list) else []

    def send_message(self, chat_id: int | str, text: str) -> None:
        """Outbound message (spec §27: outbound-only is the chosen mode)."""
        self._request("sendMessage", {"chat_id": chat_id, "text": text})
