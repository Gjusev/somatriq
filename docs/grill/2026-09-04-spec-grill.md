# Somatriq Spec Grill — 2026-09-04

Adversarial multi-lens review of `docs/SPEC.md` v2.0 before founding ADRs.
Method: 8 lens reviewers (domain-data, sync-mobile, infra-deploy, security, analytics, ai-agent, frontend, scope-milestones) → independent skeptical verification of every blocker/high finding → completeness critic → synthesis. 26 agents, 64 findings, 16 adversarially verified (8 confirmed, 7 downgraded, 1 refuted; 21 blocker/high left unverified due to a 16-verification cap — the synthesizer saw all 64).

## Verdict

The spec is a strong intent-and-structure document but not yet a complete source of truth for the 14 founding ADRs. Five verified blockers prevent correct founding documents; three are architect-resolvable, two require the product owner, plus four more product-level decisions. Everything else (glossary, most ADRs, repo skeleton, metric catalog, NOOP fork research, M1 scaffolding) is unambiguous and can start immediately.

## Blockers (verified)

### B1. Raw-evidence pipeline is internally contradictory — DECISION PENDING (product owner)
M2 (s195) ships sync while raw archival is M4 (s197); the M2 payload content is never defined; s51 prunes local raw 7 days after confirmed upload with no rejection carve-out. Every raw frame captured between M2 and M4 is destroyed within a week — violating s2/s220/s221. NOOP's reuse list (s33) has no pre-decoder capture point, so the fork must add one (unbudgeted).
Options: (1) raw + observations from day one, minimal blob store in M2, replay tooling deferred to M4; (2) observations-only, defer phone raw deletion until M4 acks raw receipt (unbounded phone storage); (3) accept the gap as a documented carve-out.

### B2. Ingest/sync contract undefined end to end — architect-resolvable (recommended resolution)
s40 lists four idempotency mechanisms with no precedence; the observation natural key is defined nowhere (grep: zero occurrences of unique/ON CONFLICT in the spec); `failed_permanent` (s39) has no trigger, no wire shape, no owner, and unknown interaction with the s51 raw prune; queue granularity drifts between record-level (s39) and batch-level (s37/42).
Recommended: batch UUID is the sole idempotency key (content hash stored for forensics, 409 on mismatch); per-record natural key `(user_id, device_id, source_record_id)` unique per hypertable; permanent rejection = HTTP 422 + `accepted:false` + error_code, mobile retains failed_permanent batches indefinitely (never raw-pruned), surfaced as persistent sync warning + `ingest.failures` row + notification; queue entries are batch-level.

### B3. Nobody owns migration execution — architect-resolvable (recommended resolution)
s134 mandates migrations and forbids startup mutation; s211 requires verifying "migration completed"; yet no migrate service exists in s25, no job in s135, no scheduler duty in s137. Seven services boot concurrently against an unmigrated DB and crash-loop. Alembic autogenerate cannot emit TimescaleDB DDL (hypertables, continuous aggregates) — hand-written SQL with pinned extension version required.
Recommended: one-shot `somatriq_migrate` compose service running `alembic upgrade head`; all services `depends_on` it with `condition: service_completed_successfully`; CI tests up+down migrations; add "migrate service exited 0" to the s211 checklist.

### B4. MCP client authentication never specified — DECISION PENDING (product owner)
s96 says only "authenticated HTTPS"; s211 verifies an "MCP authenticated endpoint"; s97 defaults desktop to `health.read` while s98 ships write tools with no scope-grant flow anywhere. Options: OAuth 2.1 resource server / scoped PAT bearer / proxy-level auth. Either plugs into the same ADR 0010 skeleton: token → scopes → per-connection grant record → FastMCP auth hook; write scopes require explicit per-connection grant in the web UI.

### B5. AI privacy levels unenforced and incomplete — architect-resolvable (recommended resolution, default level PENDING)
s92 names three levels, a default, and one prohibition. No per-level payload contract, no enforcing component, no provider-awareness (Ollama is local), and the MCP server is a second AI egress path the levels never cover (memory.read exposes profile/goal/constraint text to external clients).
Recommended: single deterministic context-builder/redaction layer between tools and LLMProvider as the only egress point (provider-aware); same level-keyed filter inside the MCP response serializer; per-level payload-contract table in ADR 0009.

## Product-owner decisions (in order)

1. **Raw strategy at M2** (B1). Recommendation: raw from day one.
2. **MCP client auth mechanism** (B4). Recommendation: OAuth 2.1, PAT as admin/service fallback; ship PAT first if today's client is header-capable.
3. **External LLM egress default** (B5). Recommendation: local-only by default, explicit per-provider opt-in at aggregates level.
4. **Sleep-night attribution + timezone policy.** Recommendation: wake date, per-day effective timezone, bounded recompute on timezone change.
5. **Licensing/distribution stance for server repo + NOOP fork.** Recommendation: private for now; NOOP license inventory during M0 fork research; ratify before anything is published.
6. **Morning brief on incomplete overnight data.** Recommendation: emit with visible coverage marker, recompute silently when data lands.

## Systemic gaps (completeness critic — none of the 8 lenses covered these)

- **No canonical metric/units/validation dictionary.** Needed at M1 (first hypertable + first ingest test). Mitigation: `system.metrics` catalog (names, units, valid physiological ranges, cadence, valid aggregations) + one shared validation library for connectors/ingest/quality/charts/MCP.
- **"Wearables can change" (s1) has no mechanism.** No device lifecycle states, no firmware/collector-build in provenance, no instrument-change policy for baselines/anomalies/experiments; source priority is not time-scoped. A WHOOP 4→5 migration silently corrupts every personal-science claim. Mitigation: device active-from/to ranges, provenance extensions, re-baselining at instrument boundaries, distribution-shift detection in the quality engine.
- **The RAW→OBSERVATION decode is unversioned and unlinked.** Without `decoder_version` + `raw_batch_id` on observations (M1 schema), the archive is write-only and M4 "verify replay" is impossible. Two columns + one registry entry at M1; full rewrite to retrofit later.
- **No freshness/trigger semantics for derived products.** Morning pipeline has no fire time, no partial-data behavior, no as-of semantics for Today; invalidation-vs-scheduled-recompute race can silently strand stale derived values. Mitigation: single-worker per-user-serialized jobs initially, monotonic invalidation epoch re-checked at commit, as-of/coverage annotations per s99.
- **NOOP fork has no coexistence strategy.** Upstream fixes are the only cure for WHOOP firmware breakage; sync module must sit behind a stable interface for mechanical merges; fork manifest tracks carried upstream commits; license inventory gates LICENSE/NOTICE/ATTRIBUTION.

## Top risks (mitigations in synthesis)

NOOP fork rot; license boundary for server-side decode; vendor-computed scores vs provenance (store as Observations with opaque registered-algorithm provenance); daily feature store needs `(date, feature_set_version)` keying; instrument-change distribution shift; freshness races; auth/pairing owned by no milestone (add to M2 exit criteria or M1.5 + new ADR 0015-device-credentials-and-pairing; bootstrap CLI mints first device token so M1 ships auth-free); backup authority (new ADR 0016-backup-and-restore: Somatriq pg_dump authoritative, Dokploy snapshots secondary, Timescale-aware pre/post hooks); split-origin CSRF (prefer single origin, path-based routing, SameSite=Lax, no CORS); metric dictionary (above); M5 is a scope mountain (split M5a quality engine / M5b features+baselines; fix connector model — NOOP is the mobile collector, Health Connect runs mobile-side, target WHOOP 4.0+5.0).

## Refuted / downgraded (skeptic pass)

- Refuted: "WorkManager cannot deliver 5-minute sync" — s36's "or the appropriate current mechanism" escape clause already permits a foreground-service driver.
- Downgraded: vendor-computed-scores taxonomy break (s31's Observation definition already reconciles it); daily-feature-store blocker (s72 fields are "Example fields"; s62/63 provenance subsumes); rolling-baseline invalidation (s138's "affected derived periods" includes the trailing baseline windows).

## Safe to proceed (no decision required)

CONTEXT.md glossary (with vendor-scores clarification + corrected connector model); ADRs 0001, 0002, 0004, 0007 (with migrate-service mechanism), 0008, 0011, 0013; draft 0014 with single-origin static-export CSR as recommended mode; M0 repo skeleton + compose + CI; Alembic + Timescale hand-written migration template + one-shot migrate service; metric/units catalog v1; M1 tracer scaffolding; Dokploy MCP config + env-var documentation skeleton; NOOP fork research (license inventory, device matrix, SQLite schema, capture-point analysis); golden-test fixture structure (DST dataset per decision 4).

## DECISIONS RECORDED — 2026-09-04 (product owner)

| # | Decision | Choice | Consequence |
|---|----------|--------|-------------|
| 1 | Raw strategy at M2 (B1) | **Raw from day one** (revised 2026-09-04; initially "Accept the gap", changed after review) | M2 transmits raw frames + decoded observations from day one; server runs a minimal `LocalVolumeRawBlobStore` from M2; only replay/reprocessing tooling waits for M4. The NOOP fork adds a pre-decoder capture point (§47 flow) as explicit M2 scope. §51 refined: mobile may prune local raw only after the server ack covers the raw batch. Observations carry `decoder_version` + `raw_batch_id` from the first schema. |
| 2 | MCP client auth (B4) | **OAuth 2.1** | Somatriq hosts authorization/token endpoints and acts as OAuth 2.1 resource server; scoped PATs remain the admin/service fallback; write scopes require an explicit per-connection grant in the web UI (also serves §160's prompt-injection gate). |
| 3 | External LLM egress (B5) | **Local-only by default** | No health data leaves the VPS unless a provider is explicitly opted in, at aggregates level. The same level-keyed filter governs the MCP response serializer — closing the second egress path. |
| 4 | Day semantics | **Wake date + recompute** | A night belongs to the morning of waking; each day stores its effective timezone; timezone change triggers bounded recomputation. Defines DST fixture expectations (§153). |
| 5 | Licensing stance | **Private for now** | Placeholder LICENSE/NOTICE citing inherited NOOP terms; license inventory is an M0 fork-research deliverable; final license ratified before any publication. |
| 6 | Morning brief on partial data | **Emit with marker** | The brief always arrives, marked with data coverage (e.g. "preliminary — 60% coverage"), and is silently recomputed when data lands. |

## Architect resolutions adopted (per synthesis recommendations)

- **B2 — Ingest/sync contract:** batch UUID is the sole idempotency key (content hash stored for forensics, 409 on mismatch); per-record natural key `(user_id, device_id, source_record_id)` unique per hypertable; permanent rejection = HTTP 422 + `accepted:false` + stable error_code, mobile retains `failed_permanent` batches indefinitely (never raw-pruned) and surfaces a persistent sync warning + `ingest.failures` row + notification; sync queue entries are batch-level (reword §39's record-level wording).
- **B3 — Migration ownership:** one-shot `somatriq_migrate` compose service runs `alembic upgrade head` before all others (`condition: service_completed_successfully`); hand-written SQL migrations for hypertables/continuous aggregates with the TimescaleDB extension version pinned; CI tests up+down; "migrate exited 0" added to the §211 checklist.
- **B5 — Privacy enforcement:** single deterministic context-builder/redaction layer between tools and LLMProvider as the only provider-egress chokepoint (provider-aware: local bypasses external restrictions); equivalent level-keyed filter in the MCP serializer; per-level payload-contract table in ADR 0009.
- **Fold-ins from the gap critic (adopted into founding docs):** metric/units catalog (`system.metrics`) created at M1; device lifecycle (active-from/to) + firmware/collector-build provenance + instrument-change re-baselining; observations carry `decoder_version` (+ `raw_batch_id` from M4); freshness semantics = single-worker per-user-serialized jobs with monotonic invalidation epoch and as-of/coverage annotations.
