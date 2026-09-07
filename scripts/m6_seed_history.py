"""Seed a synthetic 8-night history in production to exercise M6 recovery.

Pattern (mirrors the exact-math tests): each night's sleep-window RR =
[800, 800+d, 800, 800+d] * k → that night's RMSSD is exactly d ms.
Baseline nights (d=40) → today (d=50) ⇒ HRV robust z > 0 ⇒ positive HRV
contribution; RHR via 40 buckets of HR per day (quiet hour at 52bpm);
sleep duration ~7.5h steady, tonight 8.1h (slightly positive).

Usage:
    python scripts/m6_seed_history.py https://somatriq.mokka-dev.de
Reads scripts/.m3_device_token (device-token auth).
"""

import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://somatriq.mokka-dev.de").rstrip("/")
with open("scripts/.m3_device_token", encoding="utf-8") as _f:
    TOKEN = _f.read().strip()
TZ = "Europe/Madrid"


def post(path: str, body: object) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        parsed: dict[str, Any] = json.loads(res.read())
        return parsed


def night_window(days_ago: int) -> tuple[datetime, datetime]:
    """Sleep starting days_ago+1 at 23:30 UTC-ish, ending days_ago at ~07:00."""
    now = datetime.now(UTC)
    wake = now.replace(hour=5, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    start = wake - timedelta(hours=7, minutes=45)
    return start, wake


def seed_night(days_ago: int, rmssd_target: int, rhr_quiet: int) -> None:
    start, wake = night_window(days_ago)
    # sleep session
    ack = post(
        "/api/v1/ingest/sleep-sessions",
        {
            "batch_id": str(uuid4()),
            "schema_version": "1",
            "decoder_version": "m6-seed/1",
            "sessions": [
                {
                    "source_record_id": f"m6-seed-sleep-{days_ago}",
                    "start_ts": start.isoformat(),
                    "end_ts": wake.isoformat(),
                    "efficiency": 0.9,
                    "resting_hr": rhr_quiet,
                    "avg_hrv": float(rmssd_target),
                    "user_edited": False,
                    "stages": [
                        {
                            "state": "deep",
                            "start_ts": (start + timedelta(hours=2)).isoformat(),
                            "end_ts": (start + timedelta(hours=3)).isoformat(),
                        },
                    ],
                }
            ],
        },
    )
    assert ack["accepted"], ack
    # RR inside the window: pattern gives RMSSD exactly rmssd_target
    rr: list[dict[str, object]] = []
    t = start + timedelta(minutes=5)
    while t < wake - timedelta(minutes=5) and len(rr) < 400:
        for i, base in enumerate([800, 800 + rmssd_target]):
            rr.append(
                {
                    "source_record_id": f"m6-seed-rr-{days_ago}-{len(rr)}",
                    "ts": (t + timedelta(milliseconds=500 * i)).isoformat(),
                    "rr_ms": base,
                    "seq": len(rr),
                }
            )
        t += timedelta(seconds=4)
    ack = post(
        "/api/v1/ingest/rr-intervals",
        {
            "batch_id": str(uuid4()),
            "schema_version": "1",
            "decoder_version": "m6-seed/1",
            "records": rr,
        },
    )
    assert ack["accepted"], ack
    # HR for the day: 40 buckets (5 min each) with quiet hour at rhr_quiet
    day_start = wake.replace(hour=10, minute=0, second=0, microsecond=0)
    records = []
    for b in range(40):
        bucket_t = day_start + timedelta(minutes=5 * b)
        bpm = float(rhr_quiet if b < 6 else rhr_quiet + 15 + (b % 10))
        for s in range(6):
            records.append(
                {
                    "source_record_id": f"m6-seed-hr-{days_ago}-{b}-{s}",
                    "ts": (bucket_t + timedelta(seconds=s)).isoformat(),
                    "bpm": bpm,
                }
            )
    ack = post(
        "/api/v1/ingest/batches",
        {
            "batch_id": str(uuid4()),
            "schema_version": "1",
            "decoder_version": "m6-seed/1",
            "records": records,
        },
    )
    assert ack["accepted"], ack
    print(f"  night -{days_ago}d: rmssd {rmssd_target} | {len(rr)} RR + {len(records)} HR")


def main() -> None:
    print("seeding baseline nights (rmssd 40, rhr 55) + tonight (rmssd 50, rhr 53)")
    for days_ago in range(20, 0, -1):
        seed_night(days_ago, 40, 55)
    seed_night(0, 50, 53)  # tonight: better HRV, slightly lower RHR, +8.1h sleep
    print("seed complete — GET /api/v1/metrics/today should now compute recovery")


if __name__ == "__main__":
    main()
