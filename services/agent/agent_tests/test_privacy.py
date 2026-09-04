"""Privacy chokepoint tests (ADR 0009): goldens per level, journal-text
containment, unknown-tool refusal, and the aggregates raw-series proof.

These are pure unit tests — no database, no network. They are the tests the
§159 security review reads.
"""

import json
from typing import Any

from somatriq_agent.privacy import (
    DEFAULT_PRIVACY_LEVEL,
    JOURNAL_SUMMARY_CAVEAT,
    JOURNAL_TEXT_REDACTED_CAVEAT,
    apply_privacy,
    clamp_level,
)


# A representative slice of the five coach tools' output (§99 envelopes).
def _sample_results() -> dict[str, dict[str, Any]]:
    return {
        "get_today": {
            "data": {
                "date": "2026-08-21",
                "timezone": "UTC",
                "recovery": {
                    "day": "2026-08-21",
                    "score": 76.66,
                    "algorithm_version": "somatriq_recovery_v1",
                    "contributions": [
                        {
                            "input": "hrv",
                            "value": 105.0,
                            "baseline_median": 100.0,
                            "baseline_iqr": 10.0,
                            "robust_z": 1.0,
                            "contribution": "positive",
                        },
                        {
                            "input": "rhr",
                            "value": 60.0,
                            "baseline_median": 60.0,
                            "baseline_iqr": 1.0,
                            "robust_z": 0.0,
                            "contribution": "neutral",
                        },
                        {
                            "input": "temperature",
                            "contribution": "neutral",
                            "note": "input missing in v1",
                        },
                    ],
                    "missing_inputs": [],
                    "caveats": [],
                },
                "hrv": {"day": "2026-08-21", "rmssd_ms": 105.0, "sdnn_ms": 52.5, "coverage": 1.0},
                "sleep": {"day": "2026-08-21", "duration_minutes": 65.0, "efficiency": None},
                "resting_hr": 60.0,
                "resting_hr_quality": "insufficient",
                "data_freshness_minutes": 1.0,
                "journal": {"caffeine_count": 1, "journal_notes": 1, "caffeine_last_ts": None},
            },
            "coverage": {"heart_rate": 0.002, "hrv": 1.0, "date": "2026-08-21"},
            "sources": ["health.sleep_sessions"],
            "quality": 0.002,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        },
        "get_baselines": {
            "data": {
                "metric": "resting_hr",
                "status": "ok",
                "median": 60.0,
                "q1": 60.0,
                "q3": 61.0,
                "iqr": 1.0,
                "min": 59.0,
                "max": 62.0,
                "n_days": 7,
                "window_days": 28,
                "algorithm_version": "somatriq_baseline_v1",
                "source": "derived.daily_features",
            },
            "coverage": {"days_available": 7, "window_days": 28},
            "sources": ["derived.daily_features"],
            "quality": 0.25,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        },
        "get_trends": {
            "data": {
                "metric": "resting_hr",
                "direction": "up",
                "slope_per_day": 2.0,
                "n_points": 4,
                "series": [
                    {"date": "2026-08-17", "value": 10.0},
                    {"date": "2026-08-18", "value": 12.0},
                ],
                "window_days": 5,
                "algorithm_version": "least-squares-sign-v1",
                "source": "derived.daily_features",
            },
            "coverage": {"days_requested": 5, "days_with_values": 4},
            "sources": ["derived.daily_features"],
            "quality": 0.8,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        },
        "get_journal": {
            "data": {
                "day": "2026-08-21",
                "events": [
                    {
                        "kind": "caffeine",
                        "ts": "2026-08-21T08:00:00+00:00",
                        "structured": {"quantity_mg": 80, "estimated": False},
                        "text": "double espresso",
                    },
                    {
                        "kind": "journal",
                        "ts": "2026-08-21T10:00:00+00:00",
                        "structured": None,
                        "text": "slept badly",
                    },
                ],
            },
            "coverage": {"day": "2026-08-21", "events": 2},
            "sources": ["health.journal_events"],
            "quality": 1.0,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        },
        "get_data_quality": {
            "data": {
                "days": [
                    {
                        "date": "2026-08-21",
                        "sample_count": 100,
                        "coverage_ratio": 0.5,
                        "data_quality": "good",
                    }
                ],
                "feature_set_version": "daily_heart/v1",
            },
            "coverage": {"days_requested": 14, "days_with_rows": 1},
            "sources": ["derived.daily_features"],
            "quality": 0.036,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        },
    }


def test_local_level_is_verbatim() -> None:
    sample = _sample_results()
    assert apply_privacy("local", sample) == sample
    # and the input is never mutated by any other level
    before = json.dumps(sample, sort_keys=True)
    apply_privacy("summary_only", sample)
    assert json.dumps(sample, sort_keys=True) == before


def test_detailed_level_golden_journal_text_nulled() -> None:
    filtered = apply_privacy("detailed", _sample_results())

    events = filtered["get_journal"]["data"]["events"]
    assert [event["text"] for event in events] == [None, None]
    assert events[0]["structured"] == {"quantity_mg": 80, "estimated": False}
    assert JOURNAL_TEXT_REDACTED_CAVEAT in filtered["get_journal"]["caveats"]
    # Everything numeric stays at detailed.
    assert filtered["get_today"]["data"]["resting_hr"] == 60.0
    assert filtered["get_today"]["data"]["recovery"]["score"] == 76.66
    assert filtered["get_baselines"]["data"]["median"] == 60.0
    assert filtered["get_trends"]["data"]["slope_per_day"] == 2.0
    assert filtered["get_data_quality"] == _sample_results()["get_data_quality"]


def test_aggregates_level_golden_daily_values_kept_journal_text_never() -> None:
    filtered = apply_privacy("aggregates", _sample_results())

    # Daily aggregates and baselines are exactly what this level is for.
    assert filtered["get_today"]["data"]["resting_hr"] == 60.0
    assert filtered["get_baselines"]["data"] == _sample_results()["get_baselines"]["data"]
    trends = filtered["get_trends"]["data"]
    assert trends["series"] == _sample_results()["get_trends"]["data"]["series"]
    # Journal free text NEVER leaves at any level except local.
    assert all(event["text"] is None for event in filtered["get_journal"]["data"]["events"])
    assert JOURNAL_TEXT_REDACTED_CAVEAT in filtered["get_journal"]["caveats"]


def test_summary_only_level_golden_no_health_values() -> None:
    filtered = apply_privacy("summary_only", _sample_results())

    today = filtered["get_today"]["data"]
    assert today == {
        "date": "2026-08-21",
        "timezone": "UTC",
        "recovery": {
            "day": "2026-08-21",
            "status": "high",
            "algorithm_version": "somatriq_recovery_v1",
            "contributions": [
                {"input": "hrv", "contribution": "positive", "note": None},
                {"input": "rhr", "contribution": "neutral", "note": None},
                {"input": "temperature", "contribution": "neutral", "note": "input missing in v1"},
            ],
            "missing_inputs": [],
            "caveats": [],
        },
        "inputs_present": {"hrv": True, "rhr": True, "sleep": True},
        "resting_hr_quality": "insufficient",
    }

    baselines = filtered["get_baselines"]["data"]
    assert baselines == {
        "metric": "resting_hr",
        "status": "ok",
        "n_days": 7,
        "window_days": 28,
        "algorithm_version": "somatriq_baseline_v1",
        "source": "derived.daily_features",
    }

    trends = filtered["get_trends"]["data"]
    assert trends == {
        "metric": "resting_hr",
        "direction": "up",
        "n_points": 4,
        "window_days": 5,
        "algorithm_version": "least-squares-sign-v1",
        "source": "derived.daily_features",
    }

    events = filtered["get_journal"]["data"]["events"]
    assert events == [
        {"kind": "caffeine", "ts": "2026-08-21T08:00:00+00:00"},
        {"kind": "journal", "ts": "2026-08-21T10:00:00+00:00"},
    ]
    assert JOURNAL_SUMMARY_CAVEAT in filtered["get_journal"]["caveats"]

    # No health value survives anywhere in the payload.
    dumped = json.dumps(filtered)
    for forbidden in ("76.66", "105.0", "60.0", "median", "slope_per_day", "series", "quantity_mg"):
        assert forbidden not in dumped, forbidden


def test_journal_free_text_only_survives_at_local() -> None:
    sample = _sample_results()
    assert "slept badly" in json.dumps(apply_privacy("local", sample))
    for level in ("summary_only", "aggregates", "detailed"):
        dumped = json.dumps(apply_privacy(level, sample))
        assert "slept badly" not in dumped, level
        assert "double espresso" not in dumped, level


def test_unknown_tool_refused_at_every_non_local_level() -> None:
    """A raw HR series (or any future/unknown tool) cannot leave below local."""
    raw_series = {
        "get_heart_rate_summary": {
            "data": {
                "metric": "heart_rate",
                "points": [
                    {"ts": "2026-08-21T08:00:00+00:00", "bpm": 72.0},
                    {"ts": "2026-08-21T08:01:00+00:00", "bpm": 74.0},
                ],
            },
            "coverage": {"ratio": 1.0},
            "sources": ["timeseries.heart_rate"],
            "quality": 1.0,
            "caveats": [],
            "generated_at": "2026-08-21T12:00:00+00:00",
        }
    }

    for level in ("summary_only", "aggregates", "detailed"):
        filtered = apply_privacy(level, raw_series)
        refused = filtered["get_heart_rate_summary"]
        assert refused["data"]["redacted"] is True, level
        dumped = json.dumps(filtered)
        assert "points" not in dumped, level
        assert "72.0" not in dumped, level
        assert any("not cleared" in caveat for caveat in refused["caveats"])

    # local is the only level that passes unknown content through.
    assert apply_privacy("local", raw_series) == raw_series


def test_known_tool_cannot_leak_new_fields_below_local() -> None:
    """Whitelists rebuild known tools' data — a future field is dropped."""
    sample = _sample_results()
    sample["get_baselines"]["data"]["raw_samples"] = [59.1, 60.2, 61.3]

    for level in ("summary_only", "aggregates"):
        filtered = apply_privacy(level, sample)
        dumped = json.dumps(filtered["get_baselines"])
        assert "raw_samples" not in dumped, level
        assert "59.1" not in dumped, level


def test_clamp_level_provider_ceilings() -> None:
    assert clamp_level("summary_only", "aggregates") == "summary_only"
    assert clamp_level("aggregates", "aggregates") == "aggregates"
    assert clamp_level("detailed", "aggregates") == "aggregates"
    assert clamp_level("local", "aggregates") == "aggregates"
    assert clamp_level("local", "local") == "local"
    assert clamp_level("summary_only", "local") == "summary_only"


def test_default_privacy_level_is_local_only() -> None:
    """ADR 0009: nothing leaves the VPS unless a provider is explicitly enabled."""
    assert DEFAULT_PRIVACY_LEVEL == "local"
