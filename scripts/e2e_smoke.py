"""M1 end-to-end smoke against a deployed Somatriq origin (spec §152 path 1).

Usage:
    python scripts/e2e_smoke.py https://somatriq.example.com <INGEST_TOKEN>

Posts a synthetic heart-rate batch TWICE (idempotency proof), then reads the
metric endpoint. Metric reads carry the account JWT (spec §122) — set
SQT_OWNER_USER and SQT_OWNER_PASS to the owner's web credentials (the same
login the web app uses); the script fails with a clear error when they are
missing. Exits non-zero on any violated expectation.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

BASE = sys.argv[1].rstrip("/")
TOKEN = sys.argv[2]
OWNER_USER = os.environ.get("SQT_OWNER_USER")
OWNER_PASS = os.environ.get("SQT_OWNER_PASS")
BATCH_ID = str(uuid4())
NOW = datetime.now(UTC)
RECORDS = [
    {
        "source_record_id": f"e2e-{BATCH_ID[:8]}-{i}",
        "ts": (NOW - timedelta(minutes=len(range(60)) - i)).isoformat(),
        "bpm": 60 + (i % 30),
    }
    for i, _ in enumerate(range(60))
]


def post_batch() -> dict[str, Any]:
    payload = json.dumps({"batch_id": BATCH_ID, "schema_version": "1", "records": RECORDS}).encode()
    req = urllib.request.Request(
        BASE + "/api/v1/ingest/batches",
        data=payload,
        headers={"Content-Type": "application/json", "X-Somatriq-Token": TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        parsed: dict[str, Any] = json.loads(res.read())
        return parsed


def owner_login() -> str:
    """Account JWT for the metric read (spec §122: reads are owner-only)."""
    if not OWNER_USER or not OWNER_PASS:
        print(
            "E2E FAIL: metric reads need the account JWT — set SQT_OWNER_USER and "
            "SQT_OWNER_PASS to the owner's web credentials"
        )
        sys.exit(1)
    req = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=json.dumps({"username": OWNER_USER, "password": OWNER_PASS}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return str(json.loads(res.read())["access_token"])


def get_metric(jwt: str) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE + "/api/v1/metrics/heart_rate?last_hours=24&bucket=5m",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        parsed: dict[str, Any] = json.loads(res.read())
        return parsed


def main() -> int:
    first = post_batch()
    assert first["accepted"] is True, f"first post not accepted: {first}"
    assert first["records_inserted"] == len(RECORDS), f"expected all inserted: {first}"
    assert first["records_duplicate"] == 0, f"unexpected duplicates: {first}"
    print(f"1) first ingest  ok: inserted={first['records_inserted']}")

    second = post_batch()
    assert second["accepted"] is True, f"replay not accepted: {second}"
    assert second["records_inserted"] == 0, f"REPLAY INSERTED ROWS — idempotency broken: {second}"
    assert second["records_duplicate"] == len(RECORDS), f"replay duplicates wrong: {second}"
    print(f"2) replay        ok: inserted=0 duplicates={second['records_duplicate']} (idempotent)")

    jwt = owner_login()
    print("3) owner login   ok: account JWT minted for the metric read")

    series = get_metric(jwt)
    assert series["count"] > 0, f"metric empty after ingest: {series}"
    assert series["unit"] == "bpm" and series["points"], series
    print(f"4) metric read   ok: {series['count']} bucketed points, coverage={series['coverage']}")

    print("E2E PASS — synthetic batch -> ingest -> postgres -> owner-authenticated metric read")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, urllib.error.URLError) as exc:
        print(f"E2E FAIL: {exc}")
        sys.exit(1)
