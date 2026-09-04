"""Deterministic quick-log parser for /log (spec §103; ADR 0009).

Never invents quantity (spec §103): a caffeine event carries ``quantity``
ONLY when a number is literally present in the text; otherwise the payload
records ``{"estimated": false}`` — quantity unknown, and we did not guess
one. Everything else is stored as a plain journal note. No LLM: the
training parser (spec §104) is a later slice and may use AI, but /log v1 is
pure string rules whose output is deterministic and editable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CAFFEINE_KEYWORDS: tuple[str, ...] = ("coffee", "cafe", "café", "kaffee")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class ParsedLog:
    """One parsed /log entry: event kind + validated structured payload."""

    kind: str  # "caffeine" | "journal"
    structured: dict[str, Any] | None
    reply: str  # deterministic confirmation shown to the user


def _first_number(text: str) -> float | None:
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    return float(match.group().replace(",", "."))


def _quantity(value: float) -> float | int:
    """Render 2.0 as 2 (the user literally wrote a plain number)."""
    return int(value) if value.is_integer() else value


def parse_quick_log(text: str) -> ParsedLog:
    """Caffeine keywords (+ optional literal number) -> caffeine event;
    everything else -> journal note. Deterministic over the raw text."""
    lowered = text.lower()
    if any(word in lowered for word in CAFFEINE_KEYWORDS):
        number = _first_number(text)
        if number is None:
            return ParsedLog(
                kind="caffeine",
                structured={"estimated": False},
                reply="Logged: caffeine (quantity not stated — not guessed)",
            )
        quantity = _quantity(number)
        return ParsedLog(
            kind="caffeine",
            structured={"quantity": quantity},
            reply=f"Logged: caffeine (quantity {quantity})",
        )
    return ParsedLog(
        kind="journal",
        structured=None,
        reply="Logged: journal note",
    )
