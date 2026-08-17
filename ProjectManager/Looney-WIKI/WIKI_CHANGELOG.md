---
doc_type: changelog
status: active
created: 2026-07-24
updated: 2026-08-17
tags:
  - projectmanager
  - wiki
  - versioning
---

# Wiki Changelog

Reverse-chronological. Every entry states what changed and why the bump was that
size. Policy: `WIKI_VERSIONING.md`.

## [v0.7.1] - 2026-08-17

Pass: P7 — SPEC-016 watcher implementation
Type: patch

Changes:

- `01_Specs/active/SPEC-016_cross-project-sync-watcher.md` — accepted v1
  decisions, JSON manifest shape, standard-library-only dependency posture, and
  implementation notes recorded.
- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — SPEC-016 implementation
  result and live smoke evidence recorded.
- `ProjectManager/Plans/PLAN_2026-08-17_spec-016_cross-project-sync-watcher.md`
  — checklist closed and implementation result recorded.
- `ProjectManager/cross_project_sync.json`, `scripts/cross_project_sync.py`,
  and `scripts/tests/test_cross_project_sync.py` — local read-only watcher,
  manifest, and stdlib test suite added.
- `ProjectManager/Looney-WIKI/WIKI_HOME.md`,
  `ProjectManager/Looney-WIKI/WIKI_VERSIONING.md`, and
  `ProjectManager/Looney-WIKI/WIKI_PASS_TRACKER.md` — current version bumped to
  `v0.7.1`.

Rationale: PATCH — implements an already-active project-management watcher
without changing `.lun` format law, spec lifecycle state, or cartridge runtime
contracts.

Verified:

- `python3 -m py_compile scripts/cross_project_sync.py`
- `python3 -m unittest scripts.tests.test_cross_project_sync`
- `python3 scripts/cross_project_sync.py check --json`
- `python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync.md`
- before/after `git status --short` snapshots byte-identical in both repos.

## [v0.7.0] - 2026-08-17

Pass: P6 — SPEC-014/SPEC-015 closeout + SPEC-016 watcher
Type: minor

Changes:

- `01_Specs/implemented/SPEC-014_vision-embeddings.md` — governed closeout from
  the Engine merge that skipped normal PR-triggered bookkeeping; spec now lives
  in `implemented/`.
- `01_Specs/active/SPEC-015_retrieval-score-comparability.md` — active spec for
  SPEC-014's named follow-up on incomparable result scores.
- `01_Specs/active/SPEC-016_cross-project-sync-watcher.md` — new active spec for
  a read-only watcher that detects Open-Looney / Luna Engine ledger, version,
  cross-reference, git-baseline, dirty-state, and evidence-durability drift
  without mutating either repo.
- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — ledger entry for
  SPEC-016 and updated frontmatter date.
- `ProjectManager/Looney-WIKI/WIKI_HOME.md` — regenerated mechanical index;
  active spec count now includes SPEC-015 and SPEC-016.
- `ProjectManager/Looney-WIKI/WIKI_VERSIONING.md` and
  `ProjectManager/Looney-WIKI/WIKI_PASS_TRACKER.md` — current version bumped to
  `v0.7.0`.

Rationale: MINOR — new active spec plus lifecycle/contract-index catch-up for
governed specs. No shipped `LUN-FORMAT` rule changed and no cartridge
`user_version` moves.

Verified:

- `python3 scripts/wiki_home.py`
- `python3 scripts/wiki_check.py`

## [v0.6.1] - 2026-07-25

Pass: P5 follow-up — wiki_check clean
Type: patch

Changes:

- `04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md` — drop false-positive
  relative-link parse from markdown image example.
- `WIKI_HOME.md` regenerated (STATUS block freshness).

Rationale: PATCH — typo/link-check hygiene after P5, no lifecycle advance.

Verified:

- `python3 scripts/wiki_home.py`
- `python3 scripts/wiki_check.py`

## [v0.6.0] - 2026-07-25

Pass: P5 — SPEC-013 searchable figures (spine + enrichment)
Type: minor

Changes:

- `01_Specs/implemented/SPEC-013_searchable-figures.md` — searchable figures
  spine + enrichment arc recorded through Engine PRs #164–#170 (Markdown,
  PDF XObjects, bare raster input, optional OCR, `media_classification`,
  `visual_description`, `figure_discourse`).
- `01_Specs/accepted/SPEC-012_lunm-entity-unification.md` — removed stale
  accepted copy; implemented file is sole home (SPEC-012 already promoted).
- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — SPEC-013 ledger items.
- `08_Journal/2026-07-25.md` — catch-up through PR #170.
- `02_Handoffs/HANDOFF_2026-07-25_spec-013-enrichment-arc.md` — session close.
- `WIKI_HOME.md` regenerated — SPEC-012 + SPEC-013 in implemented index.

Rationale: specs advanced / new implemented SPEC appeared in the index, which
`WIKI_VERSIONING.md` §2 names as a MINOR trigger.

Verified:

- `python3 scripts/wiki_home.py`
- `python3 scripts/wiki_check.py`

## [v0.5.0] - 2026-07-24

Pass: P4 — SPEC-012 accepted
Type: minor

Changes:

- `01_Specs/accepted/SPEC-012_lunm-entity-unification.md` — promoted
  `active → accepted` after Engine read-only audit. Locked Q1–Q6: canonical
  key `entities.id`; no `graph_node_id` bridge; quarantine DDL frozen;
  N-way coalesce quarantine-only unless allowlist; flag set; DTO owners;
  unknown-default probe corpus. Open questions → Resolved questions.
- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — acceptance item checked.
- `WIKI_HOME.md` regenerated — SPEC-012 in accepted index; active count 0.

Rationale: a spec advanced lifecycle state, which `WIKI_VERSIONING.md` §2
names as a MINOR trigger.

Verified:

- `python3 scripts/wiki_home.py`
- `python3 scripts/wiki_check.py`

## [v0.4.0] - 2026-07-24

Pass: P3 — `lun-format-family` breakdown
Type: minor

Changes:

- `ProjectManager/Looney-WIKI/sections/lun-format-family.md` (new) — second
  authored breakdown, same template as `lunm-runtime-matrix.md`: definition
  → classification → what's stored vs. not → per-item detail → citations.
  Covers the `.lun` cartridge format (LUNC) across v0.1/v0.2/v0.3: the
  identity contract (required from v0.2 onward, not v0.1), the
  `nexus_refs` status change at the v0.3 boundary (not a v0.2-only table —
  its meaning changes, not its presence), the confidence → raw-signals
  story (SPEC-003), the anchor taxonomy (SPEC-001) and its C-01 rename
  (`claim_sources` → `extraction_sources`, because it anchors summaries too,
  not just claims), the ledger's soft-covenant honesty (SPEC-005), and the
  FTS5 reattachment decision (Q1) with its measured alternatives. Evidence
  appendix re-run fresh against `Marcus-Aurelius-Meditations.v03.lun` at
  authoring time — cross-checks, not bare counts (e.g. `anchor_status
  = 'unknown'` count exactly equals entity count; `extraction_sources` row
  count exactly equals `anchored` count).
- `ProjectManager/Looney-WIKI/GLOSSARY.md` — Anchor definition corrected to
  name both `claim_sources` (v0.1/v0.2) and `extraction_sources` (v0.3+,
  C-01 rename) and both extraction kinds it anchors. The prior definition
  named only `claim_sources` and only claims — stale the moment this
  breakdown explains the rename.
- `ProjectManager/Looney-WIKI/WIKI_VERSIONING.md` §2 — added "a new authored
  breakdown lands under `Looney-WIKI/sections/`" to the MINOR trigger list.
  P1 shipped the first breakdown without hitting this gap only because it
  also added check 8 (an already-listed trigger); this pass is the first to
  actually need the breakdown case named.
- `WIKI_HOME.md` regenerated — `BREAKDOWN_INDEX` now lists 2 breakdowns; also
  picked up `SPEC-012` (landed by the concurrent P2 pass) in the active-spec
  count with zero manual intervention, confirming the generator handles
  work from other agents without hand-sync.

Rationale for MINOR bump:

- A new authored breakdown landed, which `WIKI_VERSIONING.md` §2 now names
  as a MINOR trigger (added in this same pass). No shipped `LUN-FORMAT`
  rule changed, no implemented spec was amended, no LUNM format-invariant
  classification changed. The GLOSSARY and policy edits are corrections
  bundled with the breakdown that made them necessary, not independent
  contract changes.

Verification performed:

- `python3 scripts/wiki_home.py` — breakdown count 1 → 2, confirmed by eye.
- `python3 scripts/wiki_check.py` — clean except check 7 (unbumped), as
  expected pre-seal; zero broken links, zero README-claim drift introduced.
- Grepped the new breakdown for every `claim_sources` mention — confirmed
  each is version-qualified or is itself the sentence warning against
  unqualified usage. (This check exists because an early draft of this
  pass's plan made exactly that mistake before implementation began — see
  the P3 pass log.)
- `python3 scripts/wiki_check.py --strict` clean at the closing commit.
- Pass numbering re-confirmed immediately before sealing:
  `git log --oneline` and `WIKI_PASS_TRACKER.md` re-checked for any pass
  landing after `8c1b6b5` (P2) — none found; `P3`/`v0.4.0` correct.

Validated against: (pending — see the P3 pass-tracker row for the closing
commit SHA once sealed)

## [v0.3.0] - 2026-07-24

Pass: P2 — SPEC-012: LUNM entity unification
Type: minor

Changes:

- `01_Specs/active/SPEC-012_lunm-entity-unification.md` (new) — active
  architecture spec for unifying Luna's LUNM entity subsystem around one
  canonical entity row key. It rewrites the entity blueprint into this repo's
  family vocabulary: LUNM runtime matrix, not cartridge; LUNC entity
  extractions are portable observations/evidence, not authoritative live
  identity.
- SPEC-012 explicitly keeps the LUNM entity family as SPEC-009
  `engine-extension` tables owned by `luna.substrate` / `schema.sql`; it does
  not promote entity tables into the SPEC-008 format-invariant set and does not
  require a `user_version` bump for additive entity work.
- SPEC-012 records the phased Engine implementation order: canonical identity,
  unknown default, prompt diet, mentions/salience, Observatory DTO, maintenance
  safety, and later span/fact normalization.
- `02_Handoffs/HANDOFF_2026-07-24_spec-012-entity-unification-drafted.md`
  (new) — closeout handoff naming the acceptance blockers and next Engine audit
  arc.
- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — records the SPEC-012
  draft and next acceptance/Engine audit follow-up under LUNM Runtime Matrix
  Specs.
- `WIKI_HOME.md` regenerated so the active spec index reflects SPEC-012.

Rationale for MINOR bump:

- A new active spec landed, which `WIKI_VERSIONING.md` §2 names as a MINOR
  trigger. No shipped LUN-FORMAT rule changed, no implemented spec was amended,
  and no LUNM format-invariant classification changed.

Verification performed:

- `python3 scripts/wiki_home.py`
- `python3 scripts/wiki_check.py`

## [v0.2.0] - 2026-07-24

Pass: P1 — Looney-WIKI: a real wiki home
Type: minor

Changes:

- **Moved the P0 control plane** into `ProjectManager/Looney-WIKI/`:
  `WIKI_VERSIONING.md`, `WIKI_CHANGELOG.md` (this file),
  `WIKI_PASS_TRACKER.md`. This is a content migration, not a mechanical
  move — `WIKI_VERSIONING.md`'s §1 rationale for "no separate `WIKI_HOME.md`"
  is rewritten (it described exactly the file this pass adds), and its one
  relative link is corrected for the new directory depth.
- `project_organization.json` — bumped to version 3. `wiki.control_plane`
  gains a `home` key; `nav_hub` renamed to `readme` (still governed, still
  watched by check 2, no longer the nav hub); `wiki.governed` repoints
  `ProjectManager/WIKI_*.md` to `ProjectManager/Looney-WIKI/**`.
- `scripts/wiki_lib.py` (new) — shared parsing/config helpers extracted from
  `wiki_check.py`, so the verifier and the new generator read the same facts
  through one implementation rather than two that could drift apart.
- `scripts/wiki_home.py` (new) — generator. `build_blocks(config)` renders
  five index blocks (current-version status, specs, format specs, audits,
  authored breakdowns) from the live tree; the CLI writes them between
  `<!-- AUTOGEN:NAME START/END -->` sentinels in `WIKI_HOME.md`. Static
  content outside the sentinels is never touched.
- `ProjectManager/Looney-WIKI/WIKI_HOME.md` (new) — the real wiki nav hub.
  Generated index over 12 specs, 3 format specs, 5 audits, 1 breakdown, plus
  hand-authored reading paths.
- **Check 8** (new, `wiki_check.py`) — diffs `WIKI_HOME.md`'s committed
  AUTOGEN blocks against a fresh regeneration. Covers exactly the four
  indexed classes (specs, format specs, audits, breakdowns); everything else
  governed is still caught by check 7, not duplicated here.
- `ProjectManager/Looney-WIKI/TAXONOMY.md` (new) — every classification
  vocabulary already live in the repo (doc kind, spec lifecycle, `.lun`
  family, LUNM table classification), stated once.
- `ProjectManager/Looney-WIKI/GLOSSARY.md` (new) — canonical definitions.
  The prior `00_README/README.md` glossary (13 cartridge-family terms) moved
  here verbatim, plus six LUNM terms.
- `ProjectManager/Looney-WIKI/sections/lunm-runtime-matrix.md` (new) — the
  first authored breakdown and the template every future one follows:
  definition → classification → what's stored vs. not → per-item detail →
  citations. Durable facts cite SPEC-006/008/009; two findings that existed
  only in a prior session's transcript (the `lunm.created_at`
  seed-time-vs-birth-time gap, and a live `threads` table DDL drift between
  `schema.sql` and `database.py`) were re-run fresh and captured as a dated,
  reproducible evidence appendix rather than carried over as an unlogged
  claim.
- `00_README/README.md` — points at the new wiki home and glossary; the
  13-term glossary section collapses to a pointer (two glossaries is the
  drift class this system exists to kill); folder-structure block updated;
  no longer described as "the wiki nav hub" — it is the top-level project
  README.
- `ProjectManager/TODO_LUN_Development_2026-07-20.md:110` — corrected a
  stale `01_Specs/accepted/SPEC-011_...` path to `implemented/`, the same
  drift class as the P0 README fix, found in the file the user had open
  when this pass began.

Rationale for MINOR bump:

- Entirely additive structure — a new wiki home, taxonomy, glossary, one
  breakdown, and a new verifier check — plus a verifier gate-semantics
  change (check 8), which `WIKI_VERSIONING.md` §2 names as a MINOR trigger.
  No contract meaning changed: no spec's lifecycle state changed, no format
  spec changed, no `format-invariant` classification changed.

Verification performed:

- Move-then-migrate ordering, verified against real git behavior: an
  uncommitted `git mv` target has no `git log` entry, so check 7 correctly
  printed "no bump baseline yet" and skipped rather than misreporting clean;
  after committing the move, check 7 resolved with zero findings.
- Generator dry run against the tree before `WIKI_HOME.md`'s first commit:
  12 implemented specs (0 accepted/active/rejected), 3 format specs,
  5 audits, 0 breakdowns — matched by hand.
- Check 8 negative test: hand-corrupted the committed `STATUS` block
  (`v0.1.0` → `v9.9.9`); check 8 fired naming that block; regenerated;
  confirmed clean.
- Check 8 positive test: added the LUNM breakdown, regenerated, confirmed
  `BREAKDOWN_INDEX` picked it up with zero manual index editing.
- Full suite (checks 1–8) clean at the closing commit; `--strict` exits 0
  for the same reason.

Validated against: (pending — see the P1 pass-tracker row for the closing
commit SHA once sealed)

## [v0.1.0] - 2026-07-24

Pass: P0 — baseline
Type: baseline

Changes:

- `project_organization.json` — bumped to version 2; added the `wiki` block
  declaring governed globs, exclusions, the control-plane paths, the lifecycle
  vocabulary, and check 1's input glob. `canonical_ledger` and `policy`
  preserved unchanged.
- `scripts/wiki_check.py` (new) — drift verifier, Python 3 stdlib only. Seven
  checks: spec status vs folder, README claims vs tree, link integrity, version
  agreement across the control plane, changelog completeness, pass-table
  integrity, and unbumped governed change.
- `ProjectManager/WIKI_VERSIONING.md` (new) — this corpus's versioning policy.
- `ProjectManager/WIKI_CHANGELOG.md` (new) — this file.
- `ProjectManager/WIKI_PASS_TRACKER.md` (new) — pass execution control plane.
- `00_README/README.md` — corrected four stale lifecycle claims (three naming
  SPEC-010 `accepted` when it is `implemented`, one naming SPEC-005 the same);
  added the control-plane files to the folder-structure block.
- `01_Specs/implemented/SPEC-009_lunm-schema-ownership.md:174` — repaired a link
  pointing into the empty `01_Specs/accepted/`.
- `01_Specs/implemented/SPEC-001`, `SPEC-002`, `SPEC-003` — converted an
  identical broken cross-repo handoff link to a prose citation in each.
- `ProjectManager/README.md` — documented the control plane and the pass-close
  procedure; corrected the now-false claim that no organization checker exists.
  Added to governed scope: it documents the rules, so changing it is a governed
  change. Found by check 7 reporting every other edited file but not this one.

Rationale for baseline:

- This entry establishes `v0.1.0` as the contract-candidate baseline. It records
  the corpus as found rather than reconstructing history for the twelve specs
  already in `implemented/`; no retroactive versions are fabricated. The
  document repairs bundled here are not counted as a separate bump — they are
  the P0 pass's own subject matter, and every one of them was found by the
  verifier landing in this same commit.

Known-answer test:

- The verifier was landed before the document repairs, so its first run had a
  known correct answer. It reported exactly the predicted eight items: four
  check-2 findings (`00_README/README.md:46`, `:175`, `:183`, `:185`), one
  gating check-3 finding (`SPEC-009_lunm-schema-ownership.md:174`), and three
  informational `external-reference` entries (`SPEC-001:655`, `SPEC-002:1484`,
  `SPEC-003:357`) — with zero findings under checks 1, 4, 6, and 7. That result
  is the evidence the checks work; a verifier that reports nothing on a tree
  with known drift is indistinguishable from one that does nothing at all.

Deferred (measured, not assumed):

- Tree-to-README coverage. `SPEC-007` and `SPEC-011` have zero README
  references, and SPEC-007 never has had any, so the check would fire on day one
  against an invariant the README has never held. The README is a narrative hub;
  the declared `canonical_ledger` is the spec index.
- Backtick-path resolution in check 3 — ten unresolvable backticked paths in the
  README alone, several pointing at Luna Engine files no glob can reach.
- Extending check 2 to `03_Format_Spec/**` and `01_Specs/**` — 13+ historical
  `(accepted)` references, most under explicit date qualifiers, would need a
  historical-framing heuristic. Two genuine live items
  (`LUN-FORMAT_v0.1.md:260`, `SPEC-008:244`) go on the P1 editorial sweep.
- `scripts/wiki_bump.py` and a pre-commit hook — the Engine added its bump
  script in Pass 6, after its doc plane proved out. Same sequencing here.

Verification performed:

- Known-answer test (verifier landed before the repairs): exactly the predicted
  8 items, described above.
- False-positive floor: zero findings against `01_Specs/TEMPLATE.md`
  (whose `**Status:**` line must list all five states) and zero against
  `03_Format_Spec/**` (whose docs read `**Status:** Shipping`).
- Negative test: flipping `SPEC-007`'s header to `accepted` produced
  `01_Specs/implemented/SPEC-007_cartridge-sketches.md:3: header says 'accepted'
  but the file sits in implemented/`. Reverted.
- Working-tree test: with the repairs uncommitted, check 7 reported all five
  edited governed files. The original design compared only
  `last-bump..HEAD` and would have reported nothing — the case that matters
  most, since a pass is closed from a dirty tree.
- Exit-code test: exit 0 under `policy: "warning-only"`, exit 1 under
  `--strict`.

Validated against: `2c849ed` (the P0 control-plane commit) plus the repairs in
this commit; `scripts/wiki_check.py` clean at seal time.
