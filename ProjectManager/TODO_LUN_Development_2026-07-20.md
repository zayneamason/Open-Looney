---
doc_type: ledger
status: active
created: 2026-07-20
updated: 2026-08-15
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

Session handoff 2026-08-15: `02_Handoffs/HANDOFF_2026-08-15_spec-014-merged-closeout.md`
(SPEC-014 closeout — merged 2026-07-27 without a PR, so the ledger claimed
"NOT MERGED" for three weeks. Spec promoted to `implemented/`; LUNM Inspector MVP
finally committed; source PDFs gitignored. Two Engine defects found while
verifying and logged in the Engine ledger, not here.)

Session handoff 2026-07-26: `02_Handoffs/HANDOFF_2026-07-26_figure-vision.md`
(Real vision `visual_description` — Engine PR #174 / `c70d8937`. Bundled ledger line
split into four; next is SPEC-014 vision embeddings, GDAL/COG RFC parked on its
recorded trigger.)

Session handoff 2026-07-26: `02_Handoffs/HANDOFF_2026-07-26_figure-retrieval.md`
(Enrichment hits promote to figure results — Engine PR #173 / `b7c02cff`. Next: vision
embeddings, with SPEC-012 Engine WP0 parallel.)

Session handoff 2026-07-26: `02_Handoffs/HANDOFF_2026-07-26_scanned-page-images.md`
(Scanned PDF page-as-image typing — Engine PR #172 / `465b784c`. Remaining order:
assembler/RRF → vision embeddings, SPEC-012 Engine WP0 parallel.)

Session handoff 2026-07-26: `02_Handoffs/HANDOFF_2026-07-26_reader-semantic-search-session.md`
(Reader semantic search UI — bundled offline MiniLM/ONNX via `fastembed`, Keyword/Semantic toggle,
cosine parity verified 1.0000001 vs. Python-built vectors; commit `5018a80`. Agreed next-4 order:
scanned PDF page-as-image → assembler/RRF → vision embeddings, with SPEC-012 Engine WP0 parallel).

Session handoff 2026-07-26: `02_Handoffs/HANDOFF_2026-07-26_reader-figures-session.md`
(Reader v0.3.4 figure display + inspector + hierarchy scroll; Nature-of-Art sample with external media;
Engine PR #171 external sidecars).

Session handoff 2026-07-25: `02_Handoffs/HANDOFF_2026-07-25_spec-013-enrichment-arc.md`
(SPEC-013 spine + enrichment through Engine PRs #164–#170; wiki P5 / `v0.6.0`).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-011-implemented.md`
(SPEC-011 → implemented after Engine PR #159 / `629679b5`).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-012-accepted.md`
(SPEC-012 promoted `active → accepted`; Q1–Q6 locked; Engine WP0+ next).

Session handoff 2026-07-24: `02_Handoffs/HANDOFF_2026-07-24_spec-012-entity-unification-drafted.md`
(SPEC-012 drafted as active LUNM entity-unification architecture; Engine acceptance audit next).

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
- [x] Reader v0.3.4 SPEC-013 consumer (2026-07-26): `get_figure_payload`, figure inspector, `image` node type, hierarchy scroll-to-node, figure enrichment tabs in Extractions. Handoff: `02_Handoffs/HANDOFF_2026-07-26_reader-figures-session.md`.

## Cartridge Quality And Audits

- [x] M-01 title truncation audit follow-up closed: PDF parser now merges multi-line title blocks; Meditations cartridge rebuilt; follow-on memory updated.
- [x] Investigate S-01 embedding coverage gap: **classified expected builder policy** (skip sections with no embeddable descendant text). Post-M-01 ratio **149/166** (not 149/176).
- [x] Update the relevant audit/spec note once S-01 is classified — v0.2 + v0.3 audits + `LUN-FORMAT_v0.3.md` Coverage policy one-liner.
- [ ] Keep historical v0.2 limitations alive only where still applicable: Lansing 9.5% baseline measurement and form-feed artifacts are not blocked by v0.3, but should remain documented until separately resolved. **Verified 2026-07-24:** living homes current — `LUN-FORMAT_v0.2.md` §12–13, `LUN-FORMAT_v0.3.md` §8–9, `00_README/README.md`, `08_Journal/2026-05-21.md`. Leave open until PDF recovery / `\x0c` strip (or explicit won’t-fix).

## SPEC-013 Searchable Figures

- [x] Accept + implement figure spine (Engine PR [#164](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/164) / `01d2fc65`) — Markdown `figure`→`image` + `media_blobs` + FTS.
- [x] PDF embedded images (PR [#165](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/165) / `fa78da70`).
- [x] Bare PNG/JPEG/GIF/WebP builder input (PR [#166](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/166) / `b3022894`).
- [x] Optional figure OCR → `figure.content` (PR [#167](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/167) / `840db392`).
- [x] Rule `media_classification` / closed `media_kind` (PR [#168](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/168) / `a67e7ee9`).
- [x] `visual_description` stub from `figure.content` (PR [#169](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/169) / `5d39c96e`).
- [x] `figure_discourse` + `extraction_context_nodes` (PR [#170](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/170) / `fb8d81cc`).
- [x] External media sidecars for large rasters (PR [#171](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/171) / `03fa7364` merged 2026-07-26).
- [x] Reader figure display + inspector + hierarchy navigate (v0.3.4) — see handoff 2026-07-26.
- [x] Reader semantic search UI (2026-07-26): Keyword/Semantic toggle, bundled offline MiniLM/ONNX
  via `fastembed` (statically linked ONNX Runtime, no dylib bundling needed), cosine scan over
  paragraph+section `embeddings`. Parity-verified against Python-built vectors (cosine 1.0000001).
  Commit `5018a80`. Handoff: `02_Handoffs/HANDOFF_2026-07-26_reader-semantic-search-session.md`.
- [x] Scanned PDF page-as-image typing (2026-07-26): scanned pages emit `figure`→`image`
  + `media_blobs` at 150 dpi marked `meta.page_image`; OCR unchanged at 300 dpi. Default on
  (`--no-page-images` / `--page-image-dpi=N`). All three figure enrichments skip page images;
  `media_kind` vocab untouched. Engine PR [#172](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/172)
  merge `465b784c`. Handoff: `02_Handoffs/HANDOFF_2026-07-26_scanned-page-images.md`.
- [x] Assembler/RRF consumption of `figure_discourse` neighbors (2026-07-26): enrichment
  hits promote to **figure results** rather than being returned as themselves — a hit resolves
  through `extraction_sources` to the figure it anchors, carrying caption + kind + description
  + capped neighbour context. Kills the duplicated-prose evidence rows (29,590 chars of
  `figure_discourse` against 44,503 chars of sentence nodes on the Nature-of-Art cartridge)
  while keeping neighbour prose and `src_hint`-derived `media_kind` as live retrieval paths.
  First runtime read of `extraction_context_nodes`. Engine PR
  [#173](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/173) merge `b7c02cff`.
  Handoff: `02_Handoffs/HANDOFF_2026-07-26_figure-retrieval.md`.
- [x] Richer `visual_description` (2026-07-26): `--figure-vision` sends each figure to
  the Claude vision API and stores the result as the extraction content, falling back to
  the caption rollup stub when unavailable. Opt-in (one API call per figure). Oversize
  rasters downscaled via the new optional `vision` extra (Pillow) — coverage 12/26 → 26/26
  on the Nature-of-Art cartridge. Scanned page-image figures filtered *before* the call.
  Gain is **lexical** (extraction FTS + #173 promotion), not semantic — a description is
  an extraction and never enters `nodes_fts` or the MiniLM embeddings. Engine PR
  [#174](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/174) merge `c70d8937`.
  Handoff: `02_Handoffs/HANDOFF_2026-07-26_figure-vision.md`.
- [x] **SPEC-014 prerequisite — dim-mismatch guard (2026-07-26).** The `zip(a, b)` cosine
  truncation is fixed in both cartridge readers. It was worse than truncation: `dot` ran
  over the zipped prefix while both norms ran over the *full* vectors, so the result was
  an arbitrary depressed float, not a partial cosine. Two further faults found while
  fixing it, each fatal alone: `struct.error` is not a `ValueError`, so a ragged blob
  escaped any data-fault handler; and stored-vs-query agreement does **not** imply
  agreement with `meta.embedding_dim`. Guard covers all three, logs at ERROR before
  raising. Also found: config `embedding_dim` (YAML, default 384) and the cartridge's own
  `meta.embedding_dim` were never cross-checked, so a non-384 cartridge already truncated
  silently — this was never purely latent. Live-smoked on the running backend: a corrupt
  cartridge mounted through the dropbox reports `validation_status: "valid"` (open-time
  validation never inspects `embeddings`), then hybrid search returns keyword results
  while explicit semantic fails, both audible in `Logs/backend.err.log`. Engine PR
  [#175](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/175) merge `c4d60b6a`.
  Handoff: `02_Handoffs/HANDOFF_2026-07-26_vec-dim-guard.md`.
- [x] **SPEC-014 — vision embeddings. MERGED 2026-07-27** via Engine merge commit
  `ad3be249` ("Merge SPEC-014 vision embeddings"), 11 files, +1844/−120, from branch
  `feat/spec-014-vision-embeddings` (tip `83d8c20`, now an ancestor of `main`).
  ⚠ **Merged directly to `main` with no PR** — that is why this line sat at `[~]`
  for three weeks. The repo rhythm (Engine PR → merge → ledger + spec + handoff)
  has no trigger when a merge skips the PR. Watch for this on any future
  no-PR merge. Verified 2026-08-15 by `git merge-base --is-ancestor 83d8c20 main`
  and independently by a fresh cartridge build carrying an `image_embeddings`
  table. Still **zero human review** — merging did not change that.
  Follow-up `a377a327` ("Keep slow tests in default pytest run") resolved the
  owed `slow`-marker question by decision: the ~600 MB CLIP download stays in
  the default `pytest tests/` run rather than being excluded.
  **The format fork needed no governance step after all:** `LUN-FORMAT v0.3:317` reads
  "`embeddings.vector`" and sits under that heading, so its scope was always that one
  table. A second additive `image_embeddings` table is *outside* the invariant, not a
  workaround — no relaxation, no wiki MAJOR, no v0.4, `user_version` stays 3.
  Opt-in `--figure-embed` (local CLIP, no API cost); vision leg inside the default
  `hybrid` search fused through a leg-count-normalised `_rrf_fuse`; `similar()` for
  `figure`/`image` ULIDs only. Proven end-to-end on the real 37 MB Nature-of-Art PDF:
  the vision leg's top hit and the hybrid `vision_score` row are the same node
  (`01KYGRXXA5NQ4THX4Z4GN6CXKX`, cosine 0.2637), 26/26 vectors vs 0 on the control.
  ⚠ Two merge-blockers were found only by the **whole-branch** review, both seam
  defects invisible to ten green per-task reviews: a ~60x score inflation when a lone
  semantic leg was returned unfused, and a missing `.[vision]` install producing a
  *successful* build with zero vectors. Both fixed and re-reviewed.
  ✅ Spec text corrected for the three implementation deviations:
  `sentence-transformers` is in the `vision` extra rather than core; `similar()`
  image-to-image does not load the CLIP text model; build-time loud failure is
  delivered by `_preflight_vision_dependencies()`.
  ⚠ Vision-only search stays **rejected** as a sole strategy
  (`08_Journal/2026-07-24_research-searchable-figures.md:88`).
  Handoff: `02_Handoffs/HANDOFF_2026-07-26_spec-014-vision-embeddings.md`.
- [ ] **SPEC-015 — retrieval score comparability. DRAFTED 2026-08-15, needs acceptance.**
  Promotes SPEC-014 Named follow-up 1 into its own spec, as SPEC-014 said it required.
  A search response publishes one `score` key populated from two unrelated scales:
  extraction rows carry `abs(bm25)` (single digits), node rows carry `_rrf_fuse` output
  (hundredths). `_v03_search` **concatenates** them (`aibrarian_engine.py:2036`) and
  `dataroom_tools.py:124` — the default fan-out — re-sorts the merge on raw `score`, so
  extraction rows structurally dominate every cross-collection search. Re-confirmed
  2026-08-15 on `cartridge.Dragon-Hatchling.v03` (5.47–13.29 vs a uniform 0.0082),
  consistent with SPEC-014's 2026-07-26 measurement on Meditations (5.1933 vs 0.0163).
  Four options costed (extraction-as-leg / `rank_class` discriminator / partitioned
  response shape / per-query min-max — the last **recommended against**, it converts a
  visible scale mismatch into an invisible relevance inversion). Also records a latent
  hazard: the `score > 0.01` leg filters at `:1161`, `:1603`, `:1998` sit *inside* the
  fused-score range, so any refactor moving one post-fusion silently empties hybrid
  results. **5 open questions block acceptance**, chiefly whether extraction-first
  ordering is deliberate policy and whether prompt assembly thresholds on these scores.
  File: `01_Specs/active/SPEC-015_retrieval-score-comparability.md`.
- [ ] Extraction-leg case-fold dedup (small, independent of SPEC-015): `Synaptic plasticity
  [concept]` and `synaptic plasticity [concept]` return as separate rows at an identical
  `13.2932`. Same family as the known `entities` id-space fragmentation — join by
  lowercased name, not id. Named as a SPEC-015 non-goal so it does not enlarge that spec.
- [ ] Regions (`region` node under `image`). Reserved but undefined; needs both a producer
  (PDF bboxes are computed then discarded) and a consumer before the type is worth defining.
- [ ] **GDAL / COG media-family RFC — parked, trigger not met.** The survey is already
  written (`08_Journal/2026-07-24_design-intimate-searchable-figures.md:122-160`: GDAL
  pattern-transfer table + open-source GIS survey), and the research brief records the
  condition: *"Do not open media-family / COG RFC unless maps/artifacts are the workload"*
  (`…_research-searchable-figures.md:246`). Open only when that becomes true; if opened,
  it must not duplicate the existing survey or overload the `LUNM` name (see intake item
  under Looney Data Research Intake).
- File: `01_Specs/implemented/SPEC-013_searchable-figures.md`.
  Handoff: `02_Handoffs/HANDOFF_2026-07-26_reader-semantic-search-session.md` (latest);
  prior: `02_Handoffs/HANDOFF_2026-07-26_reader-figures-session.md`,
  `02_Handoffs/HANDOFF_2026-07-25_spec-013-enrichment-arc.md`.

## LUNM Runtime Matrix Specs

- [x] Draft SPEC-012 (2026-07-24) — *LUNM entity unification*. Scope: one
  canonical LUNM entity row key; graph `ENTITY` nodes, thread rosters, and
  Observatory chips become projections; unknown-default typing and prompt diet
  are included because they prevent identity repair from being repolluted. File:
  `01_Specs/active/SPEC-012_lunm-entity-unification.md`.
- [x] Promote SPEC-012 `active → accepted` (2026-07-24) after read-only Luna
  Engine audit. Locked: canonical key `entities.id`; no `graph_node_id` bridge;
  quarantine DDL; N-way quarantine-only unless allowlist; flag set; DTO owners;
  unknown-default probe corpus. File: `01_Specs/implemented/SPEC-012_lunm-entity-unification.md`
  (promoted through accepted → implemented; stale `accepted/` copy removed 2026-07-25).
  Handoff: `02_Handoffs/HANDOFF_2026-07-24_spec-012-accepted.md`.
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
- [x] LUNM Inspector MVP (2026-07-27): new read-only Tauri prototype at `06_Prototypes/LunmReaderPrototype/` with LUNM-only open contract, SPEC-008/SPEC-011 health checks, FI table inspection tabs, docs, and local validation (`cargo test` 9/9 green; `npm run build` green). It remains separate from the LUNC Reader, which still rejects LUNM.
- [x] LUNM Inspector bundle/smoke pass (2026-07-27): debug `.app` bundle now builds app-only at `06_Prototypes/LunmReaderPrototype/src-tauri/target/debug/bundle/macos/lunm-reader.app`; GUI smoke launched the bundle, opened the live Engine LUNM read-only, and rendered real Memory/Conversations rows. `lsof` showed the main DB handle opened read-only by `lunm-reader`. DMG packaging is not part of the default MVP target.
- [x] LUNM Inspector copied-matrix smoke (2026-07-27): added drag/drop and paste-path open affordances because the native file picker was impractical for `/private/tmp`; rebuilt the `.app` after a package clean so current frontend assets embedded correctly. Paste-path smoke opened `/private/tmp/lunm-reader-smoke/data/user/memory_matrix.lun`, Overview reported `0x4C554E4D` / `user_version 2` / `format 0.1`, and `lsof` confirmed `lunm-reader` held the `/private/tmp` matrix read-only.
- [x] LUNM Inspector conversation usability pass (2026-07-27): Sessions now report computed `actual_turns` from `conversation_turns` instead of the stale `sessions.turns_count`; selecting a session renders chronological role/content turn cards so stored conversation text is directly readable.

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

- [x] Optional: build a v0.3.x `.dmg` for the Reader now that u64-overflow, bare-name, verify-by-opening, click-through, M-01, SPEC-013 figure display, and semantic search fixes are baked into source. **Built 2026-07-27**: `06_Prototypes/ReaderPrototype/src-tauri/target/release/bundle/dmg/lun-reader_0.3.4_aarch64.dmg`.
- [x] Reader `.dmg` build record (2026-07-27): source commit `c616de6` (code clean; docs dirty from this session), artifact `06_Prototypes/ReaderPrototype/src-tauri/target/release/bundle/dmg/lun-reader_0.3.4_aarch64.dmg`, SHA-256 `5b01b783027f5043e04c4e86bf4d059216f5f3ee428d9224e69866325ccaa0c5`, smoke `cargo test` 64/64 green, `npm run build` green, release binary/app 125M, installed `/Applications/lun-reader.app` binary SHA-256 matches bundled app (`a9e3bf217c60e7bf3cbb4e2e2f684046f5873b1c3e47d01bc211bfe7af786ec1`).

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
