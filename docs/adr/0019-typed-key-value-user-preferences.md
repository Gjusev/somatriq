# ADR 0019 — Typed key-value user preferences

Status: Accepted · Date: 2026-09-09 · Grill session `docs/plans/2026-09-09-product-blocks.md` (P5/P5b — owner's choice overriding the env-var default)

## Context

Block 1 needs a target wake time (Today Plan's bedtime anchor); Block 2 needs a journal reminder time. Both are declared personal intentions — the glossary's **User Preference** — and env vars (`USER_TIMEZONE` pattern) would put a personal setting in deployment config, uneditable by the person it belongs to.

## Decision

**`identity.user_preferences (user_id, key, value jsonb, updated_at)`** — one typed key-value store for all preferences. Validation lives at the API edge: each key registers a pydantic schema; unknown keys or invalid values return 422 with the registered shape. The database stores shapeless jsonb and enforces nothing beyond the primary key.

## Alternatives

- **Columns on `identity.users`** — rejected: one column per future preference; schema churn per setting.
- **Per-feature preference tables** — rejected: a table per setting for a single-user deployment.
- **Env vars** — rejected (was the plan's default): deployment config is the operator's surface, not the user's; changing a wake time would require a redeploy.

## Consequences

- Preferences declare intention; they are never derived from behavior (glossary: User Preference).
- Consumers must treat a missing key as "unset" and carry a documented default (e.g. `wake_time` → 07:00 local), recording whether preference or default was used.
