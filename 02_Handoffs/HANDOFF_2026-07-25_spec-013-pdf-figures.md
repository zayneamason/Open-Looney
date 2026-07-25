# HANDOFF: SPEC-013 PDF figures

**Date:** 2026-07-25  
**Engine PR:** [#165](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/165) merge `fa78da70`

## What landed

PDF text pages extract embedded XObjects into `figure` → `image` + `media_blobs`
(PNG bytes). FTS spine uses `Image (page N)`. Same SPEC-013 spine as Markdown.

## Still deferred

- OCR of PDF figure rasters (richer FTS than page label)
- Bare JPEG/PNG as builder input (no wrapping `.md`)
- Enrichment (taxonomy / visual_description / discourse)
- Regions, GDAL, COG
