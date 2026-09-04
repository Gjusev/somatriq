# AGENTS.md — Mandatory Engineering Behavior

Any development agent (human or AI) working on Somatriq follows these rules. The master specification is `docs/SPEC.md`; the domain language is `CONTEXT.md`.

## Before you touch anything

- Read `CONTEXT.md` before domain changes. Do not invent new names for existing concepts.
- Read the relevant ADRs in `docs/adr/` before architectural changes. Never silently contradict an ADR — supersede it (new ADR, explain why, identify migration impact).
- Check `docs/grill/2026-09-04-spec-grill.md` ("DECISIONS RECORDED") for the six product decisions and three architect resolutions that amend the spec.

## Process

- Use TDD (RED → GREEN → REFACTOR) for all deterministic behavior. The list of mandatory-TDD areas is spec §8. One vertical behavior slice at a time, never twenty speculative tests.
- Use the grilling skill (grill-with-docs equivalent) before irreversible design changes; output feeds `CONTEXT.md` and `docs/adr/`.
- Use domain-modeling for new domain concepts; run the entity questions in spec §6 before adding any table.
- Use research skills for external protocol/platform assumptions (WHOOP, Android, Health Connect, Dokploy, TimescaleDB, MCP); prefer official docs and upstream source; record durable findings under `docs/research/`.
- Use diagnosing-bugs before any nontrivial fix: reproduce → minimize → hypothesize → instrument → root cause → failing regression test → fix → verify → remove instrumentation.
- Code-review every substantial feature on two independent axes: SPEC COMPLIANCE and ENGINEERING QUALITY (spec §11).
- Use design-taste-frontend before and after any major UI surface; the design review is independent of the functional review (spec §14, §17). Somatriq's visual register is calm, precise, scientific (spec §15) — restraint beats decoration.
- Load only the skills relevant to the current task (spec §214).

## Hard rules

- Never expose PostgreSQL publicly. It stays on the private Docker network (ADR 0011).
- Never give normal MCP clients arbitrary SQL (ADR 0010).
- Never let an LLM perform statistical calculations that Python performs deterministically (ADR 0009).
- Never make remote connectivity necessary for mobile collection (ADR 0005).
- Never allow sync retries to create duplicates (ADR 0006).
- Never silently overwrite algorithm semantics (ADR 0012).
- Never discard original source measurements (ADR 0013).
- Never hide data quality problems (spec §158, §221).
- Never modify production destructively without explicit authorization. Classify Dokploy operations READ / REVERSIBLE / DESTRUCTIVE before executing (spec §22).
- Never operate Dokploy manually when the official MCP can safely perform or inspect the task (ADR 0008).

## Workflow

- Conventional commits (spec §189): `feat(ingest): add idempotent batch endpoint`.
- Small coherent commits; each leaves the repository usable when practical (spec §188).
- A PR explains problem, solution, architecture/data-migration/security impact, testing, rollback (spec §190).
- Documentation is part of done: README, OpenAPI, ADR, CONTEXT, runbook, migration notes — whichever the feature touches (spec §191, §192).
- Before calling a milestone complete, run the self-review checklist in spec §215.
