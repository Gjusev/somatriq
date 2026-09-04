# ADR 0015 — Device credentials and pairing

Status: Accepted · Date: 2026-09-04 · Fills the milestone/ADR gap found by the grill

## Context

Pairing (§43), device scopes (§44), secret storage (§45), the local account (§122), and the first-run wizard (§180) were owned by no milestone and no founding ADR — yet M2's acceptance criterion (a real WHOOP sample reaching the server automatically) and M3's offline gate are untestable without issued device credentials.

## Decision

**Bootstrap CLI:** a `somatriq` admin command mints the first device token directly, so M1's tracer bullet ships auth-free against synthetic data while using the real credential path. **Pairing (M2 exit criteria):** the web app creates a short-lived pairing session rendered as QR or manual code; the Collector exchanges it for device-specific credentials with minimal scopes — `ingest.write`, `device.read`, `sync.read` — never admin, arbitrary health reads, other-user access, or database access. Server-side, device tokens are stored hashed with name, scopes, created/last-used/expiry/revoked (§123); Android-side they live in the Android secure keystore mechanism; credentials never appear in logs or crash reports (§45). **Account (M2):** the initial local account (Argon2id) and the pairing API are part of M2's exit criteria — discovered mid-milestone is how the §196 hard gate gets destabilized. The first-run wizard (§180) sequences these explicitly.

## Alternatives

- **Long-lived API key shared with the web session** — rejected: no scope separation, no revocation path, violates §44.
- **Deferring pairing to M4+** — rejected: M2/M3 acceptance criteria presuppose it.

## Consequences

- Token issuance/revocation and pairing sessions land in the audit log (§124).
- M1 runs auth-free via the bootstrap CLI without inventing a parallel auth path — the CLI mints the same token type.
- Device token compromise is bounded by minimal scopes and revocation.
