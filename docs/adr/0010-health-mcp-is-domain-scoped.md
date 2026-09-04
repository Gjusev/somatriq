# ADR 0010 — The health MCP is domain-scoped, OAuth 2.1-authenticated

Status: Accepted · Date: 2026-09-04 · Resolves grill blocker B4 (MCP authentication)

## Context

Somatriq exposes its own MCP server (FastMCP over HTTPS) rather than PostgreSQL (§95-96). "Authenticated HTTPS" had no mechanism, yet §211 verifies an authenticated endpoint, §97 defaults desktop clients to `health.read` while §98 ships write tools with no grant flow anywhere, and §160's prompt-injection defense needs an approval gate. The product owner decided: **OAuth 2.1**.

## Decision

**Authentication:** Somatriq hosts OAuth 2.1 authorization and token endpoints and acts as a **resource server** for MCP clients (the MCP-spec direction used by clients like Claude Desktop). Scoped **personal access tokens** (§123) remain the credential type for admin/service access and header-capable clients, on the same enforcement path. The chain is always: credential → scopes → per-connection grant record → FastMCP auth hook.

**Authorization:** default connection scope is `health.read` only. Write tools (`journal_add`, `experiment_create`, `experiment_checkin`) and `memory.*` scopes activate **only after an explicit per-connection grant action in the web UI** — this is also the §160 prompt-injection gate: imported health text and journals are untrusted content, tool permissions are fixed server-side, and the model cannot expand its own scopes.

**Contract:** every tool returns the §99 envelope — `data`, `coverage`, `sources`, `quality`, `caveats`, `generated_at` — and the same privacy-level filter as ADR 0009 applies to what external clients receive. There is **no `execute_sql` tool**; any future administrative query tool is admin-scoped, disabled by default, read-only, and fully audited (§100).

## Alternatives

- **PAT-only** — rejected as primary: excludes OAuth-only clients; kept as fallback credential type.
- **Proxy-level auth (forward-auth/mTLS)** — rejected: no per-scope granularity, weakest compatibility.

## Consequences

- Somatriq owns real OAuth endpoints on a public URL holding full health history — they get explicit §159 security-review attention (rate limiting, lockout, secure headers).
- MCP writes land in the audit log (§124); MCP responses are privacy-filtered and caveat-aware.
- Desktop access is read-only by default; every expansion of trust is a visible user action.
