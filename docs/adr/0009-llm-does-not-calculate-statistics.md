# ADR 0009 — The LLM does not calculate statistics, and health-data egress has one chokepoint

Status: Accepted · Date: 2026-09-04 · Resolves grill blocker B5 (privacy enforcement)

## Context

Statistics belong to deterministic Python (§73-88): the LLM understands questions, selects tools, requests computation, and interprets structured results — it never invents health data, computes over raw arrays, bypasses permissions, writes SQL, or diagnoses (§89). But §92 defined three privacy levels with no per-level content contract, no enforcing component, and no coverage of the MCP server — a second egress path where external AI clients receive health series and `memory.read` text governed only by scopes. The product owner decided: **local-only by default** for external providers.

## Decision

**Division of labor:** all numeric results come from the deterministic tool layer (NumPy/Pandas/SciPy/statsmodels) and reach the LLM as structured outputs carrying coverage, sources, quality, and caveats (§99). The LLM narrates; it never computes.

**Privacy enforcement — one chokepoint, two sites:** a single deterministic context-builder/redaction layer sits between the tools and `LLMProvider` and is the only path to model providers. It is provider-aware: local providers (Ollama) bypass external restrictions; external providers (explicit per-provider opt-in, starting at `aggregates`) receive level-filtered content. The **same level-keyed filter runs inside the MCP response serializer**, closing the second egress path. Default level: **local-only** (no health data leaves the VPS unless a provider is explicitly enabled, at `aggregates`).

**Per-level payload contract:**

| Level | Numeric data | Free text (journal/memory) | Raw samples |
|---|---|---|---|
| `summary_only` | Coarse status words ("above baseline") only | none | never |
| `aggregates` | Daily aggregates and statistics with units | none | never |
| `detailed` | Individual measurements | included | never to external providers |

Evidence labels (`Measured / Calculated / Associated / Predicted / AI interpretation`, §187) are attached structurally by the tool layer, never self-declared by the model.

## Alternatives

- **Privacy level as per-provider config only** — rejected: two config surfaces, still no MCP coverage.
- **Trusting prompts to respect the level** — rejected: prompt-based redaction is not enforcement.

## Consequences

- One component to audit for the entire egress policy; the §159 security review checks it each release.
- Local-model quality is the default experience; external quality is a deliberate, revocable opt-in.
- Prompts are versioned in git (§144); AI runs are traceable with provider/model/token/cost (§143, §145).
