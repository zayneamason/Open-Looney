---
doc_type: changelog
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - projectmanager
  - wiki
  - versioning
---

# Wiki Changelog

Reverse-chronological. Every entry states what changed and why the bump was that
size. Policy: `WIKI_VERSIONING.md`.

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
- `ProjectManager/README.md` — documented the control plane; corrected the
  now-false claim that no organization checker exists.

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

Validated against: (pending)
