---
doc_type: nav
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - wiki
  - nav
  - index
---

# Looney-WIKI: `.lun` Development Wiki Home

This is the wiki home for the `.lun` cartridge and runtime-matrix development
corpus. It is source of truth for what exists and what state it is in, not for
explanations of how any of it works — those live as authored breakdowns under
[`sections/`](sections/), one subsystem at a time.

Two layers, two different trust models:

- **The index below** (between `<!-- AUTOGEN -->` markers) is mechanical.
  `scripts/wiki_home.py` regenerates it from the tree on every run, and
  `scripts/wiki_check.py` (check 8) fails if the committed copy has drifted
  from a fresh regeneration. Trust it because it cannot silently go stale.
- **The breakdowns under `sections/`** are authored prose. Nothing regenerates
  them; they are drift-*guarded*, not drift-*proof* — `wiki_check.py` catches
  broken links and status claims inside them that contradict the tree, but the
  explanation itself is only as current as whoever last wrote it.

## Current Status

<!-- AUTOGEN:STATUS START -->
**Current version:** `v0.7.1`

| Specs (implemented) | Specs (accepted) | Specs (active) | Specs (rejected) | Format specs | Audits | Breakdowns |
|---|---|---|---|---|---|---|
| 15 | 0 | 2 | 0 | 3 | 6 | 2 |
<!-- AUTOGEN:STATUS END -->

Policy and promotion criteria: [`WIKI_VERSIONING.md`](WIKI_VERSIONING.md).
What changed each pass, and why: [`WIKI_CHANGELOG.md`](WIKI_CHANGELOG.md).
Pass execution and gates: [`WIKI_PASS_TRACKER.md`](WIKI_PASS_TRACKER.md).

## Reading Paths

- **New to the `.lun` format?** Start at
  [`../../00_README/README.md`](../../00_README/README.md) for project
  conventions and the spec lifecycle, then the current format spec in the
  index below.
- **New to LUNM (the runtime matrix)?** Start at
  [`sections/lunm-runtime-matrix.md`](sections/lunm-runtime-matrix.md).
- **Looking up a term?** [`GLOSSARY.md`](GLOSSARY.md).
- **Looking up how something is classified?** [`TAXONOMY.md`](TAXONOMY.md).
- **Checking whether a spec's state agrees with the tree?** Run
  `python3 scripts/wiki_check.py` — that is exactly what it does.

## Specs

<!-- AUTOGEN:SPEC_INDEX START -->
| Spec | Title | Status | File |
|---|---|---|---|
| SPEC-001 | Unanchored Claims in `.lun` Cartridges | implemented | [SPEC-001_orphan-claims.md](../../01_Specs/implemented/SPEC-001_orphan-claims.md) |
| SPEC-002 | Portable Identifiers for Cross-Cartridge References | implemented | [SPEC-002_portable-ids.md](../../01_Specs/implemented/SPEC-002_portable-ids.md) |
| SPEC-003 | Replace Hardcoded Confidence with Raw Signals | implemented | [SPEC-003_meaningful-confidence.md](../../01_Specs/implemented/SPEC-003_meaningful-confidence.md) |
| SPEC-004 | Multi-axis imprint weights | implemented | [SPEC-004_multi-axis-imprint-weights.md](../../01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md) |
| SPEC-005 | Append-Only Annotation Ledger | implemented | [SPEC-005_annotation-ledger.md](../../01_Specs/implemented/SPEC-005_annotation-ledger.md) |
| SPEC-005 | SPEC-005 (companion): Annotation Event Payload Schemas | implemented | [SPEC-005_payload-schemas.md](../../01_Specs/implemented/SPEC-005_payload-schemas.md) |
| SPEC-006 | application_id contract and v0.2 hygiene bundle | implemented | [SPEC-006_v02-hygiene-bundle.md](../../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md) |
| SPEC-007 | Cartridge sketches (bloom filters baked into the cartridge) | implemented | [SPEC-007_cartridge-sketches.md](../../01_Specs/implemented/SPEC-007_cartridge-sketches.md) |
| SPEC-008 | LUNM runtime matrix family — foundational contract | implemented | [SPEC-008_lunm-family-foundation.md](../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md) |
| SPEC-009 | LUNM schema ownership and the table manifest | implemented | [SPEC-009_lunm-schema-ownership.md](../../01_Specs/implemented/SPEC-009_lunm-schema-ownership.md) |
| SPEC-010 | LUNM migration discipline | implemented | [SPEC-010_lunm-migration-discipline.md](../../01_Specs/implemented/SPEC-010_lunm-migration-discipline.md) |
| SPEC-011 | LUNM format-invariant DDL ratification | implemented | [SPEC-011_lunm-format-invariant-ddl.md](../../01_Specs/implemented/SPEC-011_lunm-format-invariant-ddl.md) |
| SPEC-012 | LUNM entity unification | implemented | [SPEC-012_lunm-entity-unification.md](../../01_Specs/implemented/SPEC-012_lunm-entity-unification.md) |
| SPEC-013 | Searchable Figures (Structured Payload Pattern — Figure Spine) | implemented | [SPEC-013_searchable-figures.md](../../01_Specs/implemented/SPEC-013_searchable-figures.md) |
| SPEC-014 | Vision embeddings | implemented | [SPEC-014_vision-embeddings.md](../../01_Specs/implemented/SPEC-014_vision-embeddings.md) |
| SPEC-015 | Retrieval score comparability across result kinds | active | [SPEC-015_retrieval-score-comparability.md](../../01_Specs/active/SPEC-015_retrieval-score-comparability.md) |
| SPEC-016 | Read-only cross-project sync watcher | active | [SPEC-016_cross-project-sync-watcher.md](../../01_Specs/active/SPEC-016_cross-project-sync-watcher.md) |
<!-- AUTOGEN:SPEC_INDEX END -->

## Format Specs

<!-- AUTOGEN:FORMAT_SPEC_INDEX START -->
| Version | Status | File |
|---|---|---|
| v0.1 | Shipping (as of 2026-04-10) | [LUN-FORMAT_v0.1.md](../../03_Format_Spec/LUN-FORMAT_v0.1.md) |
| v0.2 | Shipping (as of 2026-05-12; established by Phases 1–5 of the v0.2 implementation arc) | [LUN-FORMAT_v0.2.md](../../03_Format_Spec/LUN-FORMAT_v0.2.md) |
| v0.3 | Shipping (2026-05-22; engine commit `407122f`; Meditations v0.3 audit passed) | [LUN-FORMAT_v0.3.md](../../03_Format_Spec/LUN-FORMAT_v0.3.md) |
<!-- AUTOGEN:FORMAT_SPEC_INDEX END -->

## Audits

<!-- AUTOGEN:AUDIT_INDEX START -->
| File | Date | Description | Link |
|---|---|---|---|
| AUDIT_2026-04-21_priests-and-programmers.md | 2026-04-21 | AUDIT: PRIESTS_AND_PROGRAMMERS_Lansing.lun | [AUDIT_2026-04-21_priests-and-programmers.md](../../04_Audits/AUDIT_2026-04-21_priests-and-programmers.md) |
| AUDIT_2026-05-22_meditations-v02.md | 2026-05-22 | AUDIT: Marcus-Aurelius-Meditations.lun | [AUDIT_2026-05-22_meditations-v02.md](../../04_Audits/AUDIT_2026-05-22_meditations-v02.md) |
| AUDIT_2026-05-22_meditations-v03.md | 2026-05-22 | AUDIT: Marcus-Aurelius-Meditations.v03.lun | [AUDIT_2026-05-22_meditations-v03.md](../../04_Audits/AUDIT_2026-05-22_meditations-v03.md) |
| AUDIT_2026-07-24_lunm-fi-pragma-table-info.md | 2026-07-24 | AUDIT: LUNM format-invariant PRAGMA table_info | [AUDIT_2026-07-24_lunm-fi-pragma-table-info.md](../../04_Audits/AUDIT_2026-07-24_lunm-fi-pragma-table-info.md) |
| AUDIT_2026-07-24_lunm-table-manifest.toml | 2026-07-24 | (TOML data file) | [AUDIT_2026-07-24_lunm-table-manifest.toml](../../04_Audits/AUDIT_2026-07-24_lunm-table-manifest.toml) |
| AUDIT_2026-07-24_searchable-figures-spike.md | 2026-07-24 | AUDIT: Searchable figures Markdown+PNG spike | [AUDIT_2026-07-24_searchable-figures-spike.md](../../04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md) |
<!-- AUTOGEN:AUDIT_INDEX END -->

## Breakdowns

Authored, one subsystem at a time. Each follows the same shape: definition →
classification → what's stored vs. not → per-item detail → citations.

<!-- AUTOGEN:BREAKDOWN_INDEX START -->
| Title | Description | File |
|---|---|---|
| The `.lun` cartridge format family (LUNC) | The .lun cartridge format (LUNC) — v0.1 through v0.3, identity contract, table evolution, what's stored vs. not, the annotation ledger in detail | [lun-format-family.md](sections/lun-format-family.md) |
| LUNM: the runtime matrix | LUNM runtime matrix — identity, format-invariant vs. engine-extension tables, what's stored vs. not, threads and entities in detail | [lunm-runtime-matrix.md](sections/lunm-runtime-matrix.md) |
<!-- AUTOGEN:BREAKDOWN_INDEX END -->

## Templates

- [`../../01_Specs/TEMPLATE.md`](../../01_Specs/TEMPLATE.md) — new spec
  template. Not itself a spec, so it does not appear in the index above.
