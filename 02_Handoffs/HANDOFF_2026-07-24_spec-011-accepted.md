# HANDOFF: SPEC-011 accepted

**Date:** 2026-07-24
**From:** Auto (Cursor agent)
**Status:** SPEC-011 promoted `active → accepted`

## What landed

- Resolved SPEC-011 Q1–Q4; section renamed to Resolved questions; `git mv` to `01_Specs/accepted/SPEC-011_lunm-format-invariant-ddl.md`.
- Locked answers:
  - **Q1** — `conversation_turns.session_id` stays convention-only (no FK); Engine writers audit before any future FK.
  - **Q2** — Additive FI columns require SPEC-011 amendment (or dated Appendix A); amendment-first with the Engine change set.
  - **Q3** — FI fingerprint contribution = SHA-256 hex of UTF-8 `\n`-joined `name\tsql` from `sqlite_master` for eight FI tables + Appendix A.7 non-autoindexes, sorted by `name`. No helper exists yet.
  - **Q4** — Implementation notes always pin Engine commit SHA (never “main at date”).
- Appendix A.7 lists the ratified SHOULD indexes for the fingerprint set.

## Engine work (next)

1. Add `tests/unit/test_spec011_fi_columns.py` — fresh `MemoryDatabase` matrix; assert `PRAGMA table_info` column **names** equal Appendix A for each of the eight FI tables.
2. Mirror SPEC-009 CI path (`pytest tests/unit`).
3. Do **not** implement `lunm.schema_fingerprint` stamping in that PR (SPEC-008 follow-up).
4. On merge: fill Implementation notes with PR + SHA; `git mv` → `01_Specs/implemented/`.

## Still open on ledger

- Engine SPEC-011 conformance → `implemented/`
- Cartridge v0.2 limits (Lansing 9.5% / form-feed) — docs verified current; leave open until fixed
- Deferred Looney research / optional Reader `.dmg`
