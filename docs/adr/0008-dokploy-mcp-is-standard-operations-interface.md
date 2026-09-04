# ADR 0008 — Dokploy MCP is the standard operations interface

Status: Accepted · Date: 2026-09-04

## Context

The development agent must operate Dokploy through the official `@dokploy/mcp` rather than instructing the user to click around (spec §21-23), while infrastructure-as-code remains in git (§24). Destructive operations are a real risk on a single-VPS personal deployment.

## Decision

Infrastructure operations go through the official Dokploy MCP configured with `DOKPLOY_TOOL_PRESET=deploy` (project, environment, server, application, compose, domain, deployment categories); tool categories expand only when a task requires them. Every operation is classified before execution — **READ** (inspect freely), **REVERSIBLE WRITE** (proceed with verified target identifiers resolved via MCP, never inferred from names), **DESTRUCTIVE WRITE** (delete application/database/volume/domain/project, restore over production, rotate credentials, remove backup): these require inspecting current state, describing the intended change, confirming the target identifier, ensuring a backup when appropriate, and explicit user authorization. Deployment follows the §23 workflow (inspect → deploy → status → logs → health → endpoint verification → privacy verification per §211). Git remains the source of truth for all configuration; Dokploy only holds deployment state.

## Alternatives

- **Manual Dokploy UI operation** — rejected: unobservable, unreproducible, and forbidden by §221 when the MCP can do it.
- **Raw Dokploy REST calls** — rejected: bypasses the tool safety layer; the MCP wraps the same API.

## Consequences

- API keys never enter the repository (§210); `.env.example` documents variables only.
- Post-deploy verification (§211) is part of every deployment, not an occasional audit.
- Deployment actions are recorded in the audit log where available (§124).
