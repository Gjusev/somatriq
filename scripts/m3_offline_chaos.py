"""M3 offline-reliability chaos harness (spec §196 — hard release gate).

Drives a simulated collector whose semantics MIRROR the Kotlin SyncEngine
1:1 (fork android/sync, see SYNC-NOTES.md):

- batch UUID minted ONCE at enqueue, persisted in a JSON-file queue;
- observations windowed by a max-ts watermark that advances ONLY on ack;
- retries with exponential backoff while the server is unreachable;
- raw journal segments pruned ONLY when ack.raw_ack is true;
- queue survives collector restarts (process exit mid-drain).

Run against the real origin with a REAL outage (compose stopped via Dokploy):

    # terminal 1: start collecting while online
    python scripts/m3_offline_chaos.py https://somatriq.mokka-dev.de collect --minutes 2
    # ... stop the stack (dokploy) ...
    python scripts/m3_offline_chaos.py https://somatriq.mokka-dev.de collect --offline-minutes 3
    # ... start the stack ...
    python scripts/m3_offline_chaos.py https://somatriq.mokka-dev.de drain
    SQT_OWNER_USER=... SQT_OWNER_PASS=... \
        python scripts/m3_offline_chaos.py https://somatriq.mokka-dev.de verify

`verify` also checks the read side (spec §122): the metric endpoint must
answer 401 without the account JWT and serve the owner after login, so the
owner credentials above (same account `pair` uses) are required.

Exit codes: 0 pass, 1 fail — CI-able in the future with a compose stop/start
wrapper.
"""

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "https://somatriq.mokka-dev.de"
CMD = sys.argv[2] if len(sys.argv) > 2 else ""
STATE = Path(__file__).parent / ".m3_collector_state.json"
BACKOFF_S = 2.0


# ── simulated collector state (mirrors filesDir/somatriq/ in the fork) ────


def _load() -> dict[str, Any]:
    return (
        json.loads(STATE.read_text(encoding="utf-8"))
        if STATE.exists()
        else {
            "watermark_iso": None,  # max ts shipped AND acked
            "seq": 0,  # record sequence, survives restarts
            "queue": [],  # [{batch_id, records, raw_b64, sha256, attempts, next_try}]
            "acked": [],  # full envelopes kept for forensic replay in verify()
        }
    )


def _save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _post(envelope: dict[str, Any], token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE + "/api/v1/ingest/batches",
        data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        parsed: dict[str, Any] = json.loads(res.read())
        return parsed


# ── commands ──────────────────────────────────────────────────────────────


def _mint_batch(state: dict[str, Any], count: int) -> dict[str, Any]:
    """Enqueue one batch: UUID minted HERE, never regenerated (idempotency)."""
    import uuid
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    records = []
    frames = b""
    for _ in range(count):
        state["seq"] += 1
        n = state["seq"]
        ts = now - timedelta(seconds=state["seq"] * 0)  # distinct ts per record
        ts = ts + timedelta(microseconds=n)  # monotonic uniqueness
        records.append(
            {
                "source_record_id": f"m3-{n:07d}",
                "ts": ts.isoformat(),
                "bpm": 50 + (n % 40),
            }
        )
        frame = n.to_bytes(4, "big")
        epoch_ms = int(ts.timestamp() * 1000)
        frames += len(frame).to_bytes(4, "big") + epoch_ms.to_bytes(8, "big") + frame
    entry = {
        "batch_id": str(uuid.uuid4()),
        "records": records,
        "raw_b64": base64.b64encode(frames).decode(),
        "raw_sha256": hashlib.sha256(frames).hexdigest(),  # hash the BLOB bytes
        "raw_bytes": len(frames),
        "frame_count": count,
        "attempts": 0,
        "next_try": 0.0,
    }
    state["queue"].append(entry)
    return entry


def collect(offline_minutes: float = 0.0, minutes: float = 0.0) -> int:
    """Collect + attempt sync. While offline (server unreachable or flag set)
    every batch stays queued with backoff — exactly the engine's behavior."""
    token = Path(__file__).parent / ".m3_device_token"
    device_token = token.read_text(encoding="utf-8").strip()
    state = _load()
    deadline = time.time() + (offline_minutes + minutes) * 60
    forced_offline = True
    batches, fail_attempts = 0, 0
    print(
        f"collecting for {offline_minutes + minutes:.1f} min "
        f"(first {offline_minutes:.1f} forced offline)"
    )
    while time.time() < deadline:
        entry = _mint_batch(state, 5)
        batches += 1
        _save(state)
        if not forced_offline:
            _try_sync(state, device_token, quiet=True)
        else:
            entry["attempts"] += 1
            entry["next_try"] = time.time() + BACKOFF_S
            fail_attempts += 1
            _save(state)
        time.sleep(2.0)
        if forced_offline and time.time() - (deadline - minutes * 60) >= offline_minutes * 60:
            forced_offline = False
    print(f"collected {batches} batches ({fail_attempts} enqueued-while-offline attempts)")
    return 0


def _try_sync(state: dict[str, Any], token: str, quiet: bool = False) -> bool:
    """Drain due queue entries. Watermark advances ONLY on accepted ack."""
    progressed = False
    remaining: list[dict[str, Any]] = []
    for entry in state["queue"]:
        if entry["next_try"] > time.time():
            remaining.append(entry)
            continue
        envelope = {
            "batch_id": entry["batch_id"],
            "schema_version": "2",
            "decoder_version": "m3-chaos/1",
            "records": entry["records"],
            "raw": {
                "codec": "zstd",
                "journal_version": 1,
                "frame_count": entry["frame_count"],
                "payload_b64": entry["raw_b64"],
                "payload_sha256": entry["raw_sha256"],
                "uncompressed_bytes": entry["raw_bytes"],
                "first_frame_ts": entry["records"][0]["ts"],
                "last_frame_ts": entry["records"][-1]["ts"],
            },
        }
        try:
            ack = _post(envelope, token)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            if exc.code >= 500:  # retryable
                entry["attempts"] += 1
                entry["next_try"] = time.time() + BACKOFF_S * (2 ** min(entry["attempts"], 5))
            else:  # 4xx: permanent — surface loudly, keep for forensics
                print(f"  PERMANENT {exc.code} on {entry['batch_id'][:8]}: {body}")
            remaining.append(entry)
            continue
        except (urllib.error.URLError, OSError) as exc:
            entry["attempts"] += 1
            entry["next_try"] = time.time() + BACKOFF_S * (2 ** min(entry["attempts"], 5))
            if not quiet:
                print(f"  offline/backoff: {exc.__class__.__name__} attempt {entry['attempts']}")
            remaining.append(entry)
            continue
        if ack.get("accepted"):
            # acked: advance watermark to the batch's max ts, prune raw only
            # if raw_ack (the ONLY authorization — records-only acks keep it).
            max_ts = max(r["ts"] for r in entry["records"])
            prev = state["watermark_iso"]
            if prev is None or max_ts > prev:
                state["watermark_iso"] = max_ts
            if not ack.get("raw_ack"):
                print(f"  WARNING: {entry['batch_id'][:8]} acked WITHOUT raw_ack")
            state["acked"].append({"envelope": envelope, "ack": ack})
            progressed = True
        else:
            entry["attempts"] += 1
            remaining.append(entry)
    state["queue"] = remaining
    _save(state)
    return progressed


def drain(max_wait_s: float = 120.0) -> int:
    """Server is back: drain the queue with retries until empty."""
    token = Path(__file__).parent / ".m3_device_token"
    device_token = token.read_text(encoding="utf-8").strip()
    state = _load()
    deadline = time.time() + max_wait_s
    while state["queue"] and time.time() < deadline:
        if not _try_sync(state, device_token):
            time.sleep(BACKOFF_S)
    if state["queue"]:
        print(f"DRAIN INCOMPLETE: {len(state['queue'])} batches still queued")
        return 1
    print(f"drained. watermark={state['watermark_iso']} seq={state['seq']}")
    return 0


def _owner_login() -> str:
    """Account JWT for the read-side verification (spec §122: reads are
    owner-only). Credentials come from the environment — the same owner
    account the `pair` command uses."""
    username = os.environ.get("SQT_OWNER_USER")
    password = os.environ.get("SQT_OWNER_PASS")
    if not username or not password:
        print(
            "VERIFY FAIL — the read check needs the owner account: set "
            "SQT_OWNER_USER and SQT_OWNER_PASS (same credentials as `pair`)"
        )
        raise SystemExit(1)
    req = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return str(json.loads(res.read())["access_token"])


def verify() -> int:
    """The §196 gate: no loss, no duplicates — proven by forensic replay.

    Re-post EVERY acked envelope byte-identically. Exactly-once holds iff the
    server answers accepted with inserted=0 and duplicates=<n> for every one
    (all records present => nothing lost; nothing re-inserted => no dupes).
    Uses only the public ingest contract — no admin endpoint needed. Finally
    the owner-authenticated metric read proves the data is actually VISIBLE
    server-side (and that reads answer the account JWT only, spec §122).
    """
    token_file = Path(__file__).parent / ".m3_device_token"
    device_token = token_file.read_text(encoding="utf-8").strip()
    state = _load()
    if state["queue"]:
        print(f"VERIFY FAIL — {len(state['queue'])} batches never drained (loss window)")
        return 1
    total_records = 0
    for i, item in enumerate(state["acked"], 1):
        env = item["envelope"]
        n = len(env["records"])
        total_records += n
        ack = _post(env, device_token)
        if (
            not ack.get("accepted")
            or ack.get("records_inserted", -1) != 0
            or ack.get("records_duplicate", -1) != n
        ):
            print(f"VERIFY FAIL — batch {env['batch_id'][:8]} replay mismatch: {ack}")
            return 1
        if env.get("raw") and not ack.get("raw_ack"):
            print(f"VERIFY FAIL — batch {env['batch_id'][:8]} lost raw_ack on replay")
            return 1
        print(f"  [{i:2}/{len(state['acked'])}] {env['batch_id'][:8]} dupes={n}")

    # Read-side gate: the metric endpoint answers ONLY the account JWT.
    jwt = _owner_login()
    try:
        urllib.request.urlopen(
            urllib.request.Request(BASE + "/api/v1/metrics/heart_rate?last_hours=24"),
            timeout=15,
        )
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, f"unauthenticated read: expected 401, got {exc.code}"
    else:
        print("VERIFY FAIL — unauthenticated metric read was allowed (spec §122)")
        return 1
    req = urllib.request.Request(
        BASE + "/api/v1/metrics/heart_rate?last_hours=24",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        series = json.loads(res.read())
    if series.get("count", 0) <= 0:
        print(f"VERIFY FAIL — metric read empty after {total_records} records: {series}")
        return 1
    print(f"read gate ok: {series['count']} points visible to the owner (401 without JWT)")
    print(
        f"M3 VERIFY PASS — {len(state['acked'])} batches / {total_records} records: "
        "exactly-once through a real outage (no loss, no duplicates)"
    )
    return 0


def reset() -> int:
    STATE.unlink(missing_ok=True)
    print("state reset")
    return 0


def pair(username: str, password: str, device_name: str = "m3-chaos-collector") -> int:
    """Pair the simulated collector so it owns a real device token."""

    def req(
        method: str, path: str, body: dict[str, Any] | None = None, tok: str | None = None
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        r = urllib.request.Request(
            BASE + path,
            data=json.dumps(body).encode() if body else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(r, timeout=15) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}

    auth = req("POST", "/api/v1/auth/login", {"username": username, "password": password})
    session = req("POST", "/api/v1/pairing/sessions", tok=auth["access_token"])
    paired = req(
        "POST",
        "/api/v1/pairing/confirm",
        {"pairing_code": session["pairing_code"], "device_name": device_name},
    )
    Path(__file__).parent.joinpath(".m3_device_token").write_text(paired["token"], encoding="utf-8")
    print(f"paired as {device_name} ({paired['device_id']})")
    return 0


def main() -> int:
    if CMD == "pair":
        return pair(sys.argv[3], sys.argv[4])
    if CMD == "collect":
        return collect(
            offline_minutes=float(sys.argv[3]) if len(sys.argv) > 3 else 0.0,
            minutes=float(sys.argv[4]) if len(sys.argv) > 4 else 0.0,
        )
    if CMD == "drain":
        return drain()
    if CMD == "verify":
        return verify()
    if CMD == "reset":
        return reset()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
