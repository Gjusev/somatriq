# NOOP Fork Research — license inventory, platform matrix, capture-point analysis

Status: complete · Date: 2026-09-04 · Feeds ADR 0003, ADR 0004, decision 5 (licensing), M2 planning.

Sources: `ryanbr/noop` (README, LICENSE, NOTICE, docs/ANDROID.md, docs/CROSS_PLATFORM.md, docs/DATA_MODEL.md, docs/PROTOCOL.md), `muftiarfan/noop` metadata. Fork created: **https://github.com/Gjusev/noop** (of ryanbr/noop, branch `main`).

## 1. Lineage

- **Upstream-of-record: `ryanbr/noop`** (789★, pushed daily — the active line). It is itself a fork of `muftiarfan/noop` (83★, dormant since 2026-06). Our fork tracks **ryanbr/noop** as upstream remote; `muftiarfan/noop` is recorded as the origin source.
- App identity: "Strand" (Swift, macOS reference + iOS folded-in) + native Android client under `android/`.

## 2. License inventory (decision 5: "private for now" — validated)

- **License: PolyForm Noncommercial 1.0.0** ("Other" on GitHub badge). Copyright 2026 NoopApp.
- **Scope clause (decisive for us):** the license "applies ONLY to NOOP's own original source code and documentation." The **protocol facts documented in the repository — BLE service/characteristic UUIDs, frame layouts, CRC parameters, command/event/packet numbers, byte offsets — are declared uncopyrightable factual information, reusable freely** (see NOOP's DISCLAIMER.md / ATTRIBUTION.md).
  → **Server-side replay decoding (ADR 0004's open question) is cleared**: Somatriq may implement a decoder from the documented protocol facts + the bundled `whoop_protocol.json` decode tables without inheriting PolyForm-NC on the server repo. NOOP *code* still must never be copied into the server repo (spec §29) — facts yes, code no.
- Third-party components in NOOP keep their own licenses (GRDB.swift, ZIPFoundation: MIT) — see NOOP's NOTICE.
- **Consequences for us:** the fork (Gjusev/noop) carries PolyForm-NC for NOOP-derived code; our sync module additions in the fork must be usable under the same noncommercial terms (private use = unproblematic). Server repo stays unencumbered. Publication later requires honoring PolyForm-NC attribution in any fork-derived distribution.

## 3. Platform matrix

| Platform | Language/UI | Status |
|---|---|---|
| macOS | Swift + SwiftUI/AppKit (`Strand/` + 5 SwiftPM packages) | reference implementation |
| iOS | Swift (build-from-source, folded into main project) | shares nearly all macOS source |
| **Android** (`android/`) | **Kotlin + Jetpack Compose, Room (SQLite), full Gradle project** | **shipping**: full + demo APK flavors, sideloaded; BLE pipeline, Compose UI, Room DB, CSV/Apple-Health importers present; **validated on WHOOP 4.0**; live HR validated on WHOOP 5.0/MG — deeper 5.0 scores still being reverse-engineered |

Android is a **hand-ported parity twin** of the Swift logic (no KMP, no shared binary) — the spec's §33/§110 Android/Kotlin premise holds. NOOP's own cross-platform doc warns about drift between the twins; our fork must track upstream Android changes mechanically (ADR 0004's fork-manifest discipline).

Devices: **WHOOP 4.0 (validated end-to-end) and 5.0/MG (live HR validated; deep scores in progress)** — matches ADR 0004's target.

## 4. On-device data model (sync-module input)

- Single SQLite DB (`whoop.sqlite`), WAL mode, GRDB on Swift / **Room on Android**.
- Persistence package is UI-framework-free (`WhoopStore`); tables carry natural keys + indexes + a documented migration history (docs/DATA_MODEL.md — full column inventory to be extracted when the fork is cloned).
- Sync implication: our SyncRepository reads Room tables and the raw-frame store; nothing in NOOP's schema needs to change for sync — additive module only.

## 5. Protocol & capture-point analysis (ADR 0003)

- The decoder is **platform-pure** (`WhoopProtocol` Swift package; CoreBluetooth-free, test/CLI-runnable). On Android the same decode exists as the hand-ported twin. Canonical decode tables ship as **JSON**: `Packages/WhoopProtocol/Sources/WhoopProtocol/Resources/whoop_protocol.json`.
- The transport→decode boundary is a **clean seam on every platform**: reconstructed complete frames arrive from the transport layer and are handed to the protocol decoder. **The pre-decoder capture point required by ADR 0003 lands exactly there** in the Android fork: intercept the complete frame, append to a raw-frame journal (zstd-compressed, batched), then pass to the unchanged decoder. NOOP's architecture (transport strictly separated from protocol) means this is an isolated patch, mergeable upstream-to-ours without conflicts in the decoder itself.
- Credit chain to preserve: WHOOP 4.0 protocol work builds on `johnmiddleton12/my-whoop`; 5.0/MG "puffin" framing on `b-nnett/goose`.

## 6. Fork strategy (operational)

1. Upstream remote: `ryanbr/noop`. Fork: `Gjusev/noop` (created 2026-09-04, public-by-default as GitHub forks must be — acceptable: fork content is already public upstream; our sync module will be developed on a branch and the private/public posture reviewed before it carries anything personal).
2. Sync module lands as an isolated Android module (`sync/` gradle module, no edits inside BLE/protocol classes — spec §35) + a fork manifest (`FORK.md`) listing carried upstream commits and our patches.
3. Re-merge cadence: weekly-ish, mechanical, driven by the fork manifest.
4. Band version determines expectations: on 5.0/MG, deep scores from the strap are still being RE'd upstream — our Observations-based ingestion is unaffected (vendor scores simply arrive when NOOP can decode them).

## 7. Open items for M2 kickoff

- Confirm the user's phone platform (Android assumed per spec §33/§110) and band (4.0 vs 5.0/MG).
- Clone the fork locally and extract the full Room schema (table/column inventory) into this research dir as `noop-android-schema.md`.
- Design the batch payload v1 (raw frames + observations) against ADR 0003/0006 wire contracts (`packages/somatriq_contracts` already carries the server side).
