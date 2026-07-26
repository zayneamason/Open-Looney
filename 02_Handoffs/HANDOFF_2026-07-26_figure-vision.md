# HANDOFF: Real vision visual_description (SPEC-013)

**Date:** 2026-07-26
**Status:** Implemented, tested, merged — Engine PR [#174](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/174) / `c70d8937`
**Continue in:** Claude Code
**Repo:** `_LunaEngine_BetaProject_V2.0_Root` (Engine slice — not this repo)

## What landed

`visual_description` was a rollup of `figure.content` — the caption restated. It
told a reader nothing the caption had not, which is why the retrieval path
suppresses it as redundant. **Nothing in the cartridge pipeline had ever looked
at a pixel.**

`--figure-vision` sends each figure to the Claude vision API and stores the
result as the extraction content, falling back to the rollup stub when vision is
unavailable or fails. New module `src/luna/cartridge/figure_vision.py`; the
extraction type, anchoring, and validation are unchanged and still frozen.

Opt-in, mirroring `--figure-ocr`: one API call per figure, so a 300-figure art
book would otherwise bill 300 calls on every rebuild.

## What it buys — and what it does not

**Read this before planning anything downstream.** An earlier draft of the plan
claimed the description would "ride the existing MiniLM embeddings and
`nodes_fts`". That was wrong. `visual_description` is an **extraction**, not a
`doc_node`:

- the embedder covers `paragraph` and `section` nodes only
- `nodes_fts` triggers fire on `doc_nodes`
- measured on the Nature-of-Art cartridge: `embeddings` holds 42 paragraph + 30
  section rows, **zero** anchored to any figure, **zero** for any
  `visual_description` ULID

The real route is: description → TEMP extraction FTS → BM25 → promotion to a
figure result (PR #173). So the gain is **lexical** — a figure becomes findable
by words describing its *content* rather than only its *caption*. It is **not**
semantic or cross-modal figure search. That still needs vision embeddings and
SPEC-014, and this slice does not substitute for it.

Proof the chain works, querying words present only in a generated description:

```
QUERY 'dollar bills currency'
  [figure] 'Plate 4 [shows: This artwork is composed of a grid of U.S. one-dollar
            bills arranged as a background, with a map of the continental United
            States overlaid in the center...'
```

That query returned nothing before — the only text about that figure was
`"Plate 4"`.

## Gotchas that cost real time

- **`get_provider("claude")` returns `None` in a CLI build.** The LLM registry is
  only populated inside a running Engine. Extraction survives this because
  `HaikuSubtaskBackend` silently falls back to the direct Anthropic SDK — so any
  new build-time LLM call needs the same registry → SDK fallback, or it will
  never fire for exactly the users whose extraction works. Cost one full
  debugging pass here.
- **Anthropic's image ceiling is on the base64 payload, not the raw bytes.**
  base64 inflates 4/3, so the raw ceiling is `5 MB × 3/4 = 3.75 MB`. A first cut
  used 4 MB raw, which would have passed images the API then rejects.
- **Filter page images before the call, not at write time.** Filtering after
  produces identical rows while still spending one call per scanned page — the
  entire cost of the feature on a scanned book. The test therefore asserts on the
  stub's call log, not on absent rows.
- **A non-empty system message is mandatory.** `_convert_messages` otherwise
  synthesizes an empty system block and Anthropic rejects with a 400. Documented
  in `api/server.py:_caption_images` and carried here.
- Responses can open with a markdown `# Image Description` heading despite the
  prompt saying no preamble; overlong output must be cut at a sentence boundary,
  not mid-clause. Both observed live, both now stripped and tested.

## Pillow and the `vision` extra

Oversize rasters are **downscaled**, not skipped. Pillow does the shrinking and
stays optional behind a new `vision` extra — deliberately separate from `ocr` so
nobody installs tesseract and pymupdf just to describe pictures. Without Pillow
those figures are skipped as before, with a warning naming the fix.

| Nature-of-Art cartridge | before | after |
|---|---|---|
| figures describable | 12 / 26 | **26 / 26** |

14 of 26 rasters exceed the ceiling, so treating downscaling as optional polish
would have left most of a real art book undescribed. Live: a 12.0 MB plate
downscales to 0.3 MB and describes correctly.

## Verification

- 84 targeted tests, 467 in the wider cartridge/aibrarian sweep. One failure,
  `test_streaming_seam_parity`, verified pre-existing on unmodified `src`.
- The page-image cost guard and the description cleanup are mutation-checked.
- Tests never touch the network — `describe_figures` takes an injectable
  `describe`.
- A downscale fixture initially took 58s building 12M pixels one at a time;
  rewritten with `os.urandom` + `frombytes` → 2.08s.

## Named follow-ups

1. **`media_kind` is blind to the description.** A dollar-bill/map collage
   classifies as `other` because `enrich_figures_with_media_kind` runs *before*
   `visual_description` and only ever sees the caption. Reordering would likely
   fix it, but changes an enrichment-ordering contract and affects the OCR
   interaction — worth doing deliberately, not as a drive-by.
2. Anthropic image-block construction now exists twice — `_caption_images` in
   `api/server.py` and `figure_vision`. A shared `luna/llm/vision.py` is the right
   factoring but would refactor the live vision turn path.
3. `_v03_vec_search` truncates via `zip(a, b)` with no `level` filter — latent
   today, a silent-corruption landmine the moment any second vector dimension
   exists. **Prerequisite for SPEC-014.**
4. No provenance marker distinguishes a vision-derived `visual_description` from
   a stub one — `extraction_method` is `rule` in both cases. Readers cannot tell
   which they are holding.

## Still deferred (the bundled ledger line is now split)

1. **SPEC-014 — vision embeddings.** Format-blocked: `LUN-FORMAT v0.3:317` is a
   MUST against a single cartridge-wide `embedding_dim`. Fork to resolve —
   separate table vs new meta keys plus relaxing that MUST vs v0.4. Vision-only
   search was already *rejected* as a sole strategy; do not re-propose it.
2. **Regions** — needs both a producer (PDF bboxes are computed then discarded)
   and a consumer.
3. **GDAL / COG media-family RFC — parked, trigger not met.** The survey already
   exists; the research brief says not to open it "unless maps/artifacts are the
   workload".

## Suggested next session opener

> Pick up SPEC-014 (vision embeddings) as paper first — the format fork genuinely
> blocks code. Fix the `_v03_vec_search` `zip()` truncation as a prerequisite,
> since it silently corrupts the moment a second vector dimension exists.
