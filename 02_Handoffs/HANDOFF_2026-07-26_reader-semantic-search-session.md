# HANDOFF: Reader semantic search (Floor 1 of the post-figures roadmap)

**Date:** 2026-07-26
**Status:** Implemented, tested, committed (`5018a80`)
**Continue in:** Claude Code

## What landed this session

- Agreed order for the 4 deferred SPEC-013 items (from `HANDOFF_2026-07-26_reader-figures-session.md`): **(1) Reader semantic search UI → (2) scanned PDF page-as-image → (3) vision/OCR enrichment → (4) assembler/RRF consumption**, with SPEC-012 Engine WP0 (LUNM entity unification) as an orthogonal parallel track. Rationale: semantic search picked first because it's pure Reader-side, no Engine dependency, and embeddings already exist in cartridges.
- **Reader semantic search UI (`06_Prototypes/ReaderPrototype/`)** — Keyword/Semantic toggle in `SearchPanel`:
  - `src-tauri/src/embedder.rs` (new) — bundled fp32 ONNX `all-MiniLM-L6-v2` (sourced from `Xenova/all-MiniLM-L6-v2`), loaded via `fastembed`'s `try_new_from_user_defined` + `Pooling::Mean`, lazy `OnceLock<Mutex<TextEmbedding>>`. Model files (`src-tauri/models/all-MiniLM-L6-v2/`, ~87MB) vendored via `include_bytes!` — fully offline, no runtime download.
  - **Key discovery:** `fastembed`'s default `ort-download-binaries` feature statically links ONNX Runtime into the compiled binary (confirmed via `otool -L` — zero dylib dependency). No separate dylib bundling/bootstrap needed; this simplified the original plan considerably.
  - `queries::semantic_search` — brute-force cosine scan over `embeddings` (paragraph + section merged), snippet text reconstructed from descendant sentence/list_item/cell nodes (mirrors the builder's `embedder.py` text-assembly, since paragraph/section `doc_nodes.content` is NULL by schema design).
  - New Tauri command `semantic_search`, `SearchHit.level` field, `ReaderError::UnsupportedEmbeddingModel`/`EmbeddingError` variants.
  - Frontend: `semanticSearch.ts` (model/dim guard), toggle UI in `SearchPanel.tsx` (Semantic disabled + tooltip if cartridge's embedding model/dim don't match), level badge on results.
- **Verification:**
  - 64 Rust tests green, incl. a parity test: embedding a known Meditations paragraph through the bundled Rust/ONNX pipeline vs. the paragraph's stored (Python-built) vector → cosine similarity **1.0000001** — confirms the same vector space, not an approximation.
  - Manual smoke test against the real Nature-of-Art cartridge with a deliberately paraphrased query (near-zero keyword overlap) returned genuinely on-topic hits.
  - `npm run build` (tsc + vite) clean.
  - Manually click-tested via `npm run tauri dev` by the user — confirmed working.
- Scoped to **macOS arm64 only** this session (dev machine constraint) — code has no platform-specific branches, so this should carry to other platforms without changes, but hasn't been built/tested there.
- Committed: `5018a80` — `feat(reader): semantic search over stored MiniLM embeddings`.

## Mental model (unchanged)

| Layer | Role |
|---|---|
| Open-Looney (this repo) | Format law / specs / wiki / Reader prototype |
| LunaEngineBetaV2.0 (`_LunaEngine_BetaProject_V2.0_Root`) | Living builder — **very active**: 1188 commits on `main`, 510 in the last 30 days, 167 branches (117 `feat/`) as of this session. Far larger and faster-moving than this spec/reader repo. |
| `10_Builder/` | Stale snapshot — **not** authority |

## Still deferred (next implementation picks, in agreed order)

1. **Scanned PDF page-as-image typing** (full-page rasters as `figure`/`image`) — Engine slice, next up.
2. Assembler/RRF consumption of `figure_discourse` neighbors.
3. Vision embeddings / richer `visual_description`; regions; style tags; GDAL / COG RFC.
4. SPEC-012 Engine WP0+ (LUNM entity unification) — parallel track, not blocking the above.

Also noted but not scheduled: consider git-lfs for `src-tauri/models/` (~87MB) — committed as a plain tracked file this session per explicit user choice; revisit if repo size becomes a problem.

## Suggested next session opener

> Pick up scanned PDF page-as-image typing (Engine slice) — full-page rasters currently aren't typed as `figure`/`image` nodes. This is Engine-side work in `_LunaEngine_BetaProject_V2.0_Root`, not this repo's Reader.

## Verify Reader after pull

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/Apps/lun Development/06_Prototypes/ReaderPrototype"
cargo test --release --manifest-path src-tauri/Cargo.toml   # 64 tests, incl. embedding parity
npm run build                                                # tsc + vite
npm run tauri dev                                            # click-test Keyword/Semantic toggle
```
