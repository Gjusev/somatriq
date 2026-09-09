"""Journal quick-log wire contracts (Block 2; grill P7/P11; spec §103).

The journal is a laboratory input, not a diagnostic form: the six binary
behaviors (CONTEXT.md "Behavior") plus free journal/note text. Quantity
exists ONLY when literally stated by the user (caffeine/alcohol, the
spec §103 rule the Telegram parser already follows) — the server never
invents, estimates, or infers one. System-written kinds (training,
experiment_checkin) are rejected at this edge (P11): the owner may delete
their own words, not the system's audit trail.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BEHAVIOR_KINDS: tuple[str, ...] = (
    "caffeine",
    "alcohol",
    "medication",
    "stress",
    "meal",
    "travel",
)
USER_WRITABLE_KINDS: tuple[str, ...] = (*BEHAVIOR_KINDS, "journal", "note")
SYSTEM_KINDS: tuple[str, ...] = ("training", "experiment_checkin")

# Kinds whose structured payload may carry a literal quantity.
QUANTITY_KINDS: tuple[str, ...] = ("caffeine", "alcohol")

Kind = Literal[
    "journal",
    "caffeine",
    "alcohol",
    "medication",
    "stress",
    "meal",
    "travel",
    "note",
]


class JournalEventCreate(BaseModel):
    """One quick-log event. ``ts`` defaults to now (tz-aware); ``text`` is
    kept verbatim for provenance; ``client_event_id`` makes mobile retries
    idempotent (a retried event is the SAME event)."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    ts: datetime | None = None
    text: str | None = Field(default=None, max_length=4000)
    structured: dict[str, object] | None = None
    client_event_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate_structured(self) -> "JournalEventCreate":
        if self.structured is None:
            return self
        if self.kind not in QUANTITY_KINDS:
            raise ValueError(
                f"kind {self.kind!r} is binary in v1 — no structured payload"
            )
        keys = set(self.structured)
        if not keys <= {"quantity", "estimated"}:
            raise ValueError(
                "structured payload accepts only 'quantity' and 'estimated'"
            )
        quantity = self.structured.get("quantity")
        if quantity is not None:
            if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
                raise ValueError("quantity must be a number")
            if quantity <= 0:
                raise ValueError("quantity must be positive")
        return self


class JournalEventOut(BaseModel):
    id: uuid.UUID
    kind: str
    source: str
    ts: datetime
    text: str | None = None
    structured: dict[str, object] | None = None
    client_event_id: uuid.UUID | None = None
    created_at: datetime


class JournalDayResponse(BaseModel):
    date: str
    events: list[JournalEventOut] = Field(default_factory=list)
