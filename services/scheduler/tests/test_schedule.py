"""Pure scheduling decisions (spec §101-102; grill decision: emit with
marker beats skipping). Fake-clock territory only — no DB here.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from somatriq_scheduler.jobs import sync_stale_text
from somatriq_scheduler.schedule import in_morning_window, parse_brief_time

UTC = UTC
LOCAL = ZoneInfo("Europe/Madrid")


def test_parse_brief_time_valid() -> None:
    assert parse_brief_time("07:00") == time(7, 0)
    assert parse_brief_time("7:05") == time(7, 5)
    assert parse_brief_time("00:00") == time(0, 0)
    assert parse_brief_time("23:59") == time(23, 59)
    assert parse_brief_time("  08:30 ") == time(8, 30)


@pytest.mark.parametrize("bad", ["7", "07:60", "24:00", "ab:cd", "0730", ""])
def test_parse_brief_time_invalid_raises(bad: str) -> None:
    """Bad schedule config must fail loudly at startup, never silently."""
    with pytest.raises(ValueError, match="MORNING_BRIEF_TIME"):
        parse_brief_time(bad)


def test_window_opens_at_brief_time() -> None:
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    assert in_morning_window(now, time(7, 0)) is True


def test_window_closed_before_brief_time() -> None:
    now = datetime(2026, 8, 21, 6, 59, tzinfo=UTC)
    assert in_morning_window(now, time(7, 0)) is False


def test_window_closes_at_noon() -> None:
    assert in_morning_window(datetime(2026, 8, 21, 11, 59, tzinfo=UTC), time(7, 0)) is True
    assert in_morning_window(datetime(2026, 8, 21, 12, 0, tzinfo=UTC), time(7, 0)) is False


def test_window_never_opens_in_the_evening() -> None:
    assert in_morning_window(datetime(2026, 8, 21, 23, 0, tzinfo=UTC), time(7, 0)) is False


def test_window_is_local_time() -> None:
    """07:00 local Madrid is 05:00 UTC in August — the window follows the
    configured zone, not the server clock's raw reading."""
    utc_0459 = datetime(2026, 8, 21, 4, 59, tzinfo=UTC)
    utc_0500 = datetime(2026, 8, 21, 5, 0, tzinfo=UTC)
    assert in_morning_window(utc_0459.astimezone(LOCAL), time(7, 0)) is False
    assert in_morning_window(utc_0500.astimezone(LOCAL), time(7, 0)) is True


def test_sync_stale_text_none_when_no_data() -> None:
    """No observations at all is NOT a sync warning (fresh install)."""
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    assert sync_stale_text(now, None, 6) is None


def test_sync_stale_text_none_when_fresh() -> None:
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    assert sync_stale_text(now, now - timedelta(hours=5), 6) is None


def test_sync_stale_text_marker_when_stale() -> None:
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    marker = sync_stale_text(now, now - timedelta(hours=6, minutes=30), 6)
    assert marker is not None
    assert marker.startswith("Sync warning: newest observation is 6.5 hours old")
    assert "limit 6 hours" in marker
