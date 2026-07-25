# HANDOFF: SPEC-013 enrichment arc (spine follow-ons)

**Date:** 2026-07-25  
**Status:** Engine PRs #164–#170 merged; wiki pass P5 (`v0.6.0`)

## What landed (Engine)

| PR | Merge | Slice |
|---|---|---|
| [#164](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/164) | `01d2fc65` | Markdown figure spine + `media_blobs` |
| [#165](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/165) | `fa78da70` | PDF XObjects → figure/image |
| [#166](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/166) | `b3022894` | Bare PNG/JPEG/GIF/WebP → figure spine |
| [#167](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/167) | `840db392` | Optional `--figure-ocr` → `figure.content` |
| [#168](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/168) | `a67e7ee9` | Rule `media_classification` / closed `media_kind` |
| [#169](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/169) | `5d39c96e` | `visual_description` stub from `figure.content` |
| [#170](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/170) | `fb8d81cc` | `figure_discourse` + `extraction_context_nodes` |

## Open-Looney

- SPEC: `01_Specs/implemented/SPEC-013_searchable-figures.md`
- Wiki: `WIKI_HOME` index includes SPEC-013; changelog `[v0.6.0]`; pass P5
- TODO ledger: SPEC-013 section checked through enrichment trio
- Also cleaned: removed stale `01_Specs/accepted/SPEC-012_…` (implemented is sole home)

## Still deferred

- Scanned PDF page-as-image typing (full-page rasters as figures)
- Vision embeddings / richer visual_description (beyond caption rollup)
- Style tags on `media_kind`; regions; GDAL; COG / media-family RFC
- Reader bbox UX; SPEC-007 figure terms; `external` storage thresholds
- Assembler/RRF consumption of `figure_discourse` context nodes

## Next session

1. Scanned PDF page-as-figure **or** assembler discourse consumption — pick one.
2. Optional: finish any leftover SPEC-012 Engine WP if still open (separate from figures).
