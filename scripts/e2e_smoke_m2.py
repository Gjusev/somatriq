"""M2 end-to-end smoke against a deployed Somatriq origin.

Walks the full ADR 0015 pairing dance + the ADR 0003 raw envelope:

    register/login -> pairing session -> confirm (collector side) ->
    device-token ingest (records + raw journal) -> raw_ack -> replay ->
    devices list -> metric read.

Usage:
    python scripts/e2e_smoke_m2.py https://somatriq.example.com <username> <password>

Exits non-zero on any violated expectation. The passphrase is only used to
claim/login the single local account (first caller wins — after that it logs
in with the same credentials).
"""

import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

BASE = sys.argv[1].rstrip("/")
USERNAME = sys.argv[2]
PASSWORD = sys.argv[3]
NOW = datetime.now(UTC)
BATCH_ID = str(uuid4())
N = 60
RECORDS = [
    {
        "source_record_id": f"e2e-m2-{BATCH_ID[:8]}-{i}",
        "ts": (NOW - timedelta(minutes=N - i)).isoformat(),
        "bpm": 55 + (i % 25),
    }
    for i in range(N)
]


def _req(
    method: str, path: str, body: dict[str, Any] | None = None, token: str | None = None
) -> Any:
    """Typed-loose by design: smoke script, response shapes asserted inline."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        raw = res.read()
        return json.loads(raw) if raw else {}


def _fail(step: str, exc: Exception) -> None:
    print(f"E2E FAIL at {step}: {exc}")
    sys.exit(1)


def main() -> int:
    # 1. Account: claim (first run) or login.
    status = _req("GET", "/api/v1/auth/status")
    if not status.get("has_account"):
        auth = _req("POST", "/api/v1/auth/register", {"username": USERNAME, "password": PASSWORD})
        step = "register"
    else:
        auth = _req("POST", "/api/v1/auth/login", {"username": USERNAME, "password": PASSWORD})
        step = "login"
    jwt = auth["access_token"]
    print(f"1) account {step:8s} ok: token expires {auth['expires_at']}")

    # 2. Pairing session (web side).
    session = _req("POST", "/api/v1/pairing/sessions", token=jwt)
    code = session["pairing_code"]
    assert len(code) == 8, f"pairing code shape: {code}"
    print(f"2) pairing session ok: code={code} expires {session['expires_at']}")

    # 3. Confirm (collector side — code possession is the proof).
    device_name = f"e2e-collector-{int(time.time())}"
    paired = _req(
        "POST",
        "/api/v1/pairing/confirm",
        {"pairing_code": code, "device_name": device_name},
    )
    device_token = paired["token"]
    assert device_token.startswith("sqt_dev_"), f"device token shape: {device_token[:12]}…"
    assert paired["scopes"] == [
        "ingest.write",
        "device.read",
        "sync.read",
        "data.read",
    ], paired["scopes"]
    device_id = paired["device_id"]
    print(f"3) pairing confirm ok: device={device_id} scopes={paired['scopes']}")

    # 4. Status shows consumed + device (wizard polling view).
    st = _req("GET", f"/api/v1/pairing/sessions/{session['session_id']}", token=jwt)
    assert st["status"] == "consumed" and st["device"]["name"] == device_name, st
    print(f"4) session status  ok: consumed by {st['device']['name']}")

    # 5. Envelope v2: records + raw journal segment (placeholder bytes; the
    #    server treats the blob as opaque until M4 replay).
    journal = b""
    for i in range(N):
        frame = bytes([0x11, i % 256, 0x22, i % 7])
        epoch_ms = int(NOW.timestamp() * 1000) + i
        journal += len(frame).to_bytes(4, "big") + epoch_ms.to_bytes(8, "big") + frame
    payload_b64 = base64.b64encode(journal).decode()
    envelope = {
        "batch_id": BATCH_ID,
        "schema_version": "2",
        "decoder_version": "e2e-smoke/1",
        "records": RECORDS,
        "raw": {
            "codec": "zstd",
            "journal_version": 1,
            "frame_count": N,
            "payload_b64": payload_b64,
            "payload_sha256": hashlib.sha256(base64.b64decode(payload_b64)).hexdigest(),
            "uncompressed_bytes": len(journal),
            "first_frame_ts": RECORDS[0]["ts"],
            "last_frame_ts": RECORDS[-1]["ts"],
        },
    }
    ack = _req("POST", "/api/v1/ingest/batches", envelope, token=device_token)
    assert ack["accepted"] is True, ack
    assert ack["records_inserted"] == N, ack
    assert ack["raw_ack"] is True, f"raw_ack must be true — prune authorization missing: {ack}"
    assert ack["raw_frame_count"] == N, ack
    print(
        f"5) envelope ingest ok: inserted={ack['records_inserted']}"
        f" raw_ack={ack['raw_ack']} raw_bytes={ack['raw_bytes_stored']}"
    )

    # 6. Replay: identical envelope -> zero inserts, same raw_ack truth.
    replay = _req("POST", "/api/v1/ingest/batches", envelope, token=device_token)
    assert replay["records_inserted"] == 0, f"REPLAY INSERTED ROWS — idempotency broken: {replay}"
    assert replay["records_duplicate"] == N, replay
    assert replay["raw_ack"] is True, replay
    print(
        f"6) replay          ok: inserted=0 duplicates={replay['records_duplicate']}"
        " raw_ack still true"
    )

    # 7. Tamper: same batch UUID, different content -> 409.
    tampered = json.loads(json.dumps(envelope))
    tampered["records"][0]["bpm"] = 99.9
    try:
        _req("POST", "/api/v1/ingest/batches", tampered, token=device_token)
    except urllib.error.HTTPError as exc:
        assert exc.code == 409, f"expected 409, got {exc.code}"
        print("7) uuid conflict   ok: 409 on reused UUID with different content")
    else:
        raise AssertionError("tampered replay was accepted — idempotency broken")

    # 8. Bad token: 401.
    try:
        _req("POST", "/api/v1/ingest/batches", envelope, token="sqt_dev_" + "x" * 43)
    except urllib.error.HTTPError as exc:
        assert exc.code in (401, 403), f"expected 401/403, got {exc.code}"
        print(f"8) bad token       ok: {exc.code}")

    # 9. Devices list shows our collector with last_used.
    devices = _req("GET", "/api/v1/devices", token=jwt)
    ours = next((d for d in devices if d["device_id"] == device_id), None)
    assert ours is not None and ours["last_used_at"], devices
    print(f"9) devices list    ok: {ours['name']} last_used={ours['last_used_at']}")

    # 10. Metric still serves — reads carry the account JWT now (spec §122);
    #     the device token must NOT authorize a read.
    try:
        _req("GET", "/api/v1/metrics/heart_rate?last_hours=24&bucket=5m", token=device_token)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, f"device token on a read: expected 401, got {exc.code}"
        print("10) read guard     ok: device token on metric read rejected (401)")
    else:
        raise AssertionError("device token authorized a metric read — reads must be owner-only")
    series = _req("GET", "/api/v1/metrics/heart_rate?last_hours=24&bucket=5m", token=jwt)
    assert series["count"] > 0, series
    print(f"11) metric read    ok: {series['count']} points, coverage={series['coverage']}")

    print(
        "E2E PASS — account -> pairing -> device token -> envelope v2 + raw_ack -> replay "
        "-> owner-authenticated read"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, urllib.error.URLError, KeyError) as exc:
        _fail("main", exc)
        raise
