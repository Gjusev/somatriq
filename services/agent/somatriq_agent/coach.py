"""The coach engine (spec §90): one orchestrator plus good tools.

``answer(question, user_id)`` is deliberately boring and deterministic
up to the single LLM call (ADR 0009):

1. keyword routing picks coach tools (spec §94) — a fixed, inspectable
   table, no model-driven tool selection in v1;
2. the tools run against the DB through somatriq_agent_tools (the ONLY
   facts an LLM ever sees);
3. results pass the privacy chokepoint (:func:`apply_privacy`) at the
   effective level — requested level clamped to the provider's ceiling
   (external providers never see above ``aggregates``);
4. the provider completes; a tool failure degrades that tool's result to
   an explicit error envelope instead of failing the answer (spec §158).

The prompt handed to every provider is a JSON bundle
``{"question": …, "tool_results": …}`` — deterministic providers render it
through templates, network providers embed it verbatim under a versioned
system prompt (§144).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Final
from zoneinfo import ZoneInfo

from somatriq_agent_tools import (
    DEFAULT_BASELINE_DAYS,
    DEFAULT_QUALITY_DAYS,
    DEFAULT_TREND_DAYS,
    TOOLS,
    ToolSpec,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .privacy import DEFAULT_PRIVACY_LEVEL, PrivacyLevel, apply_privacy, clamp_level
from .providers import Provider, build_provider
from .settings import get_settings

NO_TOOL_ANSWER: Final[str] = (
    "I have no data for that. I can answer from your deterministic health tools: "
    "today's recovery, baselines, trends, journal and data quality — try asking "
    "about one of those."
)

DEFAULT_METRIC: Final[str] = "resting_hr"

# Question keyword → metric argument for get_baselines/get_trends. The
# metrics are the agent_tools catalog names (our computed features win over
# vendor observations for resting_hr; HRV's daily value is the vendor
# avg_hrv observation).
METRIC_KEYWORDS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("hrv", "heart rate variability"), "avg_hrv"),
    (("resting heart", "resting hr", "rhr"), "resting_hr"),
    (("sleep",), "total_sleep_min"),
    (("strain",), "strain"),
    (("spo2", "oxygen"), "spo2_pct"),
    (("steps",), "steps"),
)

# Question keyword → tool. All listed keywords are lowercase substrings.
# get_today is the general "current status" catch-all: when a specific tool
# (baselines/trends/journal/quality) already matched, today only joins on an
# EXPLICIT-today word — "what's my hrv baseline" must not drag the whole
# day along, but "how am I today and what did I journal" gets both.
TODAY_EXPLICIT_KEYWORDS: Final[tuple[str, ...]] = (
    "today",
    "this morning",
    "how am i",
    "readiness",
    "status",
    "recover",
)

TOOL_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "get_baselines",
        ("baseline", "baselines", "usual", "typically", "normally", "average"),
    ),
    (
        "get_trends",
        (
            "trend",
            "trends",
            "trending",
            "over time",
            "changing",
            "improving",
            "declining",
            "history",
        ),
    ),
    (
        "get_journal",
        ("journal", "note", "notes", "diary", "log", "logged", "caffeine", "coffee"),
    ),
    (
        "get_data_quality",
        ("quality", "coverage", "missing data", "missing", "gap", "gaps", "reliable"),
    ),
    (
        "get_today",
        (
            "today",
            "recovery",
            "recover",
            "sleep",
            "hrv",
            "rhr",
            "resting heart",
            "readiness",
            "status",
            "how am i",
            "this morning",
            "feel",
        ),
    ),
)


@dataclass(frozen=True)
class ToolCall:
    """One selected tool plus its specific arguments (session/tz/now excluded)."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoachAnswer:
    """The coach response: the answer plus full provenance (spec §99 spirit)."""

    answer: str
    provider: str
    privacy_level: PrivacyLevel
    tools_used: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def resolve_metric(question: str) -> str:
    """First metric whose keywords appear in the question; resting_hr default."""
    lowered = question.lower()
    for keywords, metric in METRIC_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return metric
    return DEFAULT_METRIC


def select_tools(question: str) -> list[ToolCall]:
    """Deterministic keyword routing — inspectable, testable, no LLM (v1)."""
    lowered = question.lower()
    metric = resolve_metric(lowered)
    calls: list[ToolCall] = []
    specific_matched = False
    for name, keywords in TOOL_KEYWORDS:
        if name == "get_today":
            continue
        if not any(keyword in lowered for keyword in keywords):
            continue
        specific_matched = True
        if name == "get_baselines":
            calls.append(ToolCall(name, {"metric": metric, "days": DEFAULT_BASELINE_DAYS}))
        elif name == "get_trends":
            calls.append(ToolCall(name, {"metric": metric, "days": DEFAULT_TREND_DAYS}))
        elif name == "get_journal":
            calls.append(ToolCall(name))  # engine injects user_id (auth-scoped)
        elif name == "get_data_quality":
            calls.append(ToolCall(name, {"days": DEFAULT_QUALITY_DAYS}))

    today_keywords = TOOL_KEYWORDS[-1][1]
    today_matches = any(keyword in lowered for keyword in today_keywords)
    explicit_today = any(keyword in lowered for keyword in TODAY_EXPLICIT_KEYWORDS)
    if today_matches and (not specific_matched or explicit_today):
        calls.append(ToolCall("get_today"))
    return calls


def _now() -> datetime:
    """Engine clock, isolated so tests can anchor the local day (§161 seam)."""
    return datetime.now(UTC)


class CoachEngine:
    """One orchestrator plus good tools (spec §90 — no premature agents)."""

    def __init__(
        self,
        provider: Provider,
        *,
        privacy_level: PrivacyLevel = DEFAULT_PRIVACY_LEVEL,
        timezone: str = "UTC",
    ) -> None:
        self.provider = provider
        self.privacy_level = privacy_level
        self.tz = ZoneInfo(timezone)

    async def answer(
        self,
        question: str,
        user_id: uuid.UUID,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> CoachAnswer:
        """Answer one question from deterministic tools + one provider call."""
        effective = clamp_level(self.privacy_level, self.provider.max_privacy_level)
        caveats: list[str] = []
        if effective != self.privacy_level:
            caveats.append(
                f"privacy level clamped from {self.privacy_level!r} to {effective!r} "
                f"for provider {self.provider.name!r}"
            )

        calls = select_tools(question)
        if not calls:
            return CoachAnswer(
                answer=NO_TOOL_ANSWER,
                provider=self.provider.name,
                privacy_level=effective,
                tools_used=[],
                caveats=[*caveats, "no coach tool matched the question"],
            )

        moment = now or _now()
        results = await self._run_tools(calls, user_id, session, moment)
        filtered = apply_privacy(effective, results)
        caveats.extend(
            caveat for result in filtered.values() for caveat in (result.get("caveats") or [])
        )

        prompt = json.dumps(
            {"question": question, "tool_results": filtered}, default=str, sort_keys=True
        )
        answer_text = await self.provider.complete(prompt, privacy_level=effective)
        return CoachAnswer(
            answer=answer_text,
            provider=self.provider.name,
            privacy_level=effective,
            tools_used=list(filtered),
            caveats=caveats,
        )

    async def _run_tools(
        self,
        calls: list[ToolCall],
        user_id: uuid.UUID,
        session: AsyncSession,
        now: datetime,
    ) -> dict[str, dict[str, Any]]:
        """Run the selected tools; a failing tool degrades to an error envelope."""
        results: dict[str, dict[str, Any]] = {}
        for call in calls:
            spec: ToolSpec = TOOLS[call.name]
            kwargs = dict(call.args)
            if call.name == "get_journal":
                kwargs["user_id"] = user_id  # auth-scoped, never keyword-derived
            try:
                results[call.name] = await spec.fn(session, tz=self.tz, now=now, **kwargs)
            except Exception as exc:  # noqa: BLE001 - one tool must never kill the answer
                results[call.name] = {
                    "data": {"error": f"tool failed: {exc}"},
                    "coverage": {},
                    "sources": [],
                    "quality": 0.0,
                    "caveats": [f"{call.name} failed: {exc}"],
                    "generated_at": now.isoformat(),
                }
        return results


@lru_cache(maxsize=1)
def build_engine() -> CoachEngine:
    """Process-wide engine from environment settings (api's entry point)."""
    settings = get_settings()
    return CoachEngine(
        build_provider(settings),
        privacy_level=settings.privacy_level,
        timezone=settings.user_timezone,
    )


def reset_engine_cache() -> None:
    """Test hook: rebuild after environment changes."""
    build_engine.cache_clear()
