---
doc_type: policy
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - projectmanager
  - wiki
  - versioning
  - protocol
---

# Wiki Versioning Policy

This policy defines how the `.lun` development spec corpus is versioned across
passes. Modeled on the Luna Engine wiki policy at
`Docs/Design/SystemsArchitecture/WIKI_VERSIONING.md`, re-cut for a repo whose
unit of change is a spec advancing its lifecycle rather than a prose doc being
polished.

Current wiki version:

- `v0.1.0`

## 1. Scope

Governed paths are declared in root `project_organization.json` under
`wiki.governed`. **That file is authoritative.** This policy deliberately does
not restate the list: a second copy is a second thing that can drift, which is
the exact defect this system exists to catch. The reference model demonstrates
the risk — the Engine's `WIKI_HOME.md:10` reads `v2.23.0` while its own
`WIKI_VERSIONING.md:7` reads `v2.23.1`.

The governed set covers the contract documents (`01_Specs/`, `03_Format_Spec/`,
`04_Audits/`, `00_README/README.md`), the wiki home and control plane
(`ProjectManager/Looney-WIKI/**`), and the tooling that defines these rules
(`project_organization.json`, `scripts/wiki_*.py`). Changing the rules is
therefore itself a governed change.

Nav hub: `WIKI_HOME.md`, in this same directory. As of P1 it carries a
generated index over specs, format specs, audits, and authored breakdowns,
diffed against a fresh regeneration by check 8 so it cannot silently go stale.
`00_README/README.md` remains the top-level project README — folder structure,
naming conventions, spec lifecycle definition — and stays governed (check 2
still watches its prose lifecycle claims about specs), but it is not the nav
hub. P0's original reasoning here ("no separate `WIKI_HOME.md`; a second
orientation document would only duplicate claims") measured the Engine's
`WIKI_HOME.md` against its own `WIKI_VERSIONING.md` and found a duplicated
*version number* that had drifted — a real risk, but specific to a hand-authored
second copy of one fact. A generated index carries no hand-maintained
duplicate to drift; check 8 verifies it against its own generator on every
run. That reasoning is why P1 reverses P0's call rather than repeating it.

## 2. Version Format

`MAJOR.MINOR.PATCH`.

- **MAJOR** — a contract's meaning changes: a shipped `LUN-FORMAT` version's
  rules change, a spec in `implemented/` is amended so that shipped behavior is
  now non-conforming, or a spec is withdrawn from `implemented/`.
- **MINOR** — additive: a spec advances lifecycle state, a new spec or
  format-spec version lands, a new audit artifact is added, or the verifier's
  gate semantics change.
- **PATCH** — editorial: wording, link repairs, formatting, README re-sync with
  no change to any claim.

## 3. Pass Rules

A pass is one unit of work, recorded as a row in `WIKI_PASS_TRACKER.md`.

For each completed pass:

1. Run `python3 scripts/wiki_check.py`. It must be clean.
2. Record a changelog entry in `WIKI_CHANGELOG.md`, including an explicit
   rationale for the bump type.
3. Update the current version here and in `WIKI_PASS_TRACKER.md`.
4. Fill the pass row's `Gate` column. **A pass whose gate is empty is not done**
   — `wiki_check.py` check 6 enforces this.
5. Link the pass to its `02_Handoffs/HANDOFF_YYYY-MM-DD_*.md` in the Evidence
   column.

The gate is keyed to spec-lifecycle consistency, not to prose quality: a pass
fails if a spec's `**Status:**` header, its folder, and the README's claims
about it disagree.

## 4. Baseline and Promotion

- `v0.x` — contract-candidate (active cleanup and refinement)
- `v1.0.0` — contract-frozen baseline

Promote to `v1.0.0` only when:

1. Every pass gate in `WIKI_PASS_TRACKER.md` reads `pass`.
2. `scripts/wiki_check.py` runs clean.
3. Every spec in `01_Specs/implemented/` carries at least one commit SHA or PR
   number anywhere in its Implementation notes section.

Criterion 3 is not met today. Five specs lack a citation under that definition:

| Spec | State |
|---|---|
| `SPEC-001_orphan-claims.md:340` | `(pending — uncommitted at this paste)` |
| `SPEC-002_portable-ids.md:1466` | `(pending — Phase 3 implementation branch)` |
| `SPEC-003_meaningful-confidence.md:339` | field present but empty |
| `SPEC-004_multi-axis-imprint-weights.md:355-364` | no such field at all |
| `SPEC-006_v02-hygiene-bundle.md:260` | `(pending — uncommitted at evidence-paste time)` |

SPEC-001, SPEC-002, and SPEC-003 each carry a `### Phase 5 closeout` commit
table, so whether they "cite" depends on the definition above being read
strictly. Resolving all five is P1 backlog, not a P0 concern.

## 5. Commit Convention

Commit footer:

```text
Wiki-Version: vX.Y.Z
Wiki-Pass: Pn
```

Example:

```text
docs(wiki): P0 baseline — control plane and drift verifier

Wiki-Version: v0.1.0
Wiki-Pass: P0
```

Nothing currently validates this footer; it is convention, not contract.

## 6. Related

- `WIKI_HOME.md` — the wiki nav hub, generated index over specs, format specs,
  audits, and breakdowns
- `WIKI_CHANGELOG.md` — what changed, and why the bump was that size
- `WIKI_PASS_TRACKER.md` — pass execution and gates
- `TAXONOMY.md` — classification vocabulary
- `GLOSSARY.md` — canonical term definitions
- `../../scripts/wiki_check.py` — the verifier
- `../../00_README/README.md` — top-level project README and spec lifecycle
  definition
