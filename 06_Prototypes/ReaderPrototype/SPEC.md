# `.lun` Reader Prototype — Spec

**Status:** implemented (reader app v0.3.4, 2026-07-26 - v0.3 schema support + SPEC-005 ledger display + SPEC-004 reference composer + SPEC-007 SketchedShelf consumer with verify-by-opening + click-through to Reader + SPEC-013 figure display/inspector + offline MiniLM semantic search)
**Date:** 2026-07-26
**Owner:** Ahab
**Validates:** v0.3 cartridge family (`application_id = 0x4C554E43`, `'LUNC'`, `PRAGMA user_version = 3`)
**Reference cartridge:** `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun` (3803 nodes, 2767 extractions, 459 embeddings, 1 ledger genesis row)
**Figure reference cartridge:** `07_Sample_Cartridges/The-Nature-Of-Art-And-Creativity.lun` (542 nodes, 548 extractions, 72 embeddings, 26 figures, 26 images, 26 rows in each SPEC-013 enrichment family)
**Implementation:** Tauri 2 + React 19 + rusqlite 0.32 (bundled) + chrono 0.4 + murmur3 0.5 + unicode-normalization 0.1 + fastembed 5. 64 Rust tests cover the 7-step open contract (LUNM/unknown family, v0.1 / v0.2 rejection with migrate hint, missing/unsupported ledger algorithm, missing append-only triggers, head pointer mismatch), full-tree reconstruction, rich Markdown node-type compatibility, ULID-keyed list_nodes / get_node / list_extractions / get_extraction_counts / get_extraction_sources / search / semantic_search / get_figure_payload / get_ledger_events / get_latest_event_ts, the SPEC-004 reference composer (anchored claim -> Authority 0.75, match_failed -> 0.20, entity -> None, manual-anchor -> 0.90, determinism, batch parity, axis ranges), the SPEC-007 SketchedShelf consumer (MurmurHash3 byte split, LSB-first bitset layout, NFKC+casefold normalization, sketch membership with OOB guard, multi-cartridge filter against fixture), and the v0.3.3 verify-by-opening roundtrip per `SketchKind` (extraction_ulid / node_ulid / fts_term / entity_surface - confirmed + false_positive paths against a populated fixture). The current tests also cover SPEC-013 figure payload resolution from embedded media bytes and semantic-search ranking over stored paragraph/section embeddings. Earlier `queries::list_extractions` baseline-drift failures were resolved on 2026-07-23; the post-M-01 counts are now the expected fixture truth.

**v0.3 carryover:** v0.2 cartridges are no longer supported. Open contract step 3 raises `UnsupportedVersion(2)` with a migrate-first hint pointing at `python -m luna.cartridge.migrate`. The reader does not migrate. v0.2 → v0.3 history lives in [REPORT_2026-05-22_reader-v0.2-tauri-document-reconstruction.md](REPORT_2026-05-22_reader-v0.2-tauri-document-reconstruction.md) and in [`08_Journal/2026-05-22.md`](../../08_Journal/2026-05-22.md).

---

## Purpose

A standalone Tauri desktop app that opens a `.lun` cartridge and lets a user browse it — metadata, document tree, LLM extractions, anchor provenance — and run full-text search via the cartridge's built-in `nodes_fts` table.

The prototype exists to validate the v0.3 contract's central design claim: **any SQLite client can read a cartridge without Luna-specific tooling, including the SPEC-005 annotation ledger and its hash chain**. If the reader can produce a useful UX while depending only on `application_id`, the pragma stack, the meta table, the documented schema, and the ledger's append-only contract (no Luna runtime, no extraction backends, no embedding model, no ledger validator), the format has earned its portability claim. If the reader has to reach across the line into Luna infrastructure to render anything useful, that's a finding for the format spec.

Secondary purpose: give v0.3 audit work a real UI to look at, so spec illustrations and audit findings have something tangible to point at instead of `sqlite3` CLI dumps.

## Current app version

**Reader app v0.3.4** is a prototype-app version, not a `.lun` file-format version.
The file-format target is `.lun` cartridge v0.3 (`PRAGMA user_version = 3`).

v0.3.4 adds (over v0.3.3):

- **SPEC-013 figure/image consumer.** The reader accepts the new `image` node type, resolves each figure's first child image, and reads raster payload metadata from `media_blobs`. Embedded media is returned as base64; external sidecars are resolved relative to the cartridge path and guarded against path escape before being handed to Tauri's `convertFileSrc`. The Document view renders figure images inline when bytes/sidecars are present and falls back to the caption or a "No image payload" affordance when not.
- **Figure inspector drawer.** Clicking a rendered figure or a figure enrichment row opens `FigureInspectorDrawer`, showing the caption, image preview, storage/mime/byte/hash metadata, sidecar path when applicable, and the three SPEC-013 enrichment families (`media_classification`, `visual_description`, `figure_discourse`) sourced through `extraction_sources`.
- **Hierarchy scroll-to-node.** `selectNode` expands the parent chain, and the Document view gives each rendered node a stable DOM anchor. Search hits, figure enrichment rows, and tree selections can land the user on the selected node or nearest rendered ancestor in the reconstructed document.
- **Offline semantic search over stored MiniLM embeddings.** The Rust backend embeds the query with a vendored fp32 ONNX export of `sentence-transformers/all-MiniLM-L6-v2` and scans the cartridge's stored `embeddings` table with cosine similarity across paragraph and section rows. The model and tokenizer files are compiled into the binary with `include_bytes!`; ONNX Runtime is statically linked through fastembed's `ort-download-binaries` feature. There is no runtime model download and no separate model resource to bundle.
- **Keyword/Semantic search toggle.** `SearchPanel` keeps the existing FTS5 keyword path and adds a Semantic mode when `meta.embedding_model = all-MiniLM-L6-v2` and `meta.embedding_dim = 384`. Mismatched or missing embeddings disable semantic mode and surface a specific unsupported-embedding error if invoked from the backend.

v0.3.3 adds (over v0.3.2):

- **SPEC-007 § 7.3.3 verify-by-opening pass.** The SketchedShelf no longer stops at "probable"; after `filter_candidates` returns, every non-`unknown` candidate is opened (via the standard 7-step open contract) and the precise query is run against it, upgrading the badge to **confirmed** or downgrading to **false positive**. New Rust dispatcher `shelf::verify_candidate(path, item, kind)` selects the right precise-query backend per `SketchKind`: `extraction_ulid` → new `queries::get_extraction`, `node_ulid` → cheap `SELECT 1 FROM doc_nodes WHERE ulid = ?`, `fts_term` → existing `queries::search`, `entity_surface` → new `queries::find_extraction_by_content` (NFKC + lowercase + trim normalization to match the SPEC-007 § 7.1.1 sketch contract). Exposed as Tauri command `shelf_verify_candidate(path, item, kind) -> CandidateStatus`. The `CandidateStatus` enum grew two variants (`Confirmed`, `FalsePositive`); the existing `Probable` / `Unknown` continue to mean "verify in flight" / "no sketch — verify is the only signal" respectively. Auto-fires after each Filter; UI updates per-resolution via `Promise.allSettled`.
- **SketchedShelf row click-through to Reader.** Clicking any candidate row drives `useReader.openCartridgeAndNavigate(path, kind, item)`, which chains `openCartridge(path)` → per-kind navigation: `fts_term` → switch to Search view + populate query + run search; `node_ulid` → Tree view + `selectNode(ulid)` (auto-expands parent chain); `extraction_ulid` → Extractions tab + fetch the row via `get_extraction` + open Provenance drawer with sources/ledger/trust; `entity_surface` → Extractions tab + fetch via `find_extraction_by_content` + open Provenance drawer. Cross-mode state passing uses a new tiny third Zustand store `useMode` (~15 LOC) lifted from `App.tsx`'s local `useState`, so the Shelf can flip the top-level mode without prop-drilling. `useReader` and `useShelf` remain isolated — `useShelf.clickedCandidate` calls into `useReader.getState()` and `useMode.getState()` directly per the SPEC.md store-isolation rule (namespacing, not API silos).
- **New Tauri commands** (3): `shelf_verify_candidate(path, item, kind)`, `get_extraction(handle, extraction_ulid)`, `find_extraction_by_content(handle, content)`. The latter two are also useful outside the shelf flow (single-cartridge consumers can call them directly for ULID-keyed extraction navigation).
- **CandidateStatus four-state UI.** ShelfPanel badges: green "confirmed" (verified positive), amber "probable" (verify in flight or just landed), amber "no sketch (verify)" (Unknown — cartridge has no sketch of this kind), gray "false positive" (verified negative; data-quality signal, kept visible rather than dropped). Per-row "verifying…" italic indicator while the verify call is pending. Aggregate counts above the list reflect the post-verify effective status.

v0.3.2 adds (over v0.3.1):

- **SPEC-007 SketchedShelf consumer.** First multi-cartridge surface in the reader. New top-level "Shelf" tab (alongside "Reader") opens N cartridges read-only and runs sketch-based membership pre-filter to answer "which of these cartridges could contain item X of kind Y?" without scanning. Rust consumer lives in `src-tauri/src/shelf.rs` (~390 LOC including tests) and mirrors the engine's `luna.cartridge.sketches` contract: `murmur3_x64_128` with Kirsch–Mitzenmacher double hashing (h1 = low 64 bits, h2 = high 64 bits per SPEC-007 § 7.1.2), LSB-first bitset layout (§ 7.1.3), per-kind normalization (NFKC + casefold + strip for entity surfaces; lowercase for FTS terms; raw bytes for ULIDs), defensive OOB bit-index guard. Three new Tauri commands: `open_shelf(paths)` (atomic — fails the whole shelf if any cartridge fails the 7-step open contract), `close_shelf()`, `shelf_filter_candidates(item, kind)`. Frontend consumes through a thin `src/shelf.ts` adapter (swap point for a future JS-side consumer); state lives in a separate `useShelf` Zustand store to keep single-cartridge `ReaderState` clean. Demo surface in `ShelfPanel.tsx`: multi-file picker → kind dropdown (4 options) + term input → candidate list with per-row badge (probable / no sketch — verify). Definite-not matches omitted. No click-through to single-cartridge view in this slice (follow-up). No verify-by-opening pass — that's the caller's responsibility per SPEC-007 § 7.3.3. Single-cartridge mode untouched.

v0.3.1 adds (over v0.3.0):

- **SPEC-004 reference composer.** Rust backend ships the canonical reference composer (`composer_id = "lun.format/reference-v1"`, `composer_version = "1.0.0"`, `spec_version = "0.4"`) implementing SPEC-004 §4.3 piecewise: Authority (anchor_status base + anchor_method bonus + clamped logprob bonus), Temporal (180-day half-life exponential decay), Contestation (clamped 1 − 0.30·disputes − 0.50·filtered + 0.15·reconciled), Resonance (1 − 0.5^distinct_actors). Exposed as `compose_trust_vector` + `compose_trust_vectors_batch` Tauri commands. Frontend consumes through a thin `src/trust.ts` adapter — the swap point for any application that later wants a JS-side composer.
- **Trust section in Provenance drawer.** Selecting a claim renders the 4-axis TrustVector per SPEC-004 §4.4 display affordances above the Sources section: Authority as saturation bar + numeric, Contestation as amber warning chip when <0.5, Temporal as freshness label + numeric, Resonance as blue ✓ chip when >0.4. NULL axes render as "—" (never substitute defaults). Composer tooltip surfaces `composer_id@composer_version` per the cross-composer comparison rule.
- **AuthorityBar in Extractions panel rows.** Each claim/summary row shows a compact saturation bar next to the AnchorBadge so users see trust at a glance without clicking. Loaded via `compose_trust_vectors_batch` after the extraction list resolves; entities skip since Authority is always None for `anchor_status='unknown'`.

v0.3.0 adds:

- **SPEC-005 ledger display.** Provenance drawer surfaces `annotation_ledger` events for the selected extraction's ULID — `event_type` badge (color-coded per type), `actor_role` + `display_name` (joined from `annotation_actors`), formatted `entry_ts`, and a collapsible canonical-JSON payload view with truncated `entry_hash`. Empty list when no ambassador upgrades have been written yet (the expected state for v0.3 cartridges built by engine `407122f` — only the 2 genesis `meta` rows exist until slice #2 wires ambassador-upgrade ledger writes).
- **Open contract additions for SPEC-005.** `check_ledger_meta()` requires `meta.ledger_hash_algorithm = 'sha256'` plus the three ledger pointer keys; `fast_open_ledger_check()` is an O(1) post-open verification that both append-only triggers exist and that `meta.ledger_head_seq` / `meta.ledger_head_hash` agree with `MAX(seq)` and the entry_hash at that row.
- **SPEC-002 D5 integer-rowid removal.** All cross-table identity is ULID. `DocNode.id`, `Extraction.id`, `DocNodeBrief.id`, `parent_id` fields, and `SearchHit.node_id` are removed from both the Rust types and the TypeScript interfaces. `doc_nodes.id INTEGER` survives only as FTS5's `content_rowid`; the search query joins back to surface the user-facing ULID.
- **C-01 audit rename.** Reader queries `extraction_sources` / `extraction_context_nodes` (was `claim_sources` / `claim_context_nodes`); column FKs are `extraction_ulid` / `node_ulid`; Tauri command renamed `get_claim_sources` → `get_extraction_sources`.

v0.2.0 carryover (the document-reconstruction surface, unchanged in shape):

- `Document` tab loads the full `doc_nodes` tree through `list_all_nodes`.
- Recursive renderer reconstructs reading order from `parent_ulid` + `position`.
- Renderer handles sections, page markers, paragraphs, blockquotes, lists, figures, images, tables, rows, and cells.
- Rust and TypeScript node-type models accept every node type emitted by the current builder (`document`, `section`, `paragraph`, `sentence`, `list`, `list_item`, `figure`, `image`, `table`, `row`, `cell`).
- Tree labels derive useful titles from `content` or `meta_json` before falling back to generic labels.

## Out of scope (v0.3.4)

The prototype is deliberately small. Explicitly deferred:

- **Annotation writes.** Reading the ledger is in scope; writing ledger events from the reader is not. SPEC-005 ambassador-upgrade wiring is engine-side work. The reader displays whatever the engine has written.
- **Cross-cartridge semantic fusion.** v0.3.4 searches one cartridge's stored text embeddings. Cross-cartridge hybrid ranking and figure-vision embedding fusion are engine/search-assembler work, not Reader UI work in this slice.
- **Cross-cartridge navigation.** `nexus_refs` is now an active table in v0.3 but currently empty in the Meditations reference cartridge. The reader does not display the table.
- **Migration.** The reader refuses pre-v0.3 cartridges with a clear error pointing at `python -m luna.cartridge.migrate`. It does not perform the migration itself.
- **Multi-cartridge libraries.** One cartridge open at a time.
- **Runtime-matrix family.** `application_id = 'LUNM'` files are rejected with a `WrongFamilyError`-equivalent message.

## Repo character note

This prototype lives at `06_Prototypes/ReaderPrototype/`, inside a repo whose README declares it "specification and research only — implementation lives in the main Luna codebase." Placing the reader here is the second crossing of that line (the first was `10_Builder/`).

**Resolved 2026-05-23 (Ahab).** The crossings are intentional and stay. The research repo keeps its "spec + research" character with `06_Prototypes/` and `10_Builder/` as explicit carve-outs. The repo also stays outside git — research artifacts don't need version history; the engine repo provides that for code that will ship.

**The reader's long-term home is inside Luna.** Luna is planned as a Tauri app for alpha/beta, and the reader will be absorbed as a tab or feature module within Luna's main shell (a "Nexus" surface). The reader is therefore not a throwaway prototype — it's *future Luna code in incubation*. Factor accordingly:

- Rust modules (`shelf.rs`, `trust.rs`, `cartridge.rs`, `queries.rs`, etc.) should stay library-shaped so Luna's Tauri app can depend on this crate. `lib.rs` already produces `rlib + staticlib + cdylib`; keep it that way.
- React components (`ShelfPanel`, `ProvenanceDrawer`, `TrustBadges`, `DocTree`, etc.) should stay portable — own minimal global state, render under any parent route.
- Zustand stores (`useReader`, `useShelf`) are namespaced enough to coexist with Luna's stores when absorbed.
- The throwaway glue is `App.tsx`'s `Mode` toggle and `lib.rs`'s `generate_handler![]` registration. Both become Luna's responsibility when the time comes.

Incubation continues here until Luna's Tauri shell exists; migration happens then.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Shell | Tauri 2.x | Native file picker + file:// access; small bundle vs Electron. |
| Backend | Rust + `rusqlite` (with `bundled` feature) | Direct SQLite over `.lun`. Bundled SQLite avoids OS-version variance. |
| Frontend framework | React 19 + Vite + TypeScript | Matches Luna's frontend ("Eclissi"). |
| Styling | Tailwind CSS | Matches Luna. |
| State | Zustand | Matches Luna. |
| Markdown / katex | `react-markdown` + `katex` (only if needed for extraction content) | Matches Luna; optional. |

**Note on `sql.js`.** Luna's frontend uses `sql.js` (WASM SQLite in-browser) because Luna is a pure web app, not a Tauri app. This prototype is Tauri-wrapped, so it uses native `rusqlite` instead. `sql.js` is not included.

## File layout

```
06_Prototypes/ReaderPrototype/
├── SPEC.md                          # this file
├── package.json                     # vite + react + tailwind + zustand
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
├── index.html
├── src-tauri/
│   ├── tauri.conf.json
│   ├── Cargo.toml                   # rusqlite (bundled), tauri, serde
│   ├── build.rs
│   └── src/
│       ├── main.rs                  # tauri::Builder, command registry
│       ├── lib.rs                   # Tauri command surface
│       ├── cartridge.rs             # CartridgeHandle, open/validate (7-step gate incl. ledger)
│       ├── queries.rs               # SQL helpers (doc_nodes, extractions, figures, FTS, semantic search, annotation_ledger)
│       ├── embedder.rs              # vendored MiniLM query embedding via include_bytes! ONNX/tokenizer files
│       ├── types.rs                 # ULID-keyed Rust types mirrored in src/types.ts
│       └── error.rs                 # ReaderError enum, mapped to JS
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── store.ts                     # zustand: openCartridge, currentNode, figures, search
    ├── api.ts                       # invoke() wrappers, types
    ├── nodeDisplay.ts               # shared node labels, colors, preview helpers
    ├── types.ts                     # shared shape with Rust serde
    ├── components/
    │   ├── FilePicker.tsx
    │   ├── Header.tsx               # title, source, word_count, build date
    │   ├── DocumentView.tsx         # full document reconstruction view incl. figures/images
    │   ├── DocTree.tsx              # collapsible document-node tree
    │   ├── ExtractionsPanel.tsx     # claim/entity/summary tabs + filters
    │   ├── SearchPanel.tsx          # Keyword/Semantic input + result list
    │   ├── AnchorBadge.tsx          # status pill per LUN-FORMAT v0.2 display contract
    │   ├── FigureInspectorDrawer.tsx# figure media + SPEC-013 enrichments
    │   └── ProvenanceDrawer.tsx     # claim -> extraction_sources -> doc_nodes nav
    └── styles.css
```

## Open contract (cartridge validation)

When a user picks a file, the Rust backend runs validation that mirrors `luna.cartridge.validation.validate_cartridge_open` — but implemented in Rust against `rusqlite` rather than importing Python. The contract is the same:

1. SQLite header (`SQLite format 3\0`) at offset 0 — else `NotASqliteFile`.
2. `PRAGMA application_id = 0x4C554E43` — else `WrongFamily { actual_id }` with text pointing at the runtime-matrix family or unknown.
3. `PRAGMA user_version = 3` — else `UnsupportedVersion { actual }`. For `user_version` in `{1, 2}`, the error text instructs the user to run `python -m luna.cartridge.migrate <path>` (the reader does not migrate).
4. `meta.cartridge_kind IN ('knowledge')` — else `UnsupportedCartridgeKind { actual }`.
5. `meta.logprob_base = 'e'` AND `meta.logprob_attribution = 'response_level'` — else `UnsupportedAttribution { ... }`.
6. (SPEC-005, new in v0.3) `meta.ledger_hash_algorithm = 'sha256'` AND `meta.ledger_genesis_ulid`, `meta.ledger_head_seq`, `meta.ledger_head_hash` all present — else `MissingRequiredMeta { key }` or `UnsupportedHashAlgorithm { actual }`.
7. (SPEC-005, new in v0.3 — `fast_open_ledger_check()`, O(1)) Both `annotation_ledger_no_update` and `annotation_ledger_no_delete` triggers exist; `meta.ledger_head_seq` matches `MAX(seq)` in `annotation_ledger`; `meta.ledger_head_hash` matches the `entry_hash` at that row — else `LedgerIntegrity { detail }`. Full chain re-verification is opt-in via the engine's `lun fsck --ledger`; the reader stops at the head-pointer check to keep open latency O(1).

If validation passes, the Rust side stores a `CartridgeHandle` (an opened `rusqlite::Connection` with `PRAGMA query_only = 1` set) in a `Mutex<HashMap<HandleId, Connection>>` keyed by a u64 handle ID returned to the frontend.

## Tauri command surface

The frontend talks to Rust exclusively through these commands. All are read-only.

| Command | Args | Returns |
|---|---|---|
| `open_cartridge` | `path: PathBuf` | `Result<HandleId, ReaderError>` |
| `get_meta` | `handle: HandleId` | `Meta` (full meta table as struct, including 4 ledger keys, + rest-as-map) |
| `list_nodes` | `handle, parent_ulid: Option<String>, type_filter: Option<NodeType>, limit, offset` | `Vec<DocNode>` |
| `list_all_nodes` | `handle` | `Vec<DocNode>` for full document reconstruction |
| `get_node` | `handle, node_ulid: String` | `DocNode` (with parent chain + children counts) |
| `get_figure_payload` (v0.3.4) | `handle, figure_ulid: String` | `FigurePayload` — validates that the target is a `figure`, resolves the first child `image`, returns embedded media bytes as base64 or a guarded sidecar path, and joins SPEC-013 enrichment rows anchored to the figure. |
| `list_extractions` | `handle, type_filter: Option<ExtractionType>, anchor_status_filter: Option<AnchorStatus>, limit, offset` | `Vec<Extraction>` |
| `get_extraction` (v0.3.3) | `handle, extraction_ulid: String` | `Option<Extraction>` — single-row lookup by ULID; `None` means not in cartridge (the SPEC-007 verify-by-opening false-positive signal). |
| `find_extraction_by_content` (v0.3.3) | `handle, content: String` | `Option<Extraction>` — first `type='entity'` row where `LOWER(TRIM(content))` matches the NFKC-normalized needle. Backs the `entity_surface` verify-by-opening path. |
| `get_extraction_sources` | `handle, extraction_ulid: String` | `ExtractionSourcesResult` (sources + context, both ULID-joined to `doc_nodes`) |
| `get_ledger_events` | `handle, target_ulid: String` | `Vec<LedgerEvent>` (SPEC-005, joined to `annotation_actors` for `display_name`) |
| `get_latest_event_ts` | `handle, target_ulid: String` | `Option<i64>` (SPEC-004 Temporal-axis input; None when target has no events) |
| `search` | `handle, query: String, limit` | `Vec<SearchHit>` (FTS5 joined to `doc_nodes.ulid`) |
| `semantic_search` (v0.3.4) | `handle, query: String, limit` | `Vec<SearchHit>` (`source = "semantic"`, `level = paragraph \| section`) after checking that cartridge embeddings match the bundled `all-MiniLM-L6-v2` / 384-dim query model. |
| `close_cartridge` | `handle: HandleId` | `()` |
| `open_shelf` (SPEC-007, v0.3.2) | `paths: Vec<String>` | `ShelfSummary { count, paths, sketches_per_cartridge }`; atomic — fails the whole shelf if any cartridge fails the 7-step contract |
| `shelf_filter_candidates` (SPEC-007, v0.3.2) | `item: String, kind: SketchKind` | `Vec<CandidateResult { path, status: probable \| unknown }>` — definite misses omitted; verify-by-opening pass upgrades these to `confirmed` / `false_positive` (v0.3.3) |
| `shelf_verify_candidate` (SPEC-007 § 7.3.3, v0.3.3) | `path: String, item: String, kind: SketchKind` | `CandidateStatus` (`confirmed` or `false_positive`); opens the cartridge, runs the precise query per kind, closes — independent of any open shelf |
| `close_shelf` (SPEC-007, v0.3.2) | — | `()` |

**Keyword search implementation.** `SELECT n.ulid, snippet(nodes_fts, 0, '<mark>', '</mark>', '…', 32) AS snippet, rank FROM nodes_fts JOIN doc_nodes n ON n.id = nodes_fts.rowid WHERE nodes_fts MATCH ? ORDER BY rank LIMIT ?` — uses FTS5's built-in snippet and rank, with a join to surface the user-facing ULID rather than the FTS-only integer rowid (Strategy A; see [LUN-FORMAT_v0.3.md](../../03_Format_Spec/LUN-FORMAT_v0.3.md) § "Open questions" Q1).

**Semantic search implementation.** `semantic_search` rejects cartridges whose `meta.embedding_model` / `meta.embedding_dim` do not match `all-MiniLM-L6-v2` / `384`, embeds the query through `embedder.rs`, scans `embeddings(node_ulid, level, vector)`, computes cosine similarity over raw little-endian f32 blobs, and returns the top K hits as `SearchHit { source: "semantic", level }`. It reconstructs paragraph/section display text from descendant content so snippets reflect the text that was embedded. `rank = 1.0 - cosine_similarity`, keeping the UI's "lower is better" rank convention consistent with FTS5.

## UI surfaces (v1)

The app is single-window, three-panel:

```
┌─ Header ──────────────────────────────────────────────────────────────┐
│ Marcus-Aurelius-Meditations.pdf · 76651 words · v0.3 · built 2026-05-22│
├──────────────────┬────────────────────────┬────────────────────────────┤
│ Document tree    │ Main pane              │ Provenance drawer (slide-in)│
│ ─ document       │ Tabs: [Doc] [Tree]     │ For selected claim:         │
│   ▾ section #1   │       [Extract.] [Search]│  ← claim text             │
│     ▾ paragraph  │                        │  → source nodes (1..n)      │
│       sentence   │ Active tab content     │  anchor_method · anchored_at│
│     ▸ paragraph  │ scrolls here           │  related context_nodes      │
│   ▸ section #2   │                        │  ─ Annotations (SPEC-005) ─ │
│ …                │                        │  ledger events (event_type, │
│                  │                        │  actor, ts, payload, hash)  │
└──────────────────┴────────────────────────┴────────────────────────────┘
```

### Header

Pulled directly from `meta`:
- `title` (with truncated-title warning if length < 20 chars — flags the Meditations parser issue without failing)
- `source_filename` · `source_format` · word_count
- `format_version` badge ('v0.3' green, anything else yellow)
- `created_at` formatted relative + absolute

### Document tree (left rail)

Hierarchical view of `doc_nodes`. Lazy-loaded children (each section/paragraph fetches its children only on expand). Click selects → main pane shows the node's content + outgoing FTS context.

Node-type chips: `document`, `section`, `paragraph`, `sentence`, `list`, `list_item`, `figure`, `image`, `table`, `row`, `cell`. Section depth via `meta_json.level` (markdown sources) or just hierarchy (PDF sources, where level is NULL — Meditations case). Selecting a node expands its parent chain and, when the Document tab is active, scrolls to the rendered node or nearest rendered ancestor.

### Document view (main pane, tab)

The `Document` tab is the default view in reader app v0.2.0. It calls
`list_all_nodes`, groups rows by `parent_ulid`, sorts siblings by `position`, and
recursively renders the tree back into document reading order.

This is not pixel-perfect PDF layout. It is structural reconstruction: headings,
page sections, paragraphs, blockquotes, lists, figures, and tables are rendered
from the cartridge's semantic node tree. Paragraph containers with NULL content
are reconstructed from sentence children.

Figure nodes render inline. For SPEC-013 cartridges, `FigureBlock` calls
`get_figure_payload`, displays embedded image bytes or guarded external
sidecars, and opens the Figure inspector on click. Figure captions remain
readable even when no raster payload is available.

### Extractions panel (main pane, tab)

Six sub-tabs: **Claims**, **Entities**, **Summaries**, **Media kind**, **Visual desc**, **Discourse** (counts in tab labels).

Each row shows: anchor-status pill, extraction content (truncated, expandable), extraction_method, claim ULID (mono font, click to copy).

Filters: anchor_status (multi-select), extraction_method, "has source" / "no source".

Click a claim → opens Provenance drawer.

Click a figure enrichment row → follows its first `extraction_sources` figure,
selects the figure in the Document view, and opens the Figure inspector.

### Search panel (main pane, tab)

Keyword/Semantic segmented control plus one text input. Hitting Enter calls
`search` in Keyword mode or `semantic_search` in Semantic mode. Keyword results
are FTS5 hits with rank-ordered snippets (`<mark>`-highlighted matches).
Semantic results are stored-embedding matches with `source = "semantic"` and
`level = paragraph | section`. Click a result → tree expands to the parent path,
selects the matching node, and switches to the Document view.

### Anchor badge (component)

Per `LUN-FORMAT_v0.3.md` "Display invariants for anchor_status":

| anchor_status | Badge | Color |
|---|---|---|
| anchored | "✓ anchored" | green |
| synthesized | "synthesized from N sources" | purple |
| match_failed | "⚠ unanchored" | yellow |
| filtered | "filtered: {reason}" | gray; hidden by default, toggle to show |
| unknown | "? unknown (data quality)" | red (v0.3 readers should never see this on claims; entities ok) |

### Provenance drawer (right rail)

Slides in when a claim is selected. Three sections, top to bottom:

1. **Sources** — claim → `extraction_sources` → `doc_nodes` chain, ULID-joined. Click a source to navigate the tree to its node.
2. **Context nodes** — for `synthesized` claims, `extraction_context_nodes` with relevance values.
3. **Annotations (SPEC-005, new in v0.3)** — `annotation_ledger` events whose `target_ulid` matches the selected claim's ULID. Each row shows: color-coded `event_type` badge (green for `claim_anchored`, amber for `claim_disputed`, gray for `claim_filtered` / `meta` / `cartridge_imported`, blue for `claim_reconciled`, purple for `summary_overridden`, indigo for `cartridge_reviewed`), `actor_role`, `display_name` from `annotation_actors` (falls back to the last 8 chars of `actor_id` when no actor row exists), formatted `entry_ts`, and a collapsible canonical-JSON payload view with a truncated `entry_hash`. Empty list is the expected state for v0.3 cartridges built by engine `407122f` because ambassador-upgrade ledger wiring (slice #2) has not landed yet.

For `match_failed` claims the drawer also displays the raw `anchor_reason` above the Sources section.

### Figure inspector (right rail)

Slides in when a figure is selected. The drawer displays the figure ULID,
caption, image preview when available, media storage/mime/byte/hash metadata,
guarded external path when applicable, and SPEC-013 enrichment rows anchored to
the figure. It shares the right-rail slot with the Provenance drawer; selecting
a claim closes the Figure inspector and selecting a figure closes claim
provenance.

## State management (Zustand)

```ts
type ReaderState = {
  cartridge: { handle: HandleId; path: string; meta: Meta } | null;
  selectedNode: DocNode | null;
  selectedClaim: Extraction | null;
  claimSources: ExtractionSourcesResult | null;
  ledgerEvents: LedgerEvent[] | null;       // SPEC-005, loaded with claimSources
  selectedFigureUlid: string | null;         // SPEC-013 figure inspector
  figurePayload: FigurePayload | null;
  treeExpansion: Set<string>;               // ULID keys; was Set<number> in v0.2
  childrenByParent: Record<string, DocNode[]>;  // ULID keys; was Record<number,_> in v0.2
  searchMode: 'keyword' | 'semantic';
  search: { query: string; results: SearchHit[]; loading: boolean };
  view: 'document' | 'tree' | 'extractions' | 'search';

  // actions
  openCartridge: (path: string) => Promise<void>;
  closeCartridge: () => Promise<void>;
  selectNode: (ulid: string) => Promise<void>;
  selectClaim: (claim: Extraction) => Promise<void>;
  selectFigure: (figureUlid: string) => Promise<void>;
  setSearchMode: (mode: 'keyword' | 'semantic') => void;
  runSearch: () => Promise<void>;
};
```

One global reader store plus the separate Shelf mode/store added in v0.3.2.
Matches Luna's pattern. `selectClaim` loads `extraction_sources`,
`annotation_ledger`, and TrustVector data in parallel. `selectFigure` loads
`get_figure_payload` and clears claim provenance so the right rail has one
active inspector at a time. `runSearch` dispatches to FTS5 or semantic search
based on `searchMode`.

## Error UX

Rust `ReaderError` → JS error type → toast banner with mitigation text:

| Error | Banner text |
|---|---|
| `NotASqliteFile` | "Not a SQLite file. Pick a `.lun` cartridge." |
| `WrongFamily(LUNM)` | "This is a runtime-matrix file (`LUNM`), not a knowledge cartridge. The reader only opens `LUNC` cartridges." |
| `WrongFamily(other)` | "Unknown application_id: `0x{hex}`. Expected `0x4C554E43`." |
| `UnsupportedVersion(1\|2)` | "v0.{1\|2} cartridge. Migrate first: `python -m luna.cartridge.migrate {path}` — then reopen." |
| `UnsupportedVersion(n)` | "Unsupported user_version: {n}. This reader supports v0.3 only." |
| `UnsupportedAttribution(s)` | "logprob_attribution = `{s}`. v0.3 reader only supports `response_level` / `e`." |
| `UnsupportedCartridgeKind(s)` | "cartridge_kind = `{s}`. v0.3 supports `knowledge` only." |
| `MissingRequiredMeta(key)` | "Cartridge missing required `meta.{key}`. Build may be incomplete or the SPEC-005 ledger genesis row was not inserted." |
| `UnsupportedHashAlgorithm(s)` | "meta.ledger_hash_algorithm = `{s}`. v0.3 reader only supports `sha256`." |
| `LedgerIntegrity(detail)` | "Ledger integrity check failed: {detail}. Run `lun fsck --ledger` against this cartridge for diagnostics." |
| `UnsupportedEmbeddingModel(model, dim)` | "This cartridge's embeddings were built with a different model ({model}, dim {dim}) than the bundled query model. Semantic search is unavailable for this cartridge." |
| `EmbeddingError(message)` | "Semantic search embedding failed: {message}." |
| `SqliteError(e)` | Show raw error, suggest opening in `sqlite3` CLI for diagnostics. |

## Acceptance criteria (v0.3.4 done)

A v0.3 prototype is shipped when, against `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`:

1. **File open.** Native picker opens the cartridge; header populates with title, source filename, word count, build date; format-version badge reads `v0.3` in green.
2. **Document reconstruction.** Default `Document` tab renders the full cartridge in reading order from `doc_nodes.parent_ulid` and `doc_nodes.position`, including section/page boundaries and sentence-child paragraph text.
3. **Tree.** Expanding the root document reveals 128 direct section children and **166** sections total across the full tree (post-M-01; was 176 before the PDF title-block merge). Expanding a section reveals its paragraphs. Expanding a paragraph reveals its sentences. Selected sentence content displays in the main pane.
4. **Extractions: Claims tab.** Shows **1204** rows (post-M-01 Haiku rebuild; v0.2 audit baseline was 512). Filter to `anchor_status = anchored` shows **1056**. Filter to `match_failed` shows **148**. Filter to `unknown` shows 0 (per SPEC-001 hard gate).
5. **Extractions: Entities tab.** Shows **1418** rows; all carry `unknown` badge per SPEC-001 (entities scoped out of v0.3 anchor classification). UI explains this in a tooltip rather than treating it as a data-quality flag.
6. **Extractions: Summaries tab.** Shows **145** rows.
7. **Provenance.** Selecting an anchored claim shows the source node(s) from `extraction_sources` in the right drawer. Selecting a `match_failed` claim shows the `anchor_reason` text.
8. **Annotation ledger display.** Selecting any claim renders an "Annotations" section in the drawer. For a v0.3 cartridge built by engine `407122f`, the per-claim event list is empty (no ambassador upgrades have been written yet) and shows the "Ambassador upgrades will appear here once the engine writes them" placeholder. Once slice #2 lands, the same surface displays each `claim_anchored` / `claim_disputed` / etc. event with color-coded badge, actor, timestamp, and collapsible payload.
9. **Keyword search.** Query "virtue" in Keyword mode returns FTS5 hits ranked by relevance, with `<mark>`-highlighted snippets; each hit surfaces the user-facing ULID (last 8 chars in the hit row, full ULID in the title attribute); clicking a result navigates the tree to the parent path, selects the matching node, and lands in the Document view.
10. **Rejection paths.**
    - Opening a non-SQLite file → `NotASqliteFile` banner.
    - Opening a `LUNM` runtime matrix → `WrongFamily(LUNM)` banner.
    - Opening a v0.1 or v0.2 cartridge → `UnsupportedVersion({n})` banner with the migrate command.
    - Opening a cartridge with `meta.ledger_hash_algorithm` missing or not `sha256` → `MissingRequiredMeta` / `UnsupportedHashAlgorithm` banner.
    - Opening a tampered cartridge where `meta.ledger_head_seq` no longer matches `MAX(seq)` → `LedgerIntegrity` banner pointing at `lun fsck --ledger`.
11. **No Luna code.** `Cargo.toml` and `package.json` contain zero `luna`-named dependencies. Reader builds and runs from a clean checkout without the engine repo present.
12. **Read-only enforced.** Rust side opens the connection with `PRAGMA query_only = 1`. Any frontend attempt to mutate would fail at the SQLite layer.
13. **No integer-rowid leakage.** Neither the frontend nor any Tauri command surface exposes `doc_nodes.id` or `extractions.id` to the UI. The integer rowid survives only as FTS5's `content_rowid` per Strategy A; the search query joins back to `doc_nodes.ulid` before returning.
14. **Trust composition (SPEC-004).** Selecting an anchored claim from the v0.3 Meditations reference cartridge surfaces a TrustVector in the drawer with Authority=0.75, Temporal close to 1.0 (or decayed proportionally if opened later than 2026-05-22), Contestation=1.0, Resonance=0.0. The same claim's ExtractionsPanel row shows a green saturation bar at ≈0.75 position. The composer tooltip reads `lun.format/reference-v1@1.0.0`.
15. **Ambassador-upgrade trust delta.** Opening `/tmp/meditations-slice2-test.lun` (the slice #2 fixture: one ambassador-upgraded extraction `01KS76F9RD1FHNZ8BXSQT4ZQF2`) and selecting that extraction shows Authority=0.90 (0.75 base + 0.15 manual-method bonus) vs. a regular auto-anchored claim's 0.75 — visible proof that ambassador upgrades move the trust signal. The drawer's Annotations section also shows the corresponding `claim_anchored` ledger event from slice #2.
16. **Shelf opens + filters (SPEC-007, v0.3.2).** Switching to the "Shelf" tab and opening two cartridges (e.g. the sketch-bearing fixture from the engine slice's E2E test + a freshly rebuilt Meditations) lists both with their populated sketch kinds. Selecting `fts_term` + entering "virtue" and clicking Filter returns a candidate list with the matching cartridge(s) badged "probable" and any cartridge without an `fts_term` sketch badged "no sketch (verify)". A query for a definitely-absent term returns an empty list with the "definitely not" count reflecting the full shelf size.
17. **Shelf doesn't break single-cartridge mode.** Switching from Shelf → Reader and opening a single cartridge via the existing FilePicker still works; ProvenanceDrawer + TrustBadges + AuthorityBar all render against the open cartridge. The two modes share no state and don't interfere.
18. **Verify-by-opening upgrades + downgrades badges (SPEC-007 § 7.3.3, v0.3.3).** Against the rebuilt Meditations cartridge: filtering `fts_term = "virtue"` first shows the candidate badged "probable" with a "verifying…" indicator, then within a second the badge upgrades to green "confirmed" and the indicator disappears. Filtering `extraction_ulid = "01ZZZZZZZZZZZZZZZZZZZZZZZZ"` (a ULID not in the cartridge but accepted as "probable" by the bloom filter sketch with low FPR) shows the candidate downgrading to gray "false positive" after verify completes. The aggregate count line reflects the post-verify effective status (e.g., "1 confirmed" instead of "1 probable").
19. **Click-through per kind (v0.3.3).** Clicking a confirmed `fts_term` row switches to the Reader tab, opens the cartridge through the standard 7-step contract, selects the Search view, populates the query input, and renders the FTS5 results with `<mark>`-highlighted snippets. Clicking a confirmed `node_ulid` row opens to the Tree view with `selectNode` auto-expanding the parent chain. Clicking a confirmed `extraction_ulid` row opens to the Extractions tab, fetches the row via `get_extraction`, selects it, and opens the Provenance drawer with sources, ledger events, and trust composed. Clicking a confirmed `entity_surface` row opens to the Extractions tab filtered to entities, finds the row via `find_extraction_by_content`, selects it, and opens the Provenance drawer.
20. **Figure display + inspector (SPEC-013, v0.3.4).** Opening `The-Nature-Of-Art-And-Creativity.lun` renders inline figures from the `figure` -> `image` hierarchy. Clicking a figure opens the Figure inspector, displays the image payload or guarded external media URL, shows storage/mime/byte/hash metadata, and lists the figure's `media_classification`, `visual_description`, and `figure_discourse` enrichments. The Extractions panel exposes the three enrichment tabs; clicking an enrichment row navigates to the source figure and opens the inspector.
21. **Semantic search (v0.3.4).** On any cartridge whose meta advertises `embedding_model = all-MiniLM-L6-v2` and `embedding_dim = 384`, the Semantic mode embeds the user's query offline through the bundled ONNX model, scans stored paragraph/section embeddings, and returns `SearchHit` rows with `source = "semantic"` and `level = paragraph | section`. A cartridge with missing or mismatched embedding metadata disables the Semantic button in the UI and returns `UnsupportedEmbeddingModel` from the backend if called directly.

## Near-term improvements (v1.x)

Concrete data-structure-grounded improvements that fit within the v1 prototype's architecture and could land without bumping to v2. Listed in approximate UX-payoff order. Surfaced 2026-05-22.

- **Search autocomplete via a trie.** `SearchPanel` is currently type-then-submit. A trie built once at `open_cartridge` time from the FTS5 vocabulary (or from `doc_nodes` titles + extraction terms) enables live prefix completion as the user types — "vir" → virtue / virtuous / virtues. The same trie powers a Cmd+K command palette for jumping straight to sections by title, which is especially useful given the Meditations title-truncation case. Build cost is one full-table scan at open; memory is bounded by the cartridge's term count.
- **Node and extraction LRU caches.** `DocTree` re-queries on every expand; `ExtractionsPanel` re-fetches on tab switch. A standard hashmap-plus-doubly-linked-list LRU keyed by `node_ulid` (and another by `extraction_ulid`) makes re-navigation instant. Lands in `src/api.ts` as a memoization layer between Zustand and `invoke()`. Bounded memory; no eviction issues for Meditations-scale cartridges. Pairs naturally with a `Map<parent_ulid, DocNode[]>` already implicit in `DocumentView`'s group-by pass.
- **Navigation history (back / forward).** No browser-style nav today. A deque of `selectedNode` history with a cursor (or two stacks — "back" stack and "forward" stack, with selection clearing forward) gives back/forward. Header has room per the three-panel layout.
- **Explicit set types for UI state.** `DocTree` expanded-nodes, `ExtractionsPanel` filter pills (multi-select on `anchor_status` + `extraction_method`), and history visited-nodes are all sets, not arrays. Standard React `Set<T>` state pattern; mostly cleanup with a small clarity payoff in the codebase.

## Open hooks for next slices

These are explicitly out of scope for v0.3.4, but the current design should not foreclose them:

- **Hybrid search.** Keyword and semantic search are both implemented, but result merging is not. `SearchHit.source` already distinguishes `"fts"` from `"semantic"`, so a future hybrid mode can merge the two ranked streams without reshaping result rows.
- **Ambassador-upgrade writes (engine-side).** Reader does not write. Once the engine's transactional upgrade flow ships, the existing Annotations and TrustVector surfaces light up automatically — no reader mutation path required.
- **Cross-cartridge promotion (`nexus_refs`).** Active table in v0.3 but currently empty. Reader continues to ignore it; a dedicated surface lands when cross-cartridge events start populating the table.
- **Multi-cartridge shelf persistence.** The SPEC-007 `SketchedShelf` consumer, verify-by-opening pass, and click-through are implemented. Per-cartridge tabs and persistent shelf state across sessions are still v2.
- **Title-debug overlay.** A power-user toggle that surfaces the title parser-artifact blocklist's decision tree, useful for the Meditations title-truncation case. Could land in v1 if cheap.
- **Figure-vision embeddings.** v0.3.4 text semantic search only scans `embeddings`; image/vision vectors belong to the separate SPEC-014 `image_embeddings` work and are not consumed by Reader semantic mode.
- **Top-K result merging via min-heap (hybrid-search consumer).** When a hybrid-search mode fires, merging FTS5 and semantic ranked streams into a top-K is the textbook merge-K-sorted-streams problem; a min-heap is the standard tool. Implementation detail when hybrid search ships; no v0.3.4 code change.
- **Related-claims clustering via disjoint-set.** Claims that share at least one source node are structurally connected (the claim ↔ source bipartite graph has natural connected components). Computing connected components with union-find at cartridge-open lets the reader highlight "related claims" instantly when one is selected. Specialist feature; only worth building if claim-clustering becomes a UX surface. Also becomes load-bearing for the cross-cartridge `nexus_refs` surface, where union-find answers "are these two local nodes the same nexus concept across cartridges?"
- **Provenance graph view.** The `ProvenanceDrawer` shows claim -> sources structurally as a list. A force-directed or SVG node-edge render of the full provenance subgraph (claim + sources + context nodes, with annotation edges added once SPEC-005 lands) is a natural extension. Pure UI surface; no backend change beyond the existing `get_extraction_sources`.

## Validation against the format spec

The reader is a v0.3-contract consumer. Every read path it relies on should appear in `03_Format_Spec/LUN-FORMAT_v0.3.md`. If the reader needs to read something the format spec doesn't document, that's a finding for a format-spec update, not a reader workaround.

Findings from earlier v0.2 builds, all RESOLVED in `LUN-FORMAT_v0.2.md` and carried forward into v0.3:

- **`doc_nodes.meta_json` shape — RESOLVED.** See `LUN-FORMAT_v0.3.md` § "`doc_nodes` — document tree" → `meta_json` per-source shapes table.
- **`extraction_sources.extraction_ulid` / `node_ulid` — RESOLVED.** The v0.2 shadow columns are now the v0.3 primary keys; see `LUN-FORMAT_v0.3.md` § "`extraction_sources`".
- **`nexus_refs` table — RESOLVED.** Active in v0.3 (no longer a placeholder); empty in the Meditations reference cartridge. Reader continues to ignore it pending cross-cartridge usage.

No new format-spec findings surfaced during the v0.3 reader build — the schema delta was already documented in `LUN-FORMAT_v0.3.md` before this slice started.

### Findings produced during v1 build (2026-05-22)

- **`doc_nodes.meta_json` on PDF sources — RESOLVED.** PDF parser writes `{"page_num": int}` on `section`, `paragraph`, and `sentence` rows; the root `document` row carries `{"title": str}` instead. PDF sections may also carry an optional `"title"` key alongside `page_num` (observed: `{"page_num": 19, "title": "R"}`). Resolution: `LUN-FORMAT_v0.2.md` § "`doc_nodes` — document tree" now includes a per-source `meta_json` shapes table covering PDF, Markdown, and the all-sources root case.
- **`claim_sources.claim_ulid` / `node_ulid` — RESOLVED.** Both shadow columns are present in the canonical reference cartridge and nullable in v0.2. SPEC-002 documents the migration plan (NOT NULL + composite PK in v0.3). Resolution: `LUN-FORMAT_v0.2.md` § "`claim_sources` — claim-to-source anchoring" DDL block now lists `claim_ulid TEXT` and `node_ulid TEXT`; the Shadow ULID columns paragraph cites SPEC-002 Phase 2.
- **`nexus_refs` table — RESOLVED.** Canonical schema verified against builder source (`10_Builder/src/luna/cartridge/schema.py:125`):
  ```sql
  CREATE TABLE nexus_refs (
      local_node_id TEXT NOT NULL,
      nexus_node_id TEXT NOT NULL,
      node_type TEXT NOT NULL,
      promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (local_node_id, node_type)
  );
  CREATE INDEX idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
  ```
  Note: earlier draft of this bullet reported `local_node_id INTEGER` and `promoted_at INTEGER` without the index — that was wrong. The TEXT/TIMESTAMP types reflect cross-schema compatibility with the aibrarian `.db` family (which uses TEXT UUIDs as local identifiers). Zero rows in the v0.2 reference cartridge (SPEC-005 placeholder). Resolution: `LUN-FORMAT_v0.2.md` § "`nexus_refs` — cross-cartridge promotion placeholder (SPEC-005)".
- **Section nesting — RESOLVED.** Acceptance criterion §3 correctly reads "128 direct section children and 166 sections total" (post-M-01; was 176). The hierarchy claim in the format spec's `doc_nodes` section originally read `document → section → paragraph → sentence` which suggested strict 4-level depth. Resolution: `LUN-FORMAT_v0.2.md` § "`doc_nodes` — document tree" now states "Sections may nest under other sections; the diagram describes node-type ordering, not strict depth."
- **`doc_nodes.content` is nullable — RESOLVED.** Canonical reference cartridge has 439 of 3803 rows (~11.5%) with NULL content — predominantly `document` and `section` rows, plus paragraph rows whose text lives in `sentence` children. Reader reads `content` as `Option<String>` and defaults to empty string. Resolution: `LUN-FORMAT_v0.2.md` § "`doc_nodes` — document tree" -> Content nullability paragraph; also the documented FTS triggers were corrected to use `COALESCE(content, '')` to match the canonical builder schema.
- **anchor_status distribution (post-M-01 reference cartridge).** Claims: 1056 anchored / 148 match_failed / 0 unknown. Entities: 1418 all `unknown` (per SPEC-001). Summaries: 145 anchored. `extraction_context_nodes` is empty (no synthesized claims). Reader UI handles all five anchor_status variants but only three are exercised by this cartridge. (Cartridge-specific record; not a format-spec finding. Historical v0.2 audit baseline was 458/54/532/62.)
- **Visual verify 18–19 (2026-07-23).** Logic covered green by Rust shelf tests (`verify_fts_term_confirmed` / `verify_fts_term_false_positive`, `verify_extraction_ulid_*`, `verify_node_ulid_*`, `verify_entity_surface_*`) and `search_finds_virtue_and_surfaces_ulid` — 58/58 `cargo test --lib` passed. Full Tauri UI click-through (Shelf badge chrome + tab navigation) remains a recommended human smoke when a display is available; no regression signal from the automated verify-by-opening roundtrip.
- **Figure fixture shape (2026-07-26).** `The-Nature-Of-Art-And-Creativity.lun` is the v0.3.4 figure smoke fixture: 26 `figure` nodes, 26 child `image` nodes, 26 `media_classification`, 26 `visual_description`, and 26 `figure_discourse` extraction rows. Reader figure display/inspector acceptance should be checked against this cartridge rather than Meditations, which has no figure/image nodes.
- **Semantic embedding fixture shape (2026-07-26).** Both reference cartridges advertise `embedding_model = all-MiniLM-L6-v2` and `embedding_dim = 384`. Meditations carries 459 text embeddings; Nature-of-Art carries 72. Semantic search is therefore expected to be enabled for both.

### Implementation notes

- **Stack delta.** Used `npm` instead of `pnpm` (pnpm was not installed locally). No functional difference; switching is mechanical (`package-lock.json` ↔ `pnpm-lock.yaml`).
- **`user_version` strictness — CANONICAL.** Strict `user_version = 3` acceptance is the canonical v0.3 reader contract. v0.1 and v0.2 cartridges receive `UnsupportedVersion(n)` with a migrate-first hint; everything else rejects as `UnsupportedVersion`. The Open contract section above (§103-110) and the rejection table both reflect this.
- **Read-only enforcement.** Both `SQLITE_OPEN_READ_ONLY` (OS-level fd) and `PRAGMA query_only = 1` (SQL parser) are applied, per the plan's defense-in-depth rationale.
- **FTS5 `<mark>` safety.** Backend `safe_snippet()` state machine escapes HTML metacharacters in source content while preserving the literal `<mark>` / `</mark>` tokens we passed to `snippet()`. Unit-tested with adversarial inputs including `<script>` and uppercase `<MARK>` (which must be escaped, not preserved). Frontend uses `dangerouslySetInnerHTML` on the resulting string.
- **FTS5 Strategy A coupling.** v0.3 keeps `doc_nodes.id INTEGER` as FTS5's `content_rowid`. The reader never exposes that integer to the UI — `queries::search` joins `nodes_fts.rowid → doc_nodes.id` and returns `n.ulid` instead. If a future version moves to Strategy B (drop `doc_nodes.id`, rebuild FTS5), only the search query body needs to change — types and UI are already ULID-keyed.
- **Offline semantic model bundling.** `embedder.rs` uses `include_bytes!` for `model.onnx` and tokenizer/config files under `src-tauri/models/all-MiniLM-L6-v2/`; fastembed links ONNX Runtime through `ort-download-binaries`. That makes semantic search self-contained at runtime, but it also means release binaries are expected to be much larger than the pre-semantic-search 11 MB app.
- **Soft-covenant honesty.** Step 7 of the open contract verifies that the two append-only triggers exist on `annotation_ledger`. This is a soft covenant per [SPEC-005](../../01_Specs/implemented/SPEC-005_annotation-ledger.md): an admin with `sqlite3` CLI access can drop the triggers and tamper with the ledger. The reader cannot prevent that; it can only refuse to open a cartridge whose triggers are visibly missing. Full chain re-verification (`validate_ledger()`) is the engine's `lun fsck --ledger` job; the reader stops at the head-pointer check to keep open latency O(1).
- **Hybrid-search hooks now partially real.** `SearchHit.source` is `"fts"` for Keyword results and `"semantic"` for Semantic results; `level` is set for semantic paragraph/section hits. `get_latest_event_ts` remains exposed for SPEC-004 Temporal-axis work.

## Cross-references

- [`03_Format_Spec/LUN-FORMAT_v0.3.md`](../../03_Format_Spec/LUN-FORMAT_v0.3.md) — open contract, schema, anchor display invariants, ledger DDL
- [`01_Specs/implemented/SPEC-001_orphan-claims.md`](../../01_Specs/implemented/SPEC-001_orphan-claims.md) — anchor_status taxonomy
- [`01_Specs/implemented/SPEC-002_portable-ids.md`](../../01_Specs/implemented/SPEC-002_portable-ids.md) — ULID format and D5 integer-rowid removal
- [`01_Specs/implemented/SPEC-003_meaningful-confidence.md`](../../01_Specs/implemented/SPEC-003_meaningful-confidence.md) — raw signals
- [`01_Specs/implemented/SPEC-005_annotation-ledger.md`](../../01_Specs/implemented/SPEC-005_annotation-ledger.md) — ledger table, hash chain, soft-covenant triggers
- [`01_Specs/implemented/SPEC-005_payload-schemas.md`](../../01_Specs/implemented/SPEC-005_payload-schemas.md) — per-event-type payload contracts
- [`01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md`](../../01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md) — TrustVector composer contract (this reader implements the canonical reference composer)
- [`01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md`](../../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md) — application_id, user_version, finalize stack
- [`07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`](../../07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun) — primary test cartridge
- [`04_Audits/AUDIT_2026-05-22_meditations-v03.md`](../../04_Audits/AUDIT_2026-05-22_meditations-v03.md) — v0.3 shipping-gate audit; reader v0.3 named as the gating follow-up
- Engine repo `frontend/package.json` — Eclissi's Vite/React/Tailwind/Zustand stack to mirror
