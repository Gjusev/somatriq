"""Deterministic AI tools (spec §94) — the ONLY facts an LLM ever sees.

ADR 0009: statistics are computed here in deterministic Python over
somatriq_db, returned as JSON-safe §99-shaped envelopes (data, coverage,
sources, quality, caveats, generated_at). The LLM narrates; it never
computes. This package is the shared implementation layer the MCP server's
tools can be refactored onto later.
"""

from .registry import COACH_READ_SCOPE, TOOLS, ToolSpec
from .tools import (
    BASELINE_ALGORITHM,
    DEFAULT_BASELINE_DAYS,
    DEFAULT_QUALITY_DAYS,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_TREND_DAYS,
    INSUFFICIENT_BASELINE_CAVEAT,
    METRIC_SOURCES,
    MIN_TREND_POINTS,
    TREND_ALGORITHM,
    ToolFn,
    ToolValidationError,
    daily_metric_values,
    envelope,
    get_baselines,
    get_data_quality,
    get_journal,
    get_today,
    get_training,
    get_trends,
)

__all__ = [
    "BASELINE_ALGORITHM",
    "COACH_READ_SCOPE",
    "DEFAULT_BASELINE_DAYS",
    "DEFAULT_QUALITY_DAYS",
    "DEFAULT_TRAINING_DAYS",
    "DEFAULT_TREND_DAYS",
    "INSUFFICIENT_BASELINE_CAVEAT",
    "METRIC_SOURCES",
    "MIN_TREND_POINTS",
    "TOOLS",
    "TREND_ALGORITHM",
    "ToolFn",
    "ToolSpec",
    "ToolValidationError",
    "daily_metric_values",
    "envelope",
    "get_baselines",
    "get_data_quality",
    "get_journal",
    "get_today",
    "get_training",
    "get_trends",
]
