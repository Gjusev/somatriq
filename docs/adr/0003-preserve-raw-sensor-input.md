# ADR 0003 — Preserve raw sensor input from day one

Status: Accepted · Date: 2026-09-04 · Amends spec §51 (raw-ack required before mobile prune)

## Context

"Raw before interpretation" (§2), "raw evidence can be reprocessed" (§220), and "do not discard original source measurements" (§221) are non-negotiable. The grill confirmed (blocker B1) that shipping M2 sync observations-only while raw archival waits for M4, combined with §51's 7-day phone prune, permanently destroys the M2→M3 window's raw frames. NOOP has no pre-decoder capture point, so the fork must add one (§47 flow). Observations also had no decoder provenance, which would make the archive write-only.

## Decision

**M2 transmits raw frames and decoded observations together, from day one.** The server runs a minimal `LocalVolumeRawBlobStore` (volume-backed blob writer, §48-49 layout) beginning at M2; only replay/reprocessing tooling waits for M4. The NOOP fork gains a pre-decoder capture point (complete reconstructed frame → raw archive, then decode) as explicit M2 scope. Observation rows carry `decoder_version` and `raw_batch_id` provenance from the first schema (M1), so every observation is retraceable to its raw batch and decoder. Mobile raw pruning (§51) is refined: it is authorized only by a **raw-ack** — a server acknowledgement that explicitly covers the raw batch — never by an observations-only ack; `failed_permanent` batches are never pruned.

## Alternatives

- **Observations-only at M2, defer phone deletion until M4 acks raw** — rejected: unbounded phone storage for two milestones plus retro-upload machinery.
- **Accept the gap** (documented carve-out for M2→M3) — initially chosen, then reversed on review: it is a one-way door; early months are precisely when decoder improvements are most likely, and the data can never be recovered.

## Consequences

- M2 scope grows: fork capture-point work plus a simple server blob writer; modest bandwidth/storage cost (zstd-compressed frames).
- From M4, replay can re-decode archived history into parallel observation versions without overwriting originals (ADR 0012).
- Server-side replay decoding requires NOOP license clearance or a clean-room decoder (ADR 0004) — decided at M4, not now.
