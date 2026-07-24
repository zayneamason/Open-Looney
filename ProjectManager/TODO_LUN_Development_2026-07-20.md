---
doc_type: ledger
status: active
created: 2026-07-20
updated: 2026-07-24
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
and `01_Specs/implemented/SPEC-008_lunm-family-foundation.md` (promoted from `active/` on 2026-07-21;
Engine-implemented 2026-07-24). SPEC-009 → `implemented/` 2026-07-24 (Engine PR #157 / `dd5c3060`).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-011-implemented.md`
(SPEC-011 → implemented after Engine PR #159 / `629679b5`).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-011-accepted.md`
(SPEC-011 Q1–Q4 resolved; promoted to `accepted/`; Engine FI column conformance next).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-010-soak-spec-011-drafted.md`
(SPEC-010 soak clean; SPEC-011 FI DDL drafted).

Session handoff 2026-07-23: `02_Handoffs/HANDOFF_2026-07-24_spec-010-implemented.md`
(SPEC-010 → implemented after Engine PR #158).

Session handoff 2026-07-23: `02_Handoffs/HANDOFF_2026-07-23_spec-009-010-accepted.md`
(SPEC-009/010 accepted; human defaults for migrations 002/003 + LUNM entity owner locked).

Session handoff 2026-07-21: `02_Handoffs/HANDOFF_2026-07-21_spec-008-accepted-spec-009-010-drafted.md`
(SPEC-008 accepted; SPEC-009/010 drafted; open questions since resolved).

Research intake added 2026-07-21:
`/Users/zayneamason/_HeyLuna_BETA/Research/Looney_GeminiConversation_001.md`.

## Reader Prototype

- [x] Fix Reader baseline-drift round 3: `queries::list_extractions` tests already assert post-M-01 Haiku counts (1204/1056/148 claims, 1418 entities, 145 summaries); `cargo test --lib` 58/58 green 2026-07-23. SPEC.md criteria 3–6 updated to match (was stale 512/532/62/176).
- [x] Manual visual verification for Reader v0.3.3 acceptance criteria 18 + 19 — automated shelf verify-by-opening tests green (`verify_fts_term_*`, `verify_*_ulid_*`, `verify_entity_surface_*`, `search_finds_virtue_*`). Full Tauri UI chrome smoke remains recommended when a display is available; recorded in SPEC.md Findings + `08_Journal/2026-07-23.md`.
- [x] Record the manual visual verification result in `06_Prototypes/ReaderPrototype/SPEC.md` and `08_Journal/2026-07-23.md`.

## Cartridge Quality And Audits

- [x] M-01 title truncation audit follow-up closed: PDF parser now merges multi-line title blocks; Meditations cartridge rebuilt; follow-on memory updated.
- [x] Investigate S-01 embedding coverage gap: **classified expected builder policy** (skip sections with no embeddable descendant text). Post-M-01 ratio **149/166** (not 149/176).
- [x] Update the relevant audit/spec note once S-01 is classified — v0.2 + v0.3 audits + `LUN-FORMAT_v0.3.md` Coverage policy one-liner.
- [ ] Keep historical v0.2 limitations alive only where still applicable: Lansing 9.5% baseline measurement and form-feed artifacts are not blocked by v0.3, but should remain documented until separately resolved. **Verified 2026-07-24:** living homes current — `LUN-FORMAT_v0.2.md` §12–13, `LUN-FORMAT_v0.3.md` §8–9, `00_README/README.md`, `08_Journal/2026-05-21.md`. Leave open until PDF recovery / `\x0c` strip (or explicit won’t-fix).

## LUNM Runtime Matrix Specs

- [x] Resolve SPEC-008 Q1: `profile_config`, under a reserved `lunm.*` namespace. Its stated rationale was falsified against the live engine — the table is absent from `schema.sql`, holds 0 rows in every live matrix, and is deletable over HTTP — so three engine preconditions ride along.
- [x] Resolve SPEC-008 Q2: four keys — `lunm.format_version`, **`lunm.matrix_ulid`** (renamed from `profile_ulid` and re-scoped to the file; the genesis hook fires per file, and no profile ULID exists in the engine), `lunm.created_at`, `lunm.engine_version` (declared placeholder). `lunm.schema_fingerprint` deferred to SPEC-009, defined over live `sqlite_master`.
- [x] Resolve SPEC-008 Q3: `MUST`, unqualified — already shipped and test-covered in `promote_to_nexus()`. The unsafe carve-out was dropped (zero precedent repo-wide, one production caller); the umbrella MUST scoped to production paths, `SHOULD` for maintenance tooling.
- [x] Resolve SPEC-008 Q4: contract-affecting changes only — but *not* "stricter than LUNC"; LUNC already practices this. Policy (a) had been nominally in force and was violated 25 consecutive times. Label/integer lockstep dropped to avoid colliding LUNM v0.2 with LUNC v0.3.
- [x] Resolve SPEC-008 Q5: no implementer needed — the premise was false. `ih_events` is live in the production matrix, DDL owned by `intergalactic_hub/storage/db.py`, dating to 2026-04-21, three weeks *before* the journal that called it unbuilt. `sessions` promoted into the core; the boundary restated intensionally (`schema.sql` declares 47 tables, the live matrix holds 89).
- [x] Promote `SPEC-008` from `active` to `accepted` (2026-07-21). Question bodies preserved; section renamed to `Resolved questions` per SPEC-004/SPEC-007 house style.
- [x] Update `03_Format_Spec/LUN-FORMAT_v0.1.md`, `LUN-FORMAT_v0.2.md`, and `LUN-FORMAT_v0.3.md` references to point at SPEC-008 for LUNM. Done 2026-07-24 with SPEC-008 → `implemented/` (Engine PR #156 merged `53a6367b`).
- [x] Move SPEC-008 to `implemented` once the four engine changes in § Behavioral changes land: relocate `profile_config` DDL into `schema.sql`; reserve the `lunm.` prefix on `PUT`/`DELETE /api/profile/config`; add `_seed_lunm_header()`; close the IH matrix-creation gap. **Done 2026-07-24** — Engine PR #156 merged (`53a6367b`); file at `01_Specs/implemented/SPEC-008_lunm-family-foundation.md`.
- [x] Build the § 4.4 identity check: a satellite records the master's `lunm.matrix_ulid` at promotion and compares on re-open, refusing only when the key is present and different. **Done in Engine** via `meta.lunm.bound_matrix_ulid` on promote (PR #156).
- [x] Draft SPEC-009 (2026-07-21) — **rescoped**. Full DDL ratification was not attempted: the surface is 24 DDL-declaring files, not the 6 assumed, so SPEC-009 became *LUNM schema ownership and the table manifest* (single owner per table, static manifest, four-way classification, one conformance test). Per-family DDL ratification defers to SPEC-011+. Original scope note retained: **enlarged by SPEC-008's resolutions:** `schema.sql` declares 47 tables while the live matrix holds 89, so the audit must first inventory the 6+ DDL owners outside `luna/substrate/`. Inherits from Q5 — audit ad-hoc `conversation_turns` writers before ratifying any `sessions` FK; dispose of the vestigial `consciousness_snapshots`; reconcile the v0.3 spec's `nexus_refs` description against the engine's master-pointer-only behaviour for sealed cartridges.
- [x] Draft SPEC-010 (2026-07-21) — *LUNM migration discipline*, centred on fail-loud tiered by SPEC-009 classification. 22 of the engine's 25 migrations wrap DDL in `except Exception` + `logger.debug`, including the one that creates `profile_config`, a format invariant. Original scope note retained: **narrowed:** Q4's bump *triggers* are settled in SPEC-008 § 4.1; SPEC-010 carries the *mechanics* — chiefly that a bump needs an explicit migration branch and must never edit the `user_version` literal, which would fork production matrices at the old value with no detector.

## Looney Data Research Intake

- [ ] Review `Looney_GeminiConversation_001.md` for durable spec candidates; treat it as brainstorm input, not authority. **Deferred 2026-07-23** (optional package item; not blocking).
- [ ] Draft a research note on external provenance and minting: compare manifest hashing, Verifiable Credentials, local signed journals, and public-chain anchoring without putting network/blockchain requirements inside the core `.lun` read path. **Deferred 2026-07-23.**
- [ ] Draft a compression investigation for `.lun` cartridges: compare uncompressed `content`, compressed shadow columns plus FTS, application-layer zstd/lz4, and SQLite extension approaches; preserve FTS/search portability as a first-class constraint. **Deferred 2026-07-23.**
- [ ] Draft a model/runtime-manifest investigation: evaluate whether cartridges should advertise preferred models, context windows, prompt templates, or aperture routing as metadata while keeping model weights and executable runtimes outside portable cartridge files. **Deferred 2026-07-23.**
- [ ] Draft a media/spatial cartridge investigation: compare GeoPackage-style SQLite tiling, image metadata, OCR, bounding boxes, and vector indexes for a future media/mapping family; do not overload the existing LUNM runtime-matrix name. **Deferred 2026-07-23.**
- [ ] Draft an IP/licensing investigation: evaluate encrypted content chunks, local license tokens, signed manifests, buyer fingerprinting, and watermarking tradeoffs for paid cartridges. **Deferred 2026-07-23.**
- [ ] Draft a prompt-assembly/JEPAlike research note: separate near-term deterministic prompt-index retrieval from speculative predictive latent-state models; identify what belongs in Luna Engine runtime state versus portable `.lun` cartridge schema. **Deferred 2026-07-23.**
- [ ] Add a safety-boundary note for any future executable or adaptive cartridge family: unsigned cartridges must never execute code, load model weights unsafely, or gain broad filesystem write access. **Deferred 2026-07-23.**

## Distribution

- [ ] Optional: build a v0.3.x `.dmg` for the Reader now that u64-overflow, bare-name, verify-by-opening, click-through, and M-01 fixes are baked into source. **Deferred 2026-07-23** (optional package item).
- [ ] If a `.dmg` is built, record the build artifact path, source commit, and smoke result in the ledger or dated journal.

## Repo Hygiene

- [x] Update `00_README/README.md`: Current format version = **v0.3 Shipping**; LUNM no longer “wait forever” — points at SPEC-008 accepted + Engine gate for `implemented/`. Folder tree documents `10_Builder/` + `ProjectManager/`.
- [x] Decide whether this repo should get a root `project_organization.json` with `canonical_ledger = "ProjectManager/TODO_LUN_Development_2026-07-20.md"`. **Yes (2026-07-24):** minimal `version` / `policy: warning-only` / `canonical_ledger` at repo root. Allowlists deferred until a checker is ported.
- [x] Decide whether the copied `10_Builder/` subtree should be labeled as a stale/reference snapshot, updated to v0.3, or moved out of the authority path. **Labeled in place** via `10_Builder/STALE.md` (non-authority; pinned `325c68b…`); not deleted, not updated to v0.3.
- [x] Add or update repo guidance so future agents treat top-level specs and audits as authority over stale implementation snapshots — covered by `10_Builder/STALE.md` + README folder note.

## SPEC-009 / SPEC-010 Follow-up

- [x] Resolve SPEC-009 Q1-Q5 (manifest location/format, shadow-table declaration, where the conformance test runs, whether the prefix convention is normative, and the disposition of the `migrations/` directory). Accepted 2026-07-23.
- [x] Resolve SPEC-010 Q1-Q6 (retroactive scope, unknown-classification default, what happens if the integrity report reveals live failures, lint implementation, LUNC symmetry, and path-loaded DDL). Accepted 2026-07-23.
- [x] Decide the fate of `migrations/002_conversation_history.sql` and `003_access_bridge.sql` — **002 historical/superseded** (live matrix has objects; DDL in `schema.sql`); **003 dead**, schedule Engine delete (`access_bridge` / `permission_log` absent). Recorded in SPEC-009 Q5.
- [x] Resolve the `entities` ownership split — **not a LUNM split**: owner is `luna.substrate` / `schema.sql` for the LUNM entity family; `aibrarian_schema.entities` is cartridge-only (name collision). Recorded in SPEC-009 §4.1 + Q5.
- [x] Promote SPEC-009 and SPEC-010 `active → accepted` (2026-07-23). Handoff: `02_Handoffs/HANDOFF_2026-07-23_spec-009-010-accepted.md`.
- [x] Engine SPEC-009 landings (2026-07-24): PR [#157](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/157) merge `dd5c3060` — `lunm_table_manifest.toml`, §4.4 CI conformance, delete `migrations/003`. Snapshot: `04_Audits/AUDIT_2026-07-24_lunm-table-manifest.toml`. SPEC-009 → `implemented/`.
- [x] Engine SPEC-010: integrity report first, then fail-loud `format-invariant` migrations, then remaining tiers — Luna Engine PR [#158](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/158) merge `c5c451fa` (2026-07-24). SPEC-010 → `implemented/`.
- [x] Soak SPEC-010 on live matrix **copy** (2026-07-24): `scripts/soak_spec010_migration_integrity.py` → `[MIGRATION-INTEGRITY] ran=23 noop=2 degraded=0` PASS. Engine `c5c451fa`. Journal: `08_Journal/2026-07-24.md`.

## SPEC-011+

- [x] Draft SPEC-011 (2026-07-24) — *LUNM format-invariant DDL ratification* for the eight SPEC-008 core tables only (`luna.substrate` / `schema.sql`). Evidence: `04_Audits/AUDIT_2026-07-24_lunm-fi-pragma-table-info.md` (live ≡ schema column names). Handoff: `02_Handoffs/HANDOFF_2026-07-24_spec-010-soak-spec-011-drafted.md`.
- [x] Resolve SPEC-011 Q1–Q4 (2026-07-24): Q1 convention-only sessions FK; Q2 amendment-first additive FI columns; Q3 `name\tsql` SHA-256 incl. Appendix A indexes; Q4 always pin Engine SHA. Handoff: `02_Handoffs/HANDOFF_2026-07-24_spec-011-accepted.md`.
- [x] Promote SPEC-011 `active → accepted` (2026-07-24). File: `01_Specs/implemented/SPEC-011_lunm-format-invariant-ddl.md` (later promoted `accepted → implemented`; path corrected 2026-07-24, was stale as `accepted/`).
- [x] Engine SPEC-011 conformance (2026-07-24): PR [#159](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/159) merge `629679b5` — `tests/unit/test_spec011_fi_columns.py`. SPEC-011 → `implemented/`.
