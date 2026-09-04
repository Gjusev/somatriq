"""Pure scheduling decisions for the morning brief (spec §101-102, §135;
grill decision on partial data: emit WITH markers; recompute silently later).

The configured local time is the EMIT time, not a compute gate: the brief is
enqueued as soon as the window opens (MORNING_BRIEF_TIME local) and the same
window doubles as the backfill rule — if the service was down at the
configured time, it still emits on startup while the same local morning
lasts (before 12:00), unless a brief was already enqueued today. Emitting
with a coverage marker beats skipping.
"""

from datetime import datetime, time

NOON = time(12, 0)


def parse_brief_time(raw: str) -> time:
    """Parse MORNING_BRIEF_TIME ('HH:MM', 24h local). Invalid input raises —
    a misconfigured schedule must fail loudly at startup, never silently."""
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"MORNING_BRIEF_TIME must be HH:MM, got {raw!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"MORNING_BRIEF_TIME must be HH:MM, got {raw!r}") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"MORNING_BRIEF_TIME out of range: {raw!r}")
    return time(hour, minute)


def in_morning_window(local_now: datetime, brief_time: time) -> bool:
    """[brief_time, 12:00) local — the daily slot AND the backfill window."""
    return brief_time <= local_now.time() < NOON
