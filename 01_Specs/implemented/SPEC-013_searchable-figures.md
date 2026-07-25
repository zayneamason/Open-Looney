# SPEC-013: Searchable Figures (Structured Payload Pattern — Figure Spine)

**Status:** implemented  
**Severity:** medium  
**Author:** Ahab  
**Created:** 2026-07-24  
**Last updated:** 2026-07-25  
**Accepted:** 2026-07-25
**Implemented:** 2026-07-25  
**Affects format version:** LUNC additive (no `user_version` bump in this slice — `media_blobs` + `image` node role are additive; LUN-FORMAT amendment may pin v0.4 later)  
**Research brief:** [`08_Journal/2026-07-24_research-searchable-figures.md`](../../08_Journal/2026-07-24_research-searchable-figures.md)  
**Research design:** [`08_Journal/2026-07-24_design-intimate-searchable-figures.md`](../../08_Journal/2026-07-24_design-intimate-searchable-figures.md)  
**Spike evidence:** [`04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md`](../../04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md)  
**Engine spike branch:** `feat/searchable-figures-spike` (LunaEngineBetaV2.0; merge/PR separate)

**Acceptance scope:** figure **spine** only (nodes + bytes + FTS linguistic spine + honesty).  
Approach 2 intimacy (taxonomy / visual_description / discourse) remains the **north star** but is **enrichment**, not required for this acceptance. Regions, OCR, GDAL, COG, vision embeddings are deferred. Markdown / PDF XObjects / bare raster inputs land via Engine PRs #164–#166.

---

## Problem statement

Document cartridges often contain pictures. Opaque bytes without a search spine
fail Open-Looney’s structure + meaning + indexes law. Chat vision
(`images.db`) is a different product surface and must stay separate.

## Observed evidence

- LUN-FORMAT v0.3 lists `figure` in vocabulary; Markdown emitted flat `figure`+`src` without bytes or FTS-ready spine.
- 2026-07-24 Markdown+PNG spike (Engine `feat/searchable-figures-spike`, AUDIT): `figure`→`image`, embedded `media_blobs`+sha256, FTS hit on figure alt/caption; enrichment empty by design.
- GIS / GeoPackage / open-source GIS survey = related art for SQLite-as-container and layered rasters — not normative schema.

## Root cause analysis

Images were treated as optional binaries. Search indexes structure. Without
materialized linguistic text (and later enrichment), figures are invisible to
FTS/RRF and honest assembly.

## Proposed solution (normative — spine)

### 1. Node types

| Type | Role | `content` rule (v1) |
|---|---|---|
| `figure` | Logical figure in the document | **Holds the FTS-visible linguistic spine** (Markdown: alt text; later: caption and/or OCR rollup written here). Thin children for OCR lines are optional later; they MUST NOT leave `figure.content` empty when a linguistic spine exists. |
| `image` | One raster under a `figure` | Typically NULL; payload in `media_blobs` / machine meta |
| `region` | Reserved under `image` | Out of this acceptance |

Hierarchy: `… → figure → image (+)`.

### 2. Payload (`media_blobs`)

Additive side table keyed by `image` node ULID:

- `sha256` NOT NULL (64 hex chars)
- `storage` ∈ {`embedded`, `external`} with XOR discriminant (embedded ⇒ bytes; external ⇒ path)
- `media_type`, optional width/height

**v1 builder policy (accepted):** book/document figures **embed** bytes in-cartridge for portability (spike forced embedded). Schema retains `external` for a later size/kind policy; do not write absolute build-machine paths into shipped cartridges without an explicit later RFC.

**MIME allowlist (builder):** `image/png`, `image/jpeg`, `image/gif`, `image/webp`. Other types: skip insert + warn, do not fail the whole build.

**Path containment:** resolved image paths MUST stay under the source document directory (always `.resolve()` before containment check).

### 3. Linguistic spine → FTS

- Caption/alt (and later OCR) that exist MUST be FTS-visible via `figure.content` (existing `nodes_fts` triggers).
- Markdown spike: **alt/caption only** — no OCR requirement.
- MiniLM embeddings on that text are best-effort (v0.3 coverage policy); do not block the spine on embed success.

### 4. Tiered production (ship discipline)

| Tier | Required for accepted spine | May be empty |
|---|---|---|
| Build | `figure`+`image`, locus, sha256, storage | — |
| Cheap linguistic | caption/alt when available → FTS | OCR until enrichment pass |
| Enrichment | — | `media_kind`/tags, `visual_description`, discourse |
| Later | — | `region`, vision embeddings, GDAL overviews |

Readers MUST tolerate missing enrichment. Missing enrichment = incomplete intimacy, **not** license to hallucinate pixels at load time.

### 5. Provenance

Extractions MAY anchor to figure/image ULIDs via SPEC-001. Optional for spine acceptance; required when claims about figures are emitted.

### 6. Explicit non-goals (this acceptance)

- Chat `images.db` / `substrate/images.py` changes
- COG / MrSID / in-SQLite tile pyramids
- GeoTIFF CRS for non-map docs
- GDAL required to read PNG/JPEG
- ~~PDF image XObject extraction~~ — landed PR #165 (`fa78da70`); OCR of those images still deferred
- Regions / bbox
- Taxonomy / visual_description / discourse as required fields
- Thumbnail/overview as a validity requirement (optional, non-validating if present)

### 7. Assembler honesty

Cite text layers (label, caption/alt, later enrichment). Inject bitmap only on an explicit vision turn.

## Schema changes

Additive:

```sql
CREATE TABLE IF NOT EXISTS media_blobs (
    node_ulid TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL,
    storage TEXT NOT NULL CHECK (storage IN ('embedded', 'external')),
    bytes BLOB,
    external_path TEXT,
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid),
    CHECK (
        (storage = 'embedded' AND bytes IS NOT NULL AND external_path IS NULL)
        OR (storage = 'external' AND bytes IS NULL AND external_path IS NOT NULL)
    )
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_media_blobs_sha256 ON media_blobs(sha256);
```

No LUNC `user_version` bump in this slice. Format doc amendment may later name v0.4.

## Migration path

Additive only. Existing cartridges remain valid. Markdown: flat `figure`+`src` → `figure` + child `image` + `media_blobs` when file resolves.

## Validation rules

- `media_blobs` row ⇒ `doc_nodes.type = 'image'`, valid storage discriminant, sha256 64 hex.
- Linguistic spine present ⇒ FTS-visible on `figure.content`.
- Missing enrichment MUST NOT invalidate a well-formed figure/image.
- Table absent ⇒ validators no-op (forward-compatible readers).

## Enrichment (deferred — same tree, not blocking)

When implemented later (not part of this acceptance gate):

- Closed `media_kind`: `photo` | `diagram` | `chart` | `map` | `painting` | `schematic` | `screenshot` | `other` (+ open style tags) — **kinds frozen**; Engine writes `extractions.type=media_classification` with `content=<kind>` (`extraction_method=rule`, PR #168). Style tags still deferred.
- Extractions: `media_classification` (**frozen**), `visual_description` (**frozen** as caption/OCR rollup stub, Engine PR [#169](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/169)); discourse as a typed extraction shape TBD
- Optional local OCR stack — **done** (PR #167)
- Optional GDAL for TIFF/GeoTIFF/large plates
- Exact discourse extraction type strings still TBD

## Governance implications

Figures may carry higher sensitivity. Classification/consent metadata and ledger corrections (SPEC-005) apply when enrichment/human OCR edits land.

## Resolved questions (acceptance)

| ID | Resolution |
|---|---|
| R1 FTS placement | `figure.content` is the v1 FTS carrier; not dual undefined rollup |
| R2 Storage default | Embed for v1 book figures; `external` reserved; no abs build paths in shipped cartridges |
| R3 Markdown linguistic | Alt/caption only; OCR not required for spine |
| R4 Thumbnail | Optional, non-validating |
| R5 Discourse | Deferred to enrichment |
| R6 Intimacy target C | North star; enrichment may be empty; spine acceptance does not require all five layers |

## Still deferred (follow-on)

1. External size/kind thresholds when `external` is re-enabled  
2. Local OCR stack — **done** Engine PR [#167](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/167) merge `840db392`: optional `--figure-ocr` / `figure_ocr=True` rolls pytesseract text into `figure.content` (needs `.[ocr]` + tesseract; non-fatal when absent)  
3. Extraction type strings + discourse representation — **partial:** `media_classification` + `visual_description` frozen; discourse still open  
4. SPEC-007 sketches + figure terms  
5. Reader bbox UX  
6. Scanned PDF page-as-image typing  
7. Vision embedding flag  
8. `media_kind` enrichment pass + validators — **done** Engine PR [#168](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/168) merge `a67e7ee9`: rule-based `media_classification` extraction; closed kinds `photo|diagram|chart|map|painting|schematic|screenshot|other`  
9. PDF image extraction slice — **done** Engine PR [#165](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/165) merge `fa78da70`  
10. Engine PR merge of `feat/searchable-figures-spike` → `implemented/` promotion  
11. Bare PNG/JPEG (etc.) as builder input — **done** Engine PR [#166](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/166) merge `b3022894`  
12. `visual_description` stub — **done** Engine PR [#169](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/169) merge `5d39c96e`: rule rollup of `figure.content` → anchored extraction

## Decision log

| Date | Decision |
|---|---|
| 2026-07-24 | Research filed; SPEC stub opened as **active**. |
| 2026-07-24 | Design Approach 2 approved; folded into draft. |
| 2026-07-24 | Markdown+PNG spike closed (AUDIT). Acceptance withheld pending paper freeze. |
| 2026-07-25 | **Accepted** for figure **spine** only. Enrichment/regions/PDF/COG remain deferred. R1–R6 resolved. |
| 2026-07-25 | Engine PR #164 merged (`01d2fc65`); SPEC promoted **accepted → implemented**. |
| 2026-07-25 | Engine PR #165 merged (`fa78da70`) — PDF XObjects → figure/image spine. |
| 2026-07-25 | Bare-image builder input landed (PR #166, `b3022894`): filename stem → `figure.content` FTS. |
| 2026-07-25 | Optional figure OCR rollup landed (PR #167, `840db392`): append to `figure.content`; default off. |
| 2026-07-25 | Froze extraction type `media_classification` and closed `media_kind` set; Engine PR #168 merged (`a67e7ee9`) (rule heuristics, always-on). |
| 2026-07-25 | Froze extraction type `visual_description` as figure.content rollup stub; Engine PR #169 merged (`5d39c96e`). |

## Implementation notes

- Engine PR [#164](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/164) merged 2026-07-25 as `01d2fc65` (Markdown spine).
- Engine PR [#165](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/165) merged 2026-07-25 as `fa78da70` (PDF embedded images).
- Engine PR [#166](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/166) merged 2026-07-25 as `b3022894` (bare PNG/JPEG/GIF/WebP → figure spine).
- Engine PR [#167](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/167) merged 2026-07-25 as `840db392` (optional figure OCR → `figure.content` FTS).
- Engine PR [#168](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/168) merged 2026-07-25 as `a67e7ee9` (rule-based `media_classification` / `media_kind`).
- Engine PR [#169](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/169) merged 2026-07-25 as `5d39c96e` (`visual_description` stub from `figure.content`).
- Promoted `accepted → implemented` after #164; #165–#169 extend the same SPEC spine.

## References

- `08_Journal/2026-07-24_research-searchable-figures.md`
- `08_Journal/2026-07-24_design-intimate-searchable-figures.md`
- `04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md`
- `06_Prototypes/PLAN_2026-07-24_intimate-searchable-figures.md`
- `03_Format_Spec/LUN-FORMAT_v0.3.md`
- SPEC-001, SPEC-005, SPEC-007
- GIS Geography open-source GIS survey — related art only
