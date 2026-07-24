# HANDOFF: SPEC-012 entity unification drafted

**Date:** 2026-07-24
**From:** Codex
**Status:** SPEC-012 drafted in `active/`

## What landed

- New active spec: `01_Specs/active/SPEC-012_lunm-entity-unification.md`.
- Scope locked as the full LUNM entity-unification architecture, not WP0-only.
- The spec keeps LUNM as the runtime matrix family and explicitly rejects an entity sidecar cartridge.
- LUNM entity tables remain `engine-extension` under SPEC-009; no additive entity work bumps `user_version` unless it changes a SPEC-008 contract.

## Acceptance blockers

- Verify the live Luna Engine `entities` DDL and name the exact canonical row key.
- Decide whether a temporary `entities.graph_node_id` bridge is required.
- Define any quarantine DDL exactly.
- Re-audit Engine route/component names before locking the Observatory DTO implementation.

## Next implementation arc

- Run a read-only Luna Engine audit of current entity DDL, graph `ENTITY` node creation, thread rosters, prompt identity buffer, Observatory routes, and maintenance scripts.
- Promote SPEC-012 `active → accepted` only after those open questions are resolved.
- Implement in the locked order: canonical identity, unknown default, prompt diet, mentions/salience, Observatory DTO, maintenance safety, later spans/facts.
