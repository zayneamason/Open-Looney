---
doc_type: ledger
status: active
created: 2026-07-20
updated: 2026-07-21
tags:
  - lun
  - cartridge
  - reader
  - specs
  - audit
---

# .lun Development TODO

This is the canonical working ledger for `.lun` format, cartridge, reader,
audit, and runtime-matrix follow-up in this repo.

Current source of truth at creation: `08_Journal/2026-05-24.md`,
`06_Prototypes/ReaderPrototype/SPEC.md`, `04_Audits/AUDIT_2026-05-22_meditations-v03.md`,
and `01_Specs/active/SPEC-008_lunm-family-foundation.md`.

Research intake added 2026-07-21:
`/Users/zayneamason/_HeyLuna_BETA/Research/Looney_GeminiConversation_001.md`.

## Reader Prototype

- [ ] Fix Reader baseline-drift round 3: update the 3 `queries::list_extractions` tests that still expect the v0.2 Meditations extraction-count baseline after the 2026-05-23 rebuild against engine `24c19c2`.
- [ ] Run manual visual verification for Reader v0.3.3 acceptance criteria 18 + 19 against the post-M-01 rebuilt `Marcus-Aurelius-Meditations.v03.lun` via `npm run tauri dev`.
- [ ] Record the manual visual verification result in `06_Prototypes/ReaderPrototype/SPEC.md` or the next `08_Journal/` entry.

## Cartridge Quality And Audits

- [x] M-01 title truncation audit follow-up closed: PDF parser now merges multi-line title blocks; Meditations cartridge rebuilt; follow-on memory updated.
- [ ] Investigate S-01 embedding coverage gap: Meditations section embedding coverage is `149/176`; determine whether this is expected policy, source/parser behavior, or a builder defect.
- [ ] Update the relevant audit/spec note once S-01 is classified.
- [ ] Keep historical v0.2 limitations alive only where still applicable: Lansing 9.5% baseline measurement and form-feed artifacts are not blocked by v0.3, but should remain documented until separately resolved.

## LUNM Runtime Matrix Specs

- [ ] Resolve SPEC-008 Q1: choose `profile_config` or a dedicated `lunm_header` table as the LUNM human-readable identity mechanism.
- [ ] Resolve SPEC-008 Q2: decide required LUNM header keys (`lunm.format_version`, `lunm.profile_ulid`, `lunm.created_at`, likely `lunm.engine_version`; defer or accept `lunm.schema_fingerprint`).
- [ ] Resolve SPEC-008 Q3: decide whether `nexus_refs` cross-family verification is a production `MUST` with an unsafe test carve-out.
- [ ] Resolve SPEC-008 Q4: decide LUNM `user_version` bump policy, likely stricter than LUNC and limited to contract-affecting changes.
- [ ] Resolve SPEC-008 Q5: ask the engine implementer what happened to `ih_events` and confirm the core-table boundary before acceptance.
- [ ] Promote `SPEC-008` from `active` to `accepted` once Q1-Q5 are resolved, preserving question bodies with dated resolution lines.
- [ ] After acceptance, update `03_Format_Spec/LUN-FORMAT_v0.1.md`, `LUN-FORMAT_v0.2.md`, and `LUN-FORMAT_v0.3.md` references to point at SPEC-008 for LUNM.
- [ ] Move SPEC-008 to `implemented` once Q1's mechanism is built into the engine.
- [ ] Draft SPEC-009 for full LUNM table DDL ratification after a fresh audit of `schema.sql` plus the `_migrate_*` helpers.
- [ ] Draft SPEC-010 for LUNM migration discipline: idempotency rules, legal in-place changes, and `user_version` bump rules.

## Looney Data Research Intake

- [ ] Review `Looney_GeminiConversation_001.md` for durable spec candidates; treat it as brainstorm input, not authority.
- [ ] Draft a research note on external provenance and minting: compare manifest hashing, Verifiable Credentials, local signed journals, and public-chain anchoring without putting network/blockchain requirements inside the core `.lun` read path.
- [ ] Draft a compression investigation for `.lun` cartridges: compare uncompressed `content`, compressed shadow columns plus FTS, application-layer zstd/lz4, and SQLite extension approaches; preserve FTS/search portability as a first-class constraint.
- [ ] Draft a model/runtime-manifest investigation: evaluate whether cartridges should advertise preferred models, context windows, prompt templates, or aperture routing as metadata while keeping model weights and executable runtimes outside portable cartridge files.
- [ ] Draft a media/spatial cartridge investigation: compare GeoPackage-style SQLite tiling, image metadata, OCR, bounding boxes, and vector indexes for a future media/mapping family; do not overload the existing LUNM runtime-matrix name.
- [ ] Draft an IP/licensing investigation: evaluate encrypted content chunks, local license tokens, signed manifests, buyer fingerprinting, and watermarking tradeoffs for paid cartridges.
- [ ] Draft a prompt-assembly/JEPAlike research note: separate near-term deterministic prompt-index retrieval from speculative predictive latent-state models; identify what belongs in Luna Engine runtime state versus portable `.lun` cartridge schema.
- [ ] Add a safety-boundary note for any future executable or adaptive cartridge family: unsigned cartridges must never execute code, load model weights unsafely, or gain broad filesystem write access.

## Distribution

- [ ] Optional: build a v0.3.x `.dmg` for the Reader now that u64-overflow, bare-name, verify-by-opening, click-through, and M-01 fixes are baked into source.
- [ ] If a `.dmg` is built, record the build artifact path, source commit, and smoke result in the ledger or dated journal.

## Repo Hygiene

- [ ] Update `00_README/README.md`: it still describes v0.2 as current while `03_Format_Spec/LUN-FORMAT_v0.3.md` says v0.3 is Shipping.
- [ ] Decide whether this repo should get a root `project_organization.json` with `canonical_ledger = "ProjectManager/TODO_LUN_Development_2026-07-20.md"`.
- [ ] Decide whether the copied `10_Builder/` subtree should be labeled as a stale/reference snapshot, updated to v0.3, or moved out of the authority path.
- [ ] Add or update repo guidance so future agents treat top-level specs and audits as authority over stale implementation snapshots.
