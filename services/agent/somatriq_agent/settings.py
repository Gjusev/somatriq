"""Agent process settings from environment (spec §209).

Stdlib-only, mirroring the api's settings module: typed dataclass behind an
lru_cache, reset per test. The AI privacy level is validated here so a typo
in AI_PRIVACY_LEVEL fails loudly at startup instead of silently degrading
the privacy chokepoint (ADR 0009).
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from .privacy import DEFAULT_PRIVACY_LEVEL, PRIVACY_LEVELS, PrivacyLevel

DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 60.0
DEFAULT_USER_TIMEZONE = "UTC"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """Everything the coach engine reads from the environment."""

    privacy_level: PrivacyLevel
    allow_external: bool
    ollama_base_url: str | None
    ollama_model: str
    ollama_timeout_seconds: float
    external_base_url: str | None
    external_api_key: str | None
    user_timezone: str


def _privacy_level(raw: str | None) -> PrivacyLevel:
    level = (raw or DEFAULT_PRIVACY_LEVEL).strip().lower()
    if level not in PRIVACY_LEVELS:
        msg = (
            f"AI_PRIVACY_LEVEL must be one of {sorted(PRIVACY_LEVELS)}; got {raw!r} (ADR 0009)"
        )
        raise RuntimeError(msg)
    return level  # type: ignore[return-value]  # validated against PRIVACY_LEVELS above


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        privacy_level=_privacy_level(os.environ.get("AI_PRIVACY_LEVEL")),
        allow_external=_flag(os.environ.get("AI_ALLOW_EXTERNAL")),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL") or None,
        ollama_model=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        ollama_timeout_seconds=float(
            os.environ.get("OLLAMA_TIMEOUT_SECONDS", DEFAULT_OLLAMA_TIMEOUT_SECONDS)
        ),
        external_base_url=os.environ.get("OPENAI_COMPAT_BASE_URL") or None,
        external_api_key=os.environ.get("OPENAI_COMPAT_API_KEY") or None,
        user_timezone=os.environ.get("USER_TIMEZONE", DEFAULT_USER_TIMEZONE),
    )
