# HANDOFF: SPEC-009 and SPEC-010 accepted

**Date:** 2026-07-23
**From:** Auto (Cursor agent)
**Status:** SPEC-009 and SPEC-010 promoted `active → accepted`

## What landed

- Resolved SPEC-009 Q1–Q5 and SPEC-010 Q1–Q6; section renamed to Resolved questions; both `git mv`'d to `01_Specs/accepted/`.
- Human defaults locked from live-matrix evidence:
  - `migrations/002` → historical/superseded (objects present; DDL in `schema.sql`)
  - `migrations/003` → dead; schedule Engine delete (`access_bridge` / `permission_log` absent)
  - LUNM entity family owner → `luna.substrate` / `schema.sql`; `aibrarian_schema.entities` is cartridge-only (name collision)

## Engine work (not this repo)

Manifest TOML/YAML, §4.4 conformance test, migration fail-loud rollout, AST lint, fold `004`, delete `003` — all post-acceptance in Luna Engine.

## Still gated

SPEC-008 → `implemented/` + LUN-FORMAT LUNM line-4 repoint wait on Engine PR merge (landing-ready 2026-07-23 per ledger).

## Parallel hygiene (same session)

See ledger: README v0.3 Shipping, `10_Builder/STALE.md`, Reader baselines + visual 18–19, S-01 → expected policy 149/166.
