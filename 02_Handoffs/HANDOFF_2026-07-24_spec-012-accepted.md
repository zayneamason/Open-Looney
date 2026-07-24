# HANDOFF: SPEC-012 accepted

**Date:** 2026-07-24
**From:** Auto (Cursor agent)
**Status:** SPEC-012 promoted `active → accepted`

## What landed

- Resolved SPEC-012 Q1–Q6; section renamed to Resolved questions; `git mv` to
  `01_Specs/accepted/SPEC-012_lunm-entity-unification.md`.
- Locked answers:
  - **Q1** — Canonical key is `entities.id` (TEXT slug PK).
  - **Q2** — No `entities.graph_node_id` bridge; coalesce + quarantine.
  - **Q3** — Quarantine DDL frozen in §4.3; 90-day resolved retention; no FKs.
  - **Q4** — Flag set: `LUNA_ENTITY_CANONICAL_ID=0`,
    `LUNA_ENTITY_UNKNOWN_DEFAULT=0→1`, `LUNA_IDENTITY_PROMPT_DIET=0→1`,
    `LUNA_ENTITY_SALIENCE_RANK=0`, `LUNA_OBS_ENTITY_DTO=0`.
  - **Q5** — DTO owners: `entities/dto.py`, observatory routes/MCP tools,
    `EntitiesView` / `ThreadsView`.
  - **Q6** — Unknown-default probe corpus classes named in Resolved questions.
- N-way name matches are quarantine-only unless an allowlist names the survivor.
- Wiki pass P4 / `v0.5.0` (MINOR — lifecycle advance).

## Engine work (next)

1. WP0 canonical identity behind `LUNA_ENTITY_CANONICAL_ID` (schema_apply,
   quarantine table, EntityLifecycle, coalesce script, Engine
   `lunm_table_manifest.toml`).
2. Then unknown default → prompt diet → salience → Observatory DTO →
   maintenance live-lock per SPEC-012 §4.4.
3. On Engine implementation merge: fill Implementation notes with PR + SHA;
   `git mv` → `01_Specs/implemented/`.

## Still open on ledger

- Engine SPEC-012 WP0+ → `implemented/`
- Deferred Looney research / optional Reader `.dmg`
