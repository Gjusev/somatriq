# Somatriq wire contracts — payload v2 (M2)

Frozen 2026-09-04. Source of truth: `packages/contracts/somatriq_contracts/`
(ingest.py, raw.py, pairing.py, errors.py). The server validates with these
models; the Android collector mirrors them as Kotlin data classes with the
exact same field names (Kotlin camelCase ↔ JSON camelCase is 1:1; use
`@SerialName` only if a name ever diverges — it must not).

## Ingest envelope v2

`POST /api/v1/ingest/batches` with `Authorization: Bearer <device token>`
(or the transitional global INGEST_TOKEN during M1→M2 cutover).

```json
{
  "batch_id": "018f3a2e-7c1b-7d3e-9f4a-2b6c8def0111",
  "schema_version": "2",
  "decoder_version": "noop-android/1.2.0+somatriq",
  "records": [
    { "source_record_id": "hr-000001", "ts": "2026-09-04T06:41:00Z", "bpm": 58.0 }
  ],
  "raw": {
    "codec": "zstd",
    "journal_version": 1,
    "frame_count": 612,
    "payload_b64": "KLUv/QBY…",
    "payload_sha256": "…sha256 of the COMPRESSED bytes…",
    "uncompressed_bytes": 21483,
    "first_frame_ts": "2026-09-04T06:40:00Z",
    "last_frame_ts": "2026-09-04T06:45:00Z"
  }
}
```

Rules (enforced by contracts, mirrored in Kotlin):

- `schema_version` ∈ {"1","2"}; `raw` present ⇒ must be "2".
- `records` 1..10_000, each with plausible tz-aware UTC ts and catalog bpm range.
- `payload_sha256` hashes the **compressed** bytes (what `payload_b64` decodes
  to) — the server stores that blob verbatim and never decompresses until M4.
- Decoded payload cap: 8 MiB.
- v1 requests (no `raw`, no `decoder_version`) remain valid — they simply
  never earn `raw_ack`.

### Raw Journal v1 container (collector-side; server-opaque)

```
entry   := u32_be length N | u64_be epoch_ms | N bytes frame
journal := zstd( entry* )
```

One entry per complete reconstructed frame captured at the pre-decoder seam.
`length` counts frame bytes only. `epoch_ms` is the capture clock, UTC.

### Ack v2 — `raw_ack` is the prune authorization (ADR 0003)

```json
{
  "batch_id": "…",
  "accepted": true,
  "records_received": 612,
  "records_inserted": 612,
  "records_duplicate": 0,
  "raw_ack": true,
  "raw_frame_count": 612,
  "raw_bytes_stored": 4211,
  "warnings": [],
  "server_time": "2026-09-04T06:45:03Z"
}
```

`raw_ack=true` **only** when the blob is durably written and its
`raw.raw_batches` row is committed. The collector may prune local raw frames
for that batch iff `raw_ack=true`; observations-only acks never authorize
pruning; `failed_permanent` batches are never pruned.

### Idempotency (unchanged, ADR 0006)

Batch UUID is the sole replay key. Content hash covers records + decoder
version + raw payload hash; same UUID + different hash ⇒ 409
IDEMPOTENCY_CONFLICT; same UUID + same hash ⇒ original ack replayed.

## Pairing + auth (ADR 0015)

| Endpoint | Auth | Request → Response |
|---|---|---|
| GET `/api/v1/auth/status` | none | → `AuthStatus {has_account}` |
| POST `/api/v1/auth/register` | none (only while zero accounts) | `RegisterRequest` → `TokenResponse` |
| POST `/api/v1/auth/login` | none | `LoginRequest` → `TokenResponse` |
| POST `/api/v1/pairing/sessions` | account JWT | → `PairingSessionResponse {session_id, pairing_code, expires_at}` |
| GET `/api/v1/pairing/sessions/{id}` | account JWT | → `PairingStatus` (wizard polling) |
| POST `/api/v1/pairing/confirm` | none (code possession) | `PairingConfirmRequest` → `PairingConfirmResponse` |
| GET `/api/v1/devices` | account JWT | → `DeviceInfo[]` |
| POST `/api/v1/devices/{id}/revoke` | account JWT | → 204 |

- Pairing code: 8 chars from `23456789ABCDEFGHJKMNPQRSTUVWXYZ`, TTL 10 min,
  single use, stored server-side **hashed**.
- Device token: `sqt_dev_` + 43 base64url chars; stored **hashed** (sha256);
  scopes `["ingest.write","device.read","sync.read","data.read","journal.write"]`.
- Account JWT: HS256 (SECRET_KEY), `sub`=user UUID, 12 h expiry — web session
  only, never accepted by ingest.
- Login failures: 401 INVALID_CREDENTIALS. Register after account exists:
  409 ACCOUNT_EXISTS. Unknown/expired code: 404 PAIRING_SESSION_NOT_FOUND /
  410 PAIRING_CODE_EXPIRED.

## Journal quick-log (Block 2, grill P7/P10)

- `POST /api/v1/journal/events` with the account JWT (source `web`) or a
  device token carrying `journal.write` (source `mobile` — the collector
  never holds the account JWT).
- Kinds: the six behaviors `caffeine|alcohol|medication|stress|meal|travel`
  plus `journal`/`note`; `structured` only on quantity kinds, and
  `quantity` ONLY when literally stated — otherwise `{"estimated": false}`,
  never a guess (spec §103 rule, mirrored in the Telegram parser).
- Mobile retries: send a stable `client_event_id` (UUID); the server
  returns the SAME stored event, never a duplicate (ADR 0006 spirit).
- DELETE is the owner's correction, user-authored kinds only; system kinds
  (`training`, `experiment_checkin`) answer 409.

## Kotlin mirroring rules

1. JSON field names and types map 1:1 (`Instant` ↔ datetime, `Uuid` ↔ UUID,
   `UInt/ULong` for counts, `String` for tokens).
2. Mirror the **validators**: bpm catalog bounds, tz-aware timestamps,
   base64+size cap, schema gates. A payload the server would 422 must fail
   client-side before wasting an upload.
3. Enums as sealed/const sets: ErrorCode, pairing status, scopes.
4. Version constants (`SCHEMA_VERSION_V2`, `PAIRING_TTL_MINUTES`,
   `DEVICE_SCOPES`, journal version 1) live in one Kotlin object
   (`SyncContract`) — no magic strings in workers.
5. Serialization: kotlinx.serialization with `explicitToJson = false`
   defaults; strict `ignoreUnknownKeys = false` so server-side contract
   additions surface as visible errors, not silent drops.

## Examples

- [`ingest-batch-v2.example.json`](ingest-batch-v2.example.json) — envelope
  with raw (payload_b64 is a zstd journal segment placeholder).
- [`pairing-flow.http`](pairing-flow.http) — the full web+collector dance.
