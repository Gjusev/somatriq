"""Tool registry (spec §94): the enumerable surface for the provider layer.

The coach engine and any future MCP refactor enumerate ``TOOLS`` to know
what exists; each :class:`ToolSpec` carries the callable plus the scope a
client would need to invoke it (spec §97 vocabulary; the coach's own scope
is ``coach.read``).
"""

from dataclasses import dataclass
from typing import Final

from .tools import (
    ToolFn,
    get_baselines,
    get_data_quality,
    get_journal,
    get_today,
    get_trends,
)

COACH_READ_SCOPE: Final[str] = "coach.read"


@dataclass(frozen=True)
class ToolSpec:
    """One deterministic tool: name, description, callable, required scope."""

    name: str
    description: str
    fn: ToolFn
    required_scope: str = COACH_READ_SCOPE


_TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="get_today",
        description=(
            "Today's recovery score, sleep, HRV, resting HR and journal counts, "
            "assembled by the shared analytics reading (spec §76)."
        ),
        fn=get_today,
    ),
    ToolSpec(
        name="get_baselines",
        description=(
            "Personal baseline (median + inclusive IQR, somatriq_baseline_v1) for a "
            "metric over a trailing window of daily values."
        ),
        fn=get_baselines,
    ),
    ToolSpec(
        name="get_trends",
        description=(
            "A metric's daily values over a trailing window plus a deterministic "
            "direction word (least-squares slope sign)."
        ),
        fn=get_trends,
    ),
    ToolSpec(
        name="get_journal",
        description="Journal events for one local day, verbatim (kind, ts, structured, text).",
        fn=get_journal,
    ),
    ToolSpec(
        name="get_data_quality",
        description="Per-day coverage and data-quality grades from the daily feature store.",
        fn=get_data_quality,
    ),
)

TOOLS: Final[dict[str, ToolSpec]] = {spec.name: spec for spec in _TOOLS}

__all__ = ["COACH_READ_SCOPE", "TOOLS", "ToolSpec"]
