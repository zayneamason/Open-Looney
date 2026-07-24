---
doc_type: tracker
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - projectmanager
  - wiki
  - versioning
---

# Wiki Pass Tracker

Execution control plane for `.lun` development documentation passes. Policy:
`WIKI_VERSIONING.md`. Changelog: `WIKI_CHANGELOG.md`.

Current branch:

- `main`

Validated commit baseline:

- `1dcce8f`

Wiki version baseline:

- `v0.1.0`

Wiki current version:

- `v0.3.0`

Wiki target version:

- `v1.0.0` contract-frozen baseline (see `WIKI_VERSIONING.md` §4 for the three
  promotion criteria; criterion 3 has a five-spec backlog)

## Pass Status

| Pass | Name | Owner | Status | Gate | Evidence |
|---|---|---|---|---|---|
| P0 | Baseline — control plane and drift verifier | Claude | done | pass | `WIKI_CHANGELOG.md` `[v0.1.0]` |
| P1 | Looney-WIKI: a real wiki home | Claude | done | pass | `WIKI_CHANGELOG.md` `[v0.2.0]` |
| P2 | SPEC-012: LUNM entity unification | Codex | done | pass | `02_Handoffs/HANDOFF_2026-07-24_spec-012-entity-unification-drafted.md` |

## Gate Rule

A pass gate reads `pass` only when `python3 scripts/wiki_check.py` is clean at
the pass's closing commit. Check 6 enforces the converse mechanically: a row
marked `done` with an empty `Gate` is itself a finding.

## Pass Log

### P0 — Baseline

Ported the Luna Engine's wiki control plane
(`Docs/Design/SystemsArchitecture/`) onto this repo, adapting it from a
prose-review model to one keyed on spec-lifecycle consistency.

Three deviations from the reference model, each for a stated reason:

1. **Three control files, not four.** No `WIKI_HOME.md`; `00_README/README.md`
   already fills the nav-hub role. The Engine's own copy demonstrates the cost
   of the fourth file — `WIKI_HOME.md:10` says `v2.23.0` while
   `WIKI_VERSIONING.md:7` says `v2.23.1`, a duplicated version that drifted
   after 30+ passes.
2. **Governed scope is declared in `project_organization.json`, not restated
   here.** Same reasoning: one authoritative copy.
3. **Verifier first, bump script deferred.** The Engine added `wiki_bump.py` in
   its Pass 6. Bumping three files by hand is tolerable at one pass per session;
   automating it before the rhythm exists would be guessing at the friction.

The plan was reviewed twice before implementation — once externally, once by an
adversarial audit that raised 41 findings and refuted 33. Eight survived and
changed the design. The four most consequential:

- Check 7 originally compared only `last-bump..HEAD`, making it blind to staged
  and unstaged edits — precisely the state a pass is closed in. It now unions
  `git status --porcelain`.
- Check 2's original rule required a **bolded** lifecycle word. Measured, that
  rule finds two of the four live drifts: `README:183` is `**SPEC-010 accepted**`
  (one bold span) and `:185` is unbolded prose. Matching is now
  bold-insensitive, over a clause that may cross a newline.
- Check 1 had no declared input set. Applied to all governed docs it would have
  reported four permanent false positives — `01_Specs/TEMPLATE.md:3`, which must
  enumerate all five states, and three `03_Format_Spec/` docs carrying
  `**Status:** Shipping`. Its input set is now `01_Specs/*/SPEC-*.md`.
- The acceptance criterion asserted "exactly three" drifts. There are four, plus
  four link findings. Stating a wrong expected answer would have taught the
  implementer to "fix" a working parser.

Follow-ups recorded, not done in P0:

- P1 editorial sweep: `LUN-FORMAT_v0.1.md:260` and `SPEC-008:244` both describe
  SPEC-006 / SPEC-010 as living in `accepted/`; `TODO_LUN_Development_2026-07-20.md:110`
  cites `01_Specs/accepted/SPEC-011_…`, which does not exist.
- P1 backlog: the five specs lacking a commit/PR citation
  (`WIKI_VERSIONING.md` §4).

### P1 — Looney-WIKI: a real wiki home

Asked "where is the actual wiki documentation?", the honest answer was
nowhere — P0 built governance, not content. This pass builds the navigation
and content layer: a real `WIKI_HOME.md` with a generated, diff-checked
index; a taxonomy and glossary; and the first authored subsystem breakdown
(LUNM), reusing this repo's own live-verification discipline rather than
prose review.

The design was reviewed once before implementation and two of four findings
changed real behavior, not just wording:

- The move of the three P0 control-plane files into `Looney-WIKI/` was
  planned as "content unchanged." It wasn't: `WIKI_VERSIONING.md` explicitly
  argued against having a `WIKI_HOME.md` — exactly the file this pass adds —
  and its one relative link broke at the new directory depth. Both required
  rewriting `WIKI_VERSIONING.md`'s §1 reasoning, not just moving bytes.
  Verified live: an uncommitted `git mv` target has no `git log` entry, so
  check 7 correctly skips rather than silently reading a fresh rename as
  clean — that skip is by design, not a bug, and the move commit's baseline
  resolves cleanly right after.
- The LUNM breakdown's evidence was drafted from "verified earlier this
  session" — not a citable source for a wiki page. Split into durable
  citations (SPEC-006/008/009, no fresh query needed) and a dated,
  reproducible evidence appendix for the two findings that existed only in a
  prior transcript. Both were re-run fresh at authoring time; row counts
  moved slightly between the original session and this one (`memory_nodes`
  624→626, `graph_edges` 2517→2538), confirming the matrix is genuinely live
  and that "point-in-time" framing in the doc is load-bearing, not
  decorative. The `threads` DDL source citation had also drifted: its line
  number in `database.py` moved between the original finding and this pass's
  re-verification, in one Engine repo under active development on the same
  calendar day.

Two items P0's pass log flagged as P1 backlog remain open — checked, not
fixed: `LUN-FORMAT_v0.1.md:260` and `SPEC-008:244` still describe SPEC-006 /
SPEC-010 as living in `accepted/`. Out of this pass's scope (the wiki-home
build), not silently dropped — next pass.

Not done, deferred with reasoning:

- Tree-to-README coverage check — `SPEC-007` and `SPEC-011` both have zero
  README references, `SPEC-007` never has had any; would fire against an
  invariant the README never held.
- Backtick-path resolution in check 3 — ten unresolvable backticked paths in
  the README alone.
- Extending check 2 to `03_Format_Spec/**` / `01_Specs/**` — 13+ historical
  `(accepted)` references under date qualifiers need a framing heuristic a
  simple check doesn't have; the two live ones are the editorial-sweep item
  above instead.
- `scripts/wiki_bump.py`, pre-commit hook — same reasoning as P0: earn the
  automation after the manual rhythm proves out.

### P2 — SPEC-012: LUNM entity unification

Asked to turn the entity-unification architecture blueprint into repo authority,
this pass adds `SPEC-012_lunm-entity-unification.md` as an active LUNM spec.
The spec deliberately keeps the current family boundary intact: LUNM is the
runtime matrix family, not a cartridge; LUNC entity extractions are portable
observations, not Luna's authoritative entity identity layer.

The design locks the architecture while preserving acceptance blockers that
must be re-verified in the Luna Engine repo:

- The canonical entity key is the live LUNM `entities` row key, but the exact
  column name must be verified before `active → accepted`.
- `ENTITY` graph nodes, thread rosters, and Observatory chips become
  projections over that key rather than peer identity namespaces.
- Unknown-default typing, prompt diet, mention salience, Observatory DTOs, and
  maintenance live-locks are part of the same architecture because each one
  prevents the canonical identity repair from being immediately repolluted.

No format-invariant set changed and no `user_version` bump is specified.
Entity tables remain SPEC-009 `engine-extension` tables owned by
`luna.substrate` / `schema.sql`; Engine implementation is deferred until the
acceptance audit resolves the open questions in SPEC-012.
