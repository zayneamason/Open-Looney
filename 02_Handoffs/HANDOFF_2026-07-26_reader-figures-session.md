# HANDOFF: Reader figure display + session close (SPEC-013 consumer)

**Date:** 2026-07-26  
**Status:** Reader v0.3.4 installed locally; Open-Looney commit pending with this handoff  
**Continue in:** Claude Code (this session closed in Cursor)

## What landed this session

### Engine
- PR [#171](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/171) **merged** `03fa73645e99ce26f6da14d8a158966f2cc69b83` — external `media_blobs` sidecars (`{stem}.media/`, `--embed-max`, `--force-embed-media`).
- Live Engine code: `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root` (not `10_Builder/`). Checkout/pull `main` before next Engine work.

### Reader (`06_Prototypes/ReaderPrototype/`) — **v0.3.4**
- `get_figure_payload` — `media_blobs` + enrichments; embedded → base64; external → resolved path + `convertFileSrc` / asset protocol
- `NodeType::Image` / `"image"` so SPEC-013 cartridges load
- Document figures + `FigureInspectorDrawer`
- Extractions tabs for `media_classification` / `visual_description` / `figure_discourse`
- Hierarchy / search / provenance → Document view **scroll-to-node** (`lun-node-*` anchors)
- Installed: `/Applications/lun-reader.app` (rebuild after source changes)

### Sample cartridge (local)
- `07_Sample_Cartridges/The-Nature-Of-Art-And-Creativity.lun` (~952 KB) — **commit this**
- Sidecars: `The-Nature-Of-Art-And-Creativity.media/` (~114 MB, **gitignored**) — must sit next to the `.lun`
- Source PDF: `09_Sample_Sources/The-Nature-Of-Art-And-Creativity.pdf`
- Full rebuild done with extract+embed+`--figure-ocr`: ~201 claims / 240 entities / 29 summaries / 26 figures / 72 embeddings

## Mental model (for next agent)

| Layer | Role |
|---|---|
| Open-Looney | Format law / specs / wiki / Reader prototype |
| LunaEngineBetaV2.0 | Living builder |
| `10_Builder/` | Stale snapshot — **not** authority |

## Still deferred (next implementation picks)

1. **Scanned PDF page-as-image** typing (full-page rasters as `figure`/`image`)
2. **Assembler/RRF** consumption of `figure_discourse` neighbors
3. Vision embeddings / richer `visual_description`; regions; style tags; GDAL / COG RFC
4. Reader: semantic search UI (embeddings exist); bbox UX; don’t ship nested `src-tauri/src-tauri` target dirs
5. SPEC-012 Engine WP0+ if LUNM entity unification is the parallel track

## Suggested next session opener

> Pick one: (A) scanned PDF page-as-figure Engine slice, or (B) assembler discourse consumption, or (C) SPEC-012 Engine WP0. Reader figure UX is done for this arc; reopen Nature-of-Art `.lun` with `.media/` beside it after any Engine media-policy change.

## Verify Reader after pull

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/Apps/lun Development/06_Prototypes/ReaderPrototype"
npm run tauri dev
# or rebuild: npm run tauri build && cp -R …/bundle/macos/lun-reader.app /Applications/
```
