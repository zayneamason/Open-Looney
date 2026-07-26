# HANDOFF: Scanned PDF page-as-image typing (SPEC-013 item 6)

**Date:** 2026-07-26
**Status:** Implemented, tested, merged — Engine PR [#172](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/172) / `465b784c`
**Continue in:** Claude Code
**Repo:** `_LunaEngine_BetaProject_V2.0_Root` (Engine slice — not this repo)

## What landed

`_emit_page_images` — the only producer of `figure`/`image` nodes and
`media_blobs` rows — was reachable only from `_parse_text_page`. A scanned page
(under `_MIN_TEXT_CHARS = 50` of extractable text) rendered a 300-dpi pixmap
purely to feed OCR and then discarded it. Scanned cartridges carried **zero
rasters**; without tesseract they held nothing but N copies of
`"[Scanned page — OCR not available]"`.

Scanned pages now emit `figure` → `image` + a `media_blobs` row at
`PAGE_IMAGE_DPI` (150 — storage only; OCR still reads at 300, so text quality is
untouched). Both nodes carry `meta.page_image`. Default ON, with
`--no-page-images` and `--page-image-dpi=N`.

Files (all under `src/luna/cartridge/`):

| File | Change |
|---|---|
| `media.py` | `PAGE_IMAGE_DPI`, `PAGE_IMAGE_META_KEY`, `is_page_image(dict)` / `is_page_image_meta(json)` |
| `parsers/pdf.py` | `_emit_page_raster`, `PDFParser(page_images=, page_image_dpi=)`, named `_OCR_DPI = 300` |
| `builder.py` | knobs threaded to `PDFParser`, page images excluded from OCR collection, CLI flags |
| `media_classify.py`, `visual_description.py`, `figure_discourse.py` | skip page-image figures |
| `tests/test_cartridge_pdf_page_images.py` | new, 10 tests |

## The four traps (why this was mostly carve-out work)

A page-sized figure walks into enrichments designed for small captioned pictures.

| Trap | Without a carve-out |
|---|---|
| `media_classification` | regex-scans figure content — a page mentioning "the map of Europe" classifies as `map` |
| `visual_description` | stubs a "description" from a page label nobody derived from pixels |
| `figure_discourse` | a page figure's sibling paragraphs *are* its own OCR text → `before: <this page>` |
| `--figure-ocr` | re-OCRs the page, appending a duplicate full page into `figure.content` → doubled in FTS, inflated `word_count`, feeds traps 1–2 |

Consequence: `figure.content` is a **bare label** (`"Page 3 (scanned page)"`).
The page's words live once, in `sentence` nodes — the FTS *and* embedding
carriers.

`media_kind` vocabulary is **unchanged**: page figures get no classification row
rather than a forced `other`. Verified there is no "every figure is classified"
invariant — `validation.py` only checks content against the closed set when a row
is present.

## Verification

- **356 passed** across the cartridge test surface, 0 failures. 10 new tests.
- **Smoke** on an 8-page rasterized PDF (image-only, no text layer): 8
  figures/images; 6 embedded + 2 external with sidecars confirmed on disk; **0
  enrichment extractions**; every figure at position 0; labels clean after a real
  OCR pass — tesseract is installed on the dev machine, so the `--figure-ocr`
  test was decisive rather than vacuous.
- **Off-path:** `--no-page-images` differs by exactly 16 nodes (8 figures + 8
  images), sentences identical at 115, no `.media/` created. Structural equality,
  not byte equality — ULIDs carry a timestamp plus 80 random bits.

Reproduce the smoke input from any multi-page PDF:

```python
import fitz
src, out = fitz.open("in.pdf"), fitz.open()
for p in src:
    pix = p.get_pixmap(dpi=200)
    page = out.new_page(width=p.rect.width, height=p.rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
out.save("/tmp/scanned.pdf")
```

## Gotchas for whoever picks this up

- **Sidecar count is `count(DISTINCT sha256)`, not page count.**
  `materialize_external` is content-addressed and skips a write when the file
  exists, so identical pages (blank versos) share one file while each keeps its
  own `media_blobs` row. Asserting 1:1 page↔file fails on any real book.
- `media_blobs.bytes` is NULL by schema CHECK whenever `storage='external'` —
  size a raster from the parser node's `meta["image_bytes"]`, not the column.
- `validate_media_blobs` validates the row discriminant but **never stats the
  sidecar file**. The new `embed_max=0` test does.
- Existing scanned cartridges are **rebuild, not migrate** — `migrate.py` cannot
  invent pixels it never stored and has no access to the source PDF.

## Named follow-ups (not patched in-flight)

1. `validate_media_blobs` doesn't check sidecar existence (`validation.py:573-577`
   self-documents that `lun fsck` isn't wired). Pre-existing, but sidecar counts
   now scale with page count.
2. Two pixmap renders per scanned page (150 storage, 300 OCR); could `shrink(1)`
   the OCR pixmap instead. Tesseract dominates build time, so low priority.
3. Text-branch full-bleed detection (`get_image_rects` coverage ratio →
   informational `page_coverage`). Deliberately **out**: a captioned full-page
   plate on a text page is a real figure whose enrichments should keep running,
   so it must not share the `page_image` suppression flag.
4. `_MIN_TEXT_CHARS = 50` is unnormalized by page area — a scan carrying a
   60-char header takes the text path, and its raster is emitted as an ordinary
   figure with enrichments unsuppressed.
5. `tests/test_cartridge_pdf_parser.py:14` imports `fitz` at module scope, so it
   hard-fails instead of skipping without pymupdf.
6. JPEG and/or grayscale encoding for footprint, if PNG @ 150 dpi proves heavy on
   real scanned books.

## Still deferred (agreed order)

1. **Assembler/RRF consumption of `figure_discourse` neighbors** — next up.
2. Vision embeddings / richer `visual_description`; regions; style tags; GDAL /
   COG RFC.
3. SPEC-012 Engine WP0+ (LUNM entity unification) — parallel track, not blocking.

## Suggested next session opener

> Pick up assembler/RRF consumption of `figure_discourse` neighbors. Note that
> page-image figures deliberately carry no `figure_discourse` rows, so the
> assembler must not assume every figure has neighbors.
