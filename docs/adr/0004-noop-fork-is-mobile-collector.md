# ADR 0004 — The NOOP fork is the mobile collector, not a connector

Status: Accepted · Date: 2026-09-04

## Context

Somatriq Mobile is a real fork of NOOP (spec §29, §33), maintained with an upstream remote because WHOOP firmware changes will break BLE decoding and only upstream NOOP fixes that. The grill found two model errors in the spec: NOOP was listed as a Connector of itself (§126), and Health Connect was planned server-side where it cannot run; NOOP targets WHOOP 4.0 and 5.0 (spec says only "WHOOP 4"). The license terms inherited from NOOP have never been inventoried (§162), and "never copy NOOP code into the server repository" (§29) has unexamined consequences for M4 server-side replay decoding.

## Decision

Terminology (see `CONTEXT.md`): the fork is the **Collector** — it owns BLE, local persistence, decoding, and sync upload. **Connectors** are server-side integrations (Huawei, Withings, Garmin, Oura, CSV). Health Connect data flows through the Collector on the phone. Device targets are WHOOP 4.0 and 5.0, following actual NOOP support. The fork keeps: upstream remote, a **fork manifest** recording which upstream commits are carried, and the sync module (§35) behind a stable internal interface so upstream merges stay mechanical; the pre-decoder capture point (ADR 0003) lands as an explicitly isolated patch. Decoding stays mobile-only until the M0 license inventory clears a server-side port; if not cleared, server replay uses a clean-room decoder reimplemented from protocol observation. Both repositories remain private with placeholder LICENSE/NOTICE citing inherited terms; the final license is ratified before any publication.

## Alternatives

- **Rewrite the collector from scratch** — rejected: discards the only working open WHOOP BLE stack.
- **Vendor NOOP as a library** — rejected: it is an application, not a library; fork is the supported shape.
- **Publish immediately under noncommercial terms** — rejected for now: the inherited-license inventory does not exist yet.

## Consequences

- M0 includes NOOP fork research: license inventory, device support matrix, SQLite schema analysis, capture-point insertion analysis.
- Upstream merge cadence is an explicit maintenance duty, not an aspiration.
- The server never depends on fork code; the raw archive (ADR 0003) is the boundary that keeps replay possible even if the fork diverges.
