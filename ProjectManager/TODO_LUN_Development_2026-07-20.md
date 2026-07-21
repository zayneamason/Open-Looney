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
and `01_Specs/accepted/SPEC-008_lunm-family-foundation.md` (promoted from `active/` on 2026-07-21).

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

- [x] Resolve SPEC-008 Q1: `profile_config`, under a reserved `lunm.*` namespace. Its stated rationale was falsified against the live engine — the table is absent from `schema.sql`, holds 0 rows in every live matrix, and is deletable over HTTP — so three engine preconditions ride along.
- [x] Resolve SPEC-008 Q2: four keys — `lunm.format_version`, **`lunm.matrix_ulid`** (renamed from `profile_ulid` and re-scoped to the file; the genesis hook fires per file, and no profile ULID exists in the engine), `lunm.created_at`, `lunm.engine_version` (declared placeholder). `lunm.schema_fingerprint` deferred to SPEC-009, defined over live `sqlite_master`.
- [x] Resolve SPEC-008 Q3: `MUST`, unqualified — already shipped and test-covered in `promote_to_nexus()`. The unsafe carve-out was dropped (zero precedent repo-wide, one production caller); the umbrella MUST scoped to production paths, `SHOULD` for maintenance tooling.
- [x] Resolve SPEC-008 Q4: contract-affecting changes only — but *not* "stricter than LUNC"; LUNC already practices this. Policy (a) had been nominally in force and was violated 25 consecutive times. Label/integer lockstep dropped to avoid colliding LUNM v0.2 with LUNC v0.3.
- [x] Resolve SPEC-008 Q5: no implementer needed — the premise was false. `ih_events` is live in the production matrix, DDL owned by `intergalactic_hub/storage/db.py`, dating to 2026-04-21, three weeks *before* the journal that called it unbuilt. `sessions` promoted into the core; the boundary restated intensionally (`schema.sql` declares 47 tables, the live matrix holds 89).
- [x] Promote `SPEC-008` from `active` to `accepted` (2026-07-21). Question bodies preserved; section renamed to `Resolved questions` per SPEC-004/SPEC-007 house style.
- [ ] Update `03_Format_Spec/LUN-FORMAT_v0.1.md`, `LUN-FORMAT_v0.2.md`, and `LUN-FORMAT_v0.3.md` references to point at SPEC-008 for LUNM. **Timing corrected:** SPEC-008 § Dependencies puts this at `implemented/`, not at acceptance — this line previously said "after acceptance". All three carry the deferral verbatim at line 4.
- [ ] Move SPEC-008 to `implemented` once the four engine changes in § Behavioral changes land: relocate `profile_config` DDL into `schema.sql` (it currently rides the only fail-silent migration path); reserve the `lunm.` prefix on `PUT`/`DELETE /api/profile/config`; add `_seed_lunm_header()`; close the IH matrix-creation gap, where `intergalactic_hub/storage/db.py` can create the file without stamping `application_id`.
- [ ] Build the § 4.4 identity check: a satellite records the master's `lunm.matrix_ulid` at promotion and compares on re-open, refusing only when the key is present and different. Highest-value follow-up SPEC-008 generates — cross-profile conflation is otherwise undetectable.
- [ ] Draft SPEC-009 for full LUNM table DDL ratification. **Scope enlarged by SPEC-008's resolutions:** `schema.sql` declares 47 tables while the live matrix holds 89, so the audit must first inventory the 6+ DDL owners outside `luna/substrate/`. Inherits from Q5 — audit ad-hoc `conversation_turns` writers before ratifying any `sessions` FK; dispose of the vestigial `consciousness_snapshots`; reconcile the v0.3 spec's `nexus_refs` description against the engine's master-pointer-only behaviour for sealed cartridges.
- [ ] Draft SPEC-010 for LUNM migration discipline: idempotency rules and legal in-place changes. **Narrowed:** Q4's bump *triggers* are settled in SPEC-008 § 4.1; SPEC-010 carries the *mechanics* — chiefly that a bump needs an explicit migration branch and must never edit the `user_version` literal, which would fork production matrices at the old value with no detector.

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
