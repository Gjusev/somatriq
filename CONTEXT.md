# CONTEXT — Somatriq Domain Glossary

Status: living document — the shared domain language.

Every term has exactly one meaning. Modules must not invent conflicting names. When terminology changes, update this file in the same change (spec §3, §31).

Decision log: `docs/grill/2026-09-04-spec-grill.md` · Architecture history: `docs/adr/` · Master spec: `docs/SPEC.md`

---

## Identity & Sources

**User** — the person whose data Somatriq holds. Initial deployment is one person; IDs are designed so multiple users are possible later without multi-tenant complexity.
**User Preference** — a single declared personal setting (e.g. target wake time), stored per user and validated against a typed schema per key. Declares intention; never derived from behavior.

**Device** — a physical instrument (e.g. one specific WHOOP 4 band) with an `active_from`/`active_to` lifecycle. A device replacement is an *instrument boundary* (see Baseline).

**Data Source** — the provenance origin of an observation: device + provider + collector (e.g. "WHOOP via Somatriq Mobile").

**Collector** — the mobile application that captures from hardware (the NOOP fork). The Collector owns BLE, local persistence, decoding, and sync upload. The Collector is **not** a Connector.

**Connector** — a server-side integration with an external health ecosystem (Huawei, Withings, Garmin, Oura, CSV import). Health Connect data arrives mobile-side through the Collector, not through a server Connector.

**Canonical Source** — the source currently selected to represent a metric when multiple sources overlap. Resolution is deterministic (metric, time-scoped priority, coverage, quality, user preference) and changing it is an invalidation trigger.

## Data layers (never collapsed)

**Raw Frame** — reconstructed BLE protocol evidence before interpretation. Immutable, compressed (zstd), archived forever by default.

**Raw Archive** — the blob store of raw batches (`LocalVolumeRawBlobStore` initially; S3-compatible later). PostgreSQL stores path, hash, size, compression, schema version, device, time range, record count.

**Decoder** — the code that transforms raw frames into observations. Versioned (`decoder_version`). Decoding runs on the phone initially; server-side replay requires license clearance (ADR 0004).

**Observation** — a source-provided measurement associated with a specific time or period (heart rate sample, RR interval, SpO2, battery…). **Vendor-computed scores (WHOOP HRV/recovery/strain/sleep scores) are Observations**: they are source-reported measurements with opaque registered-algorithm provenance, not Somatriq Derived Metrics.

**Derived Metric** — a value calculated from one or more observations by a versioned Somatriq algorithm.

**Metric** — any named quantity in the Metric Catalog.

**Metric Catalog** — `system.metrics`: the single data dictionary (canonical names, units, physiological valid ranges, expected cadence, valid aggregations) that ingest validation, connectors, the quality engine, charts, and MCP responses all import. Never re-implemented per subsystem.

**Provenance** — the full traceability record of a value: inputs, source device + firmware + collector build, algorithm name/version/parameters, quality, `calculated_at`.

**Algorithm Version** — an immutable semantics identifier (`somatriq_recovery_v1`). Historical calculation semantics are never silently overwritten; versions run in parallel for comparison.

## Sync & ingestion

**Batch** — the unit of mobile→server transfer and of the sync queue. Queue entries are batch-level. A batch carries a batch UUID, a protocol schema version, and payload(s) (raw + observations).

**Idempotency** — the batch UUID is the sole idempotency key. The content hash is stored for forensics; a hash mismatch on a known UUID returns 409.

**Natural Key** — per-record identity: `(user_id, device_id, source_record_id)`, unique per hypertable. Distinct `source_record_id` = distinct row, even at identical timestamps.

**Sync Queue states** — `pending` → `uploading` → `acknowledged`; `failed_retryable` (exponential backoff with jitter); `failed_permanent` (retained on device indefinitely, never raw-pruned; surfaced as a persistent sync warning and an `ingest.failures` row).

**Raw-ack** — a server acknowledgement that explicitly covers the raw batch. The only event that authorizes mobile raw pruning (7 days later). An observations-only ack never authorizes raw deletion.

## Time

**Day (local day)** — the user's calendar day in that day's effective timezone. All daily features, baselines, and analyses key on local days.

**Night** — a sleep period, attributed to the **wake date** (the morning of waking). "Last night's sleep" always belongs to today.

**Effective Timezone** — the timezone stored on each daily row. A timezone change triggers bounded recomputation of affected history via the reprocessing tooling.

All timestamps are stored in UTC (timezone-aware). Source timezone is preserved when semantically relevant.

## Journal

**Journal Event** — a quick-logged user event with an occurrence time, an optional structured payload, and verbatim text for provenance. A user input, never an Observation.

**Behavior** — a journalable exposure with a closed vocabulary (caffeine, alcohol, medication, stress, meal, travel). Binary for analysis; quantities are recorded only when literally stated, never invented.
**Annotation** — a user-authored note anchored to a date or date range, for marking periods on longitudinal views. Distinct from a Journal Event: it labels time; it does not log an exposure. System facts (device change, algorithm version) are computed overlays, never annotations.

## Quality & analysis

**Coverage** — the proportion of expected data available for a requested interval.

**Data Quality** — the assessment of validity, completeness, and consistency. Distinct from **Statistical Confidence** (a regression can be statistically confident on poor source data; both are represented).

**Baseline** — a robust personal reference (7/28/90-day windows; median, MAD, EWMA, percentiles) with warmup states `collecting` / `early` / `established`. Baselines are re-baselined at instrument boundaries (device change).

**Feature Set** — the coherent daily feature representation, versioned as `feature_set_version`. Experiments and predictions pin a features version.

**Vital** — a slow-moving physiological quantity monitored against the personal baseline (HRV, resting HR, respiratory rate, SpO₂, skin-temperature deviation). Deviations are phrased vs the personal baseline, never as clinical abnormality.
**Insight** — a deterministic, human-meaningful candidate finding. Ranking (importance, novelty, confidence, actionability) and AI-written explanation are presentation layers some surfaces add; AI text is always traceable to the deterministic analysis that produced it.
**Behavior Insight** — an Insight reporting the association between one day's Behavior exposure and the next day's outcome: group sizes, effect, uncertainty, confounders, and method. Evidence label `Associated`; never causal language.
**Sleep Need** — a prescriptive, explainable recommendation of tonight's sleep duration derived from personal baselines and recent state. A Derived Metric (`Calculated`), never presented as a Prediction; a later version may be scored against actual sleep and promoted to one.
**Sleep Debt** — the capped shortfall of measured sleep vs the personal sleep baseline over a rolling short window. Asymmetric: oversleeping never banks credit; always shown with its window and cap.
**Today Plan** — the day's prescriptive bundle (training guidance, target-strain range, bedtime window), each element carrying its own contributions and caveats. Always present; degrades visibly with data gaps, never disappears.
**Training Guidance** — a tiered same-day training recommendation (`rest` / `light` / `moderate` / `hard`) derived from recovery and recent state, with an optional target range anchored in the personal recent strain distribution. Guidance, never medical advice.

**Experiment / Intervention / Checkin / Compliance** — N-of-1 research entities. Adherence is recorded `yes`/`no`/`unknown`, never assumed.

## AI surfaces

**Provider** — an LLM backend. Local (Ollama) is the default; external providers are enabled per provider by explicit opt-in.

**Privacy Level** — `summary_only` / `aggregates` / `detailed`. Enforced at a single chokepoint: the deterministic context-builder/redaction layer between tools and LLMProvider (provider-aware: local providers bypass external restrictions), and the same level-keyed filter inside the MCP response serializer.

**MCP** — Somatriq's domain-scoped Model Context Protocol server. OAuth 2.1 resource server; `health.read` by default; write scopes require an explicit per-connection grant.

**Evidence labels** — `Measured` / `Calculated` / `Associated` / `Predicted` / `AI interpretation`, attached structurally, never self-declared by the model.

## Operations

**Reprocessing** — bounded recomputation of derived data, selectable by user, date range, metric, source, and algorithm version. Never a full blind recompute.

**Invalidation** — dependency-driven marking of affected derived periods when inputs arrive late or change. Uses a monotonic epoch re-checked at commit so invalidations landing during a recompute are never consumed silently.

**Morning Brief** — the daily brief. Always emitted; when overnight data is incomplete it is marked `preliminary — N% coverage` and silently recomputed when the data lands.
