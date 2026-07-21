# HANDOFF: SPEC-008 accepted, SPEC-009 and SPEC-010 drafted

**Date:** 2026-07-21
**From:** Claude Code (Opus 4.8)
**To:** Next session
**Purpose:** Resolve SPEC-008's five open questions against the live engine, promote it to `accepted`, and draft the two LUNM specs it hands down. Pick up from SPEC-009/SPEC-010 question resolution.

**Execution status:** Complete and merged. `main` is at `a351b31`, pushed to `origin`. Five commits. Nothing is left uncommitted.

---

## Current state

`01_Specs/accepted/SPEC-008_lunm-family-foundation.md` — **accepted 2026-07-21**, all five questions resolved. Blocked on four engine changes before `implemented/`.

`01_Specs/active/SPEC-009_lunm-schema-ownership.md` — **drafted**, 5 open questions.
`01_Specs/active/SPEC-010_lunm-migration-discipline.md` — **drafted**, 6 open questions.

`ProjectManager/TODO_LUN_Development_2026-07-20.md` is the canonical ledger and is now tracked in git. 9 done, 27 open.

Branches `spec/spec-008-question-resolutions` and `spec/spec-009-010-lunm-schema-and-migration` still exist locally and on origin. Both are fully merged into `main` and can be deleted.

---

## Repos and fixed paths

Spec repo (this one). **Note:** handoffs before 2026-07 cite `Research/Code for .lun Development`. That path is stale; the repo moved:

```bash
/Users/zayneamason/_HeyLuna_BETA/Apps/lun\ Development
```

Engine repo — implementation target, and the source of truth for every claim this repo makes about code. `00_README/README.md` says implementation "lives in the main Luna codebase" but never gives the path:

```bash
/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root
```

Live matrix used for every count in SPEC-008/009/010 (`application_id = 1280659021`, `user_version = 2`, 89 tables):

```bash
$ENGINE/data/user/memory_matrix.lun
```

Engine HEAD at the time of this work: **`2ed07b0`**. SPEC-008's body was originally drafted against `432e2e9`, 580 commits earlier.

---

## What was decided

SPEC-008's five questions, each verified against shipped code rather than accepted as drafted. **Four of the five recommendations were wrong in some material way** — the decisions mostly survived, their stated reasons did not.

| Q | Decision | What did not survive |
| --- | --- | --- |
| Q1 | `profile_config` under a reserved `lunm.*` namespace | All three cost arguments. It is absent from `schema.sql`, holds 0 rows in every live matrix, and is deletable via one authenticated HTTP call |
| Q2 | Four keys; `profile_ulid` → **`lunm.matrix_ulid`**, file-scoped | The key name and its scope. The genesis hook fires per file; no profile ULID exists in the engine |
| Q3 | `MUST`, unqualified | The `--unsafe-skip-family-check` carve-out — zero precedent repo-wide. The MUST itself was already shipped |
| Q4 | Contract-affecting changes only | "Stricter than LUNC" — LUNC already practices this. Policy (a) had been nominally in force and violated 25 consecutive times |
| Q5 | Premise withdrawn | Everything. `ih_events` is live, dating to three weeks *before* the journal that called it unbuilt |

SPEC-009 was **rescoped during drafting**. The ledger scoped it as full DDL ratification; sizing showed 24 files declare `CREATE TABLE` against the matrix, not the ~6 SPEC-008 assumed. It now ratifies no DDL — it establishes ownership, a static manifest, a four-way classification, and one conformance test. Per-family DDL ratification defers to SPEC-011+.

---

## Next, in dependency order

1. **Resolve SPEC-009 Q1–Q5 and SPEC-010 Q1–Q6.** Same loop SPEC-008 just went through. Every question carries a recommendation; verify each against the engine before accepting it, because that is exactly where SPEC-008's four defects came from.
2. **Promote both to `accepted`** once resolved. See § House conventions.
3. **The four engine changes gating SPEC-008 → `implemented/`.** These live in the engine repo, not here: relocate `profile_config` DDL into `schema.sql`; reserve the `lunm.` prefix on `PUT`/`DELETE /api/profile/config`; add `_seed_lunm_header()`; close the IH matrix-creation gap.
4. **Build the § 4.4 identity check** — highest-value follow-up SPEC-008 generates. Cross-profile conflation is currently undetectable.
5. **Then** repoint `03_Format_Spec/LUN-FORMAT_v0.{1,2,3}.md` at SPEC-008 and move it to `implemented/`. SPEC-008 § Dependencies puts this at `implemented/`, **not** at acceptance — the ledger previously said "after acceptance" and was wrong.

Independent of all of the above: `00_README/README.md` still describes v0.2 as current while the v0.3 format spec says v0.3 is Shipping. `10_Builder/` is still an unclassified stale snapshot.

---

## Needs the human, not more analysis

- **`migrations/002_conversation_history.sql` and `003_access_bridge.sql`** declare 5 tables between them and have **zero loaders** anywhere in the tree. Whether they ever ran against a production matrix is not recoverable from code. Deleting DDL that silently shaped a live file is not a decision to make from the source alone. (SPEC-009 Q5.)
- **The `entities` / `entity_*` ownership split** — `entities` is declared in `substrate/aibrarian_schema.py`, its siblings in `substrate/database.py`. SPEC-009 requires one owner be chosen; it does not choose.
- **Whether SPEC-009's rescope is acceptable** versus the full-ratification scope the ledger originally called for.

---

## Traps

**Engine citations go stale fast.** Every `database.py` line number in SPEC-008's original body had drifted. Re-verify before citing; prefer symbol names over line numbers. Anything citing `database.py:141–161`, `:412–439`, `schema.sql:441`, or `ReaderPrototype/SPEC.md:142` is pre-correction — that last one is a blank line.

**`schema.sql` is not the schema.** It declares 47 of 89 live tables. Any audit that reads only `schema.sql` sees roughly half the file. This single assumption produced SPEC-008's wrong table count, its uncheckable exclusion list, and the deferral of `lunm.schema_fingerprint`.

**Grepping `src/` misses DDL.** There is an engine-root `migrations/` directory loaded by filesystem path, not by import. That is why `ambassador_*` has no `CREATE TABLE` in `src/luna/`.

**Amending one spec section silently falsifies its neighbours.** An adversarial review of this session's own edits found 35 confirmed defects; the worst were in sections that were *correct as drafted* and became wrong without being touched — § Migration path claimed SPEC-008 changed nothing long after Q1 mandated a header. After editing any section, re-read § Migration path, § Validation rules, § Governance implications and § Dependencies.

**Measure, do not assume.** Three counts this session were off by roughly 2×, always toward more sprawl than documented: table count (36 → 89), scripts opening the matrix raw (60 → 32), DDL-declaring files (6 → 24).

---

## House conventions

**Resolving open questions.** Precedent is `01_Specs/implemented/SPEC-007_cartridge-sketches.md:306-321` and `SPEC-004_multi-axis-imprint-weights.md:311-333`. Preserve the question body verbatim — including a `**Recommendation:**` the resolution overrides — and append an indented `**Resolution (YYYY-MM-DD):**` line beneath it.

**At promotion:** rename `## Open questions` → `## Resolved questions`, rewrite its intro to past tense, update `Status` and `Last updated`, then `git mv` into `01_Specs/accepted/`. Relative links are depth-2 (`../../`) and survive the move.

**Lifecycle, per `00_README/README.md`:** `accepted` means *agreed to implement*. Engine work gates `implemented/`, never acceptance. A wording error in this session's first pass got this backwards at four sites and had to be corrected before promotion.

**Ledger entries should record outcomes, not just checkmarks.** Three of five SPEC-008 predictions were wrong; a bare `[x]` would have erased that and the next reader would have re-derived `lunm.profile_ulid` from the stale line.
