"""THE privacy chokepoint (ADR 0009) — the only path from coach tools to
any model provider.

``apply_privacy(level, tool_results)`` sits between the deterministic tool
layer and ``Provider.complete`` and is provider-aware by construction: the
coach clamps the requested level to the provider's maximum
(:func:`clamp_level`) before filtering, and the filter itself is the single
deterministic redaction component the §159 security review audits.

Levels (information monotone: each level reveals strictly less than the one
above it):

============  =====================  =====================  ====================
level         health numbers        journal free text      raw / intraday series
============  =====================  =====================  ====================
``local``     everything             included               only via local tools
``detailed``  all numeric detail     NEVER (field nulled)   never to external
``aggregates`` daily aggregates +    NEVER (field nulled)   never
              baselines only
``summary_only`` none — dates,       NEVER (kind + ts only) never
              grades, counts, words
============  =====================  =====================  ====================

The metadata line (``quality`` floats, day counts, data-quality grade
words) is preserved at every level: numbers that DESCRIBE the data are not
health values. Everything that IS a health value — measurements, scores,
robust z's, baselines, slopes, journal text — is dropped below the level
that allows it.

Enforcement is structural, not prompt-based (ADR 0009 rejected
prompt-trusting):

* known tools are rebuilt through per-level WHITELIST redactors — a field
  the redactor does not name cannot pass, so a future tool change adding a
  raw-series field leaks nothing;
* UNKNOWN tools are refused outright at every non-local level (their
  content has no cleared shape);
* journal free text never survives at any level except ``local`` — the
  ``text`` field is nulled even at ``detailed``.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, Literal

PrivacyLevel = Literal["local", "summary_only", "aggregates", "detailed"]

PRIVACY_LEVELS: Final[frozenset[str]] = frozenset(
    {"local", "summary_only", "aggregates", "detailed"}
)

# Information ordering: summary_only < aggregates < detailed < local.
LEVEL_RANK: Final[dict[str, int]] = {
    "summary_only": 0,
    "aggregates": 1,
    "detailed": 2,
    "local": 3,
}

# Default per ADR 0009: local-only — nothing leaves the VPS unless a
# provider is explicitly enabled (external providers start at aggregates).
DEFAULT_PRIVACY_LEVEL: Final[PrivacyLevel] = "local"

JOURNAL_TEXT_REDACTED_CAVEAT: Final[str] = (
    "journal free text redacted: it only leaves at privacy level 'local'"
)
JOURNAL_SUMMARY_CAVEAT: Final[str] = (
    "journal reduced to kind + timestamps: summary_only carries no health values"
)
UNKNOWN_TOOL_CAVEAT = "tool {name!r} result withheld: not cleared at privacy level {level!r}"

# The coach's own tool set (spec §94). Redactors below whitelist per tool;
# anything outside this set is refused at every non-local level.
_KNOWN_TOOLS: Final[frozenset[str]] = frozenset(
    {"get_today", "get_baselines", "get_trends", "get_journal", "get_data_quality"}
)

ResultDict = dict[str, Any]
Redactor = Callable[[ResultDict], ResultDict]


def clamp_level(requested: PrivacyLevel, maximum: PrivacyLevel) -> PrivacyLevel:
    """The effective level: the lesser of what was asked and what a provider
    may ever receive (external providers cap at ``aggregates``, ADR 0009)."""
    if LEVEL_RANK[requested] <= LEVEL_RANK[maximum]:
        return requested
    return maximum


def _rebuild(
    result: Mapping[str, Any],
    *,
    data: ResultDict,
    caveats: list[str] | None = None,
) -> ResultDict:
    """Envelope skeleton with replaced data (whitelisted keys only)."""
    return {
        "data": data,
        "coverage": dict(result.get("coverage") or {}),
        "sources": list(result.get("sources") or []),
        "quality": result.get("quality"),
        "caveats": [*(result.get("caveats") or []), *(caveats or [])],
        "generated_at": result.get("generated_at") or datetime.now(UTC).isoformat(),
    }


def _refuse(name: str, level: PrivacyLevel) -> ResultDict:
    """An unknown tool's result never leaves at a non-local level."""
    caveat = UNKNOWN_TOOL_CAVEAT.format(name=name, level=level)
    return {
        "data": {"redacted": True, "reason": caveat},
        "coverage": {},
        "sources": [],
        "quality": 0.0,
        "caveats": [caveat],
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ── top-level data whitelists (every non-local level) ────────────────────
#
# Known tools keep ONLY the keys below their name: a future tool change
# adding a raw-series field ("points", "raw_samples", …) leaks nothing even
# at detailed/aggregates, because the key simply is not in the whitelist.

_TOOL_DATA_KEYS: Final[dict[str, frozenset[str]]] = {
    "get_today": frozenset(
        {
            "date",
            "timezone",
            "recovery",
            "hrv",
            "sleep",
            "resting_hr",
            "resting_hr_quality",
            "data_freshness_minutes",
            "journal",
        }
    ),
    "get_baselines": frozenset(
        {
            "metric",
            "status",
            "median",
            "q1",
            "q3",
            "iqr",
            "min",
            "max",
            "n_days",
            "first_day",
            "last_day",
            "window_days",
            "algorithm_version",
            "source",
            "days_available",
            "required_minimum",
        }
    ),
    "get_trends": frozenset(
        {
            "metric",
            "direction",
            "slope_per_day",
            "n_points",
            "series",
            "window_days",
            "algorithm_version",
            "source",
        }
    ),
    "get_journal": frozenset({"day", "events"}),
    "get_data_quality": frozenset({"days", "feature_set_version", "timezone"}),
}

_EVENT_KEYS: Final[frozenset[str]] = frozenset({"kind", "ts", "structured", "text"})


def _whitelist_data(result: Mapping[str, Any], keys: frozenset[str]) -> ResultDict:
    return {key: value for key, value in (result.get("data") or {}).items() if key in keys}


def _whitelisted(result: Mapping[str, Any], tool: str) -> ResultDict:
    """Envelope with the tool's data pruned to its whitelisted top-level keys."""
    return _rebuild(result, data=_whitelist_data(result, _TOOL_DATA_KEYS[tool]))


# ── journal ──────────────────────────────────────────────────────────────


def _journal_drop_text(result: Mapping[str, Any]) -> ResultDict:
    """aggregates / detailed: events keep kind + ts + structured, text nulled."""
    data = result.get("data") or {}
    events = [
        {key: event.get(key) for key in _EVENT_KEYS if key != "text"} | {"text": None}
        for event in (data.get("events") or [])
    ]
    return _rebuild(
        result,
        data={**_whitelist_data(result, _TOOL_DATA_KEYS["get_journal"]), "events": events},
        caveats=[JOURNAL_TEXT_REDACTED_CAVEAT],
    )


def _journal_summary(result: Mapping[str, Any]) -> ResultDict:
    """summary_only: kind + timestamps only — no text, no structured values."""
    data = result.get("data") or {}
    events = [
        {"kind": event.get("kind"), "ts": event.get("ts")} for event in (data.get("events") or [])
    ]
    return _rebuild(
        result,
        data={**_whitelist_data(result, _TOOL_DATA_KEYS["get_journal"]), "events": events},
        caveats=[JOURNAL_SUMMARY_CAVEAT],
    )


# ── get_today ────────────────────────────────────────────────────────────


def _score_band(score: Any) -> str:
    if not isinstance(score, (int, float)):
        return "not available"
    if score >= 70:
        return "high"
    if score >= 40:
        return "moderate"
    return "low"


def _today_summary(result: Mapping[str, Any]) -> ResultDict:
    """summary_only: dates, grade words, presence booleans — no health values."""
    data = result.get("data") or {}
    recovery = data.get("recovery") or {}
    contributions = [
        {
            "input": contribution.get("input"),
            "contribution": contribution.get("contribution"),
            "note": contribution.get("note"),
        }
        for contribution in (recovery.get("contributions") or [])
    ]
    hrv = data.get("hrv") or {}
    sleep = data.get("sleep") or {}
    return _rebuild(
        result,
        data={
            "date": data.get("date"),
            "timezone": data.get("timezone"),
            "recovery": {
                "day": recovery.get("day"),
                "status": _score_band(recovery.get("score")),
                "algorithm_version": recovery.get("algorithm_version"),
                "contributions": contributions,
                "missing_inputs": list(recovery.get("missing_inputs") or []),
                "caveats": list(recovery.get("caveats") or []),
            },
            "inputs_present": {
                "hrv": hrv.get("rmssd_ms") is not None,
                "rhr": data.get("resting_hr") is not None,
                "sleep": sleep.get("duration_minutes") is not None,
            },
            "resting_hr_quality": data.get("resting_hr_quality"),
        },
    )


# ── get_baselines ────────────────────────────────────────────────────────

_BASELINE_METADATA_KEYS: Final[tuple[str, ...]] = (
    "metric",
    "status",
    "days_available",
    "required_minimum",
    "n_days",
    "window_days",
    "algorithm_version",
    "source",
)


def _baselines_summary(result: Mapping[str, Any]) -> ResultDict:
    """summary_only: status + counts only — no median/quartiles/min/max."""
    data = result.get("data") or {}
    return _rebuild(result, data={key: data[key] for key in _BASELINE_METADATA_KEYS if key in data})


# ── get_trends ───────────────────────────────────────────────────────────

_TREND_METADATA_KEYS: Final[tuple[str, ...]] = (
    "metric",
    "direction",
    "n_points",
    "window_days",
    "algorithm_version",
    "source",
)


def _trends_summary(result: Mapping[str, Any]) -> ResultDict:
    """summary_only: the direction word only — no slope, no series."""
    data = result.get("data") or {}
    return _rebuild(result, data={key: data[key] for key in _TREND_METADATA_KEYS if key in data})


# ── per-level redactor tables ────────────────────────────────────────────
#
# summary_only rebuilds the numeric interiors down to words and counts;
# aggregates/detailed keep the day-level aggregate values but still prune
# every tool's data to its whitelisted top-level keys (no future field can
# smuggle a raw series through); local is verbatim.

_REDACTORS: Final[dict[str, dict[str, Redactor]]] = {
    "summary_only": {
        "get_today": _today_summary,
        "get_baselines": _baselines_summary,
        "get_trends": _trends_summary,
        "get_journal": _journal_summary,
        "get_data_quality": lambda result: _whitelisted(result, "get_data_quality"),
    },
    "aggregates": {
        "get_today": lambda result: _whitelisted(result, "get_today"),
        "get_baselines": lambda result: _whitelisted(result, "get_baselines"),
        "get_trends": lambda result: _whitelisted(result, "get_trends"),
        "get_journal": _journal_drop_text,
        "get_data_quality": lambda result: _whitelisted(result, "get_data_quality"),
    },
    "detailed": {
        "get_today": lambda result: _whitelisted(result, "get_today"),
        "get_baselines": lambda result: _whitelisted(result, "get_baselines"),
        "get_trends": lambda result: _whitelisted(result, "get_trends"),
        "get_journal": _journal_drop_text,
        "get_data_quality": lambda result: _whitelisted(result, "get_data_quality"),
    },
    "local": {},
}


def apply_privacy(
    level: PrivacyLevel, tool_results: Mapping[str, Mapping[str, Any]]
) -> dict[str, ResultDict]:
    """Filter tool results to exactly what ``level`` may carry to a provider.

    Returns a NEW structure; the input is never mutated. Journal free text
    survives only at ``local``; unknown tools are refused at every
    non-local level; known tools keep only whitelisted keys.
    """
    filtered: dict[str, ResultDict] = {}
    redactors = _REDACTORS[level]
    for name, result in tool_results.items():
        if level != "local" and name not in _KNOWN_TOOLS:
            filtered[name] = _refuse(name, level)
            continue
        redactor = redactors.get(name)
        filtered[name] = redactor(dict(result)) if redactor else dict(result)
    return filtered
