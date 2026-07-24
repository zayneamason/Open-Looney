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

- `v0.1.0`

Wiki target version:

- `v1.0.0` contract-frozen baseline (see `WIKI_VERSIONING.md` §4 for the three
  promotion criteria; criterion 3 has a five-spec backlog)

## Pass Status

| Pass | Name | Owner | Status | Gate | Evidence |
|---|---|---|---|---|---|
| P0 | Baseline — control plane and drift verifier | Claude | done | pass | `WIKI_CHANGELOG.md` `[v0.1.0]` |

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
