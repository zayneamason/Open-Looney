# SPEC-013: Searchable Figures (Structured Payload Pattern — Intimate Images)

**Status:** active (research-backed draft; **not ready for acceptance**)  
**Severity:** medium  
**Author:** Ahab  
**Created:** 2026-07-24  
**Last updated:** 2026-07-24  
**Affects format version:** TBD (candidate additive slice toward v0.4)  
**Research brief:** [`08_Journal/2026-07-24_research-searchable-figures.md`](../../08_Journal/2026-07-24_research-searchable-figures.md)  
**Research design:** [`08_Journal/2026-07-24_design-intimate-searchable-figures.md`](../../08_Journal/2026-07-24_design-intimate-searchable-figures.md)

**Primary mode:** research → design → this SPEC draft. No Engine implementation is authorized by this document alone.

---

## Problem statement

Document cartridges often contain pictures (figures, diagrams, photos, charts).
Today those payloads are either omitted from the node tree or stored as opaque
bytes without a first-class meaning spine. Retrieval and honest citation then
depend on surrounding prose or raw vision dumps into the prompt — both fail
Open-Looney’s “structure + meaning + indexes” law for text nodes.

The research goal is **intimate knowledge**: when a cartridge loads, materialized
layers should say what the picture looks like, what kind it is, where it sits,
how it relates to surrounding text, and what linguistic content is searchable —
without requiring COG tile pyramids or turning every `.lun` into a mini GeoPackage.

## Observed evidence

- User intent (2026-07-24): fold searchable/understandable images into `.lun`
  because documents contain pictures; GIS/GeoPackage and open-source GIS surveyed
  as transferable *container and layer* patterns, not as the required image schema.
  Related art index: [GIS Geography — Open Source GIS Software](https://gisgeography.com/tag/open-source-gis-software/)
  and [13 Free GIS Software Options](https://gisgeography.com/free-gis-software/).
- Engine already separates **chat** vision (`substrate/images.py` → `images.db` +
  `media/` files) from **document** cartridges — correct split, but LUNC lacks
  the intimate figure-node story.
- LUNC text path already proves the pattern: `doc_nodes` → extractions →
  embeddings → FTS5 → provenance (SPEC-001–007, LUN-FORMAT v0.3).
- LUN-FORMAT v0.3 already lists `figure` in observed vocabulary; Markdown builder
  emits `figure` with alt + `src` but no bytes spine, OCR, taxonomy, or discourse.
- PDF parser skips image blocks today; OCR exists only as scanned-page fallback.

## Root cause analysis

Text was modeled as addressable units with derived meaning; images were treated
as optional binary attachments. Search and understanding systems index what is
structured. Unstructured rasters are invisible to FTS/RRF and to honest assembly
unless OCR/captions/taxonomy/appearance/discourse (or later vision embeddings)
are materialized as first-class derived artifacts.

## Proposed solution

Apply the **Structured Payload Pattern** as an **intimate-figure law**
(research design Approach 2):

1. Node types: `figure` (logical) wraps one or more `image` (raster) children;
   `region` reserved under `image` (compatible extension, not required for draft intimacy).
2. Persist bytes with hybrid `storage=embedded|external` + `sha256`
   (`media_blobs` or equivalent).
3. **Hybrid meaning placement:**
   - FTS-critical linguistic spine on/near the figure (`content` and/or thin
     caption/OCR/alt/label children);
   - taxonomy, visual appearance, discourse as **extractions**;
   - machine fields in `meta_json` / side table.
4. **Tiered production:** build guarantees locus + bytes + hash; cheap linguistic
   spine when available; visual description, closed `media_kind` + open tags, and
   discourse links as enrichment passes (may be empty; readers must tolerate).
5. Extractions anchor to figure/image/(region) ULIDs via SPEC-001 machinery;
   ledger records human corrections (SPEC-005 pattern).
6. GDAL/rasterio: **optional builder tool** for large/odd rasters; Dataset / bands /
   window / overview / VRT remain normative *metaphors*. GDAL is not required to
   read ordinary PNG/JPEG figure cartridges.
7. Keep chat `images.db` out of scope.
8. Defer COG/MrSID tile pyramids, GeoTIFF CRS (except geographic/map docs), and
   separate media-family cartridges to their own RFC.

**Closed `media_kind` draft set:**  
`photo` | `diagram` | `chart` | `map` | `painting` | `schematic` | `screenshot` | `other`  
plus optional open style/tags.

## Schema changes

Additive only (draft — refine before acceptance):

- Expand observed node types with normative roles for `figure`, `image`, and
  reserved `region` (v0.3 already mentions `figure` as vocabulary).
- Optional `media_blobs` side table (see research design §4.3).
- Extraction types for `media_classification`, `visual_description`, and discourse
  binding (exact type strings TBD in acceptance pass).
- Embedding coverage remains best-effort (v0.3 policy); linguistic spine preferred
  for MiniLM; vision embeddings optional later flag.

Do not break v0.3 readers (ignore unknown types / empty enrichment).

## Migration path

Additive only. Existing cartridges remain valid. Conceptual builder flags for
figure extract / enrichment passes (`--figures`, enrichment jobs) — TBD at
implementation planning; not authorized by this draft alone.

Markdown migration concept: today’s flat `figure`+`src` → `figure` + child `image`.

## Validation rules

Draft candidates:

- Figure/image nodes with payload must have `sha256` and a valid `storage`
  discriminant (embedded XOR external path).
- If linguistic spine (OCR/caption/alt/label) is present, it must be FTS-visible.
- If `media_classification` enrichment exists, `media_kind` ∈ closed set.
- `region` nodes (if any) must reference a parent image ULID and valid bbox.
- Missing enrichment MUST NOT invalidate an otherwise well-formed figure/image.

## Governance implications

Images often carry higher sensitivity than prose (likeness, ceremonial content).
Classification / consent metadata may need explicit guidance for figure/image
nodes (align with future role-based access specs). Ledger (SPEC-005) should record
human OCR / taxonomy / appearance corrections.

## Open questions

Still open for a later acceptance pass:

1. Exact embed-vs-external size/kind policy thresholds
2. Local OCR stack choice for offline/sovereign builds
3. Exact extraction type strings and discourse-link representation
4. Whether sketches (SPEC-007) must include figure/OCR terms
5. Reader UX for figure cite vs optional bbox highlight (regions)
6. Scanned multi-page PDFs: page-as-`image` under section vs other typing
7. Vision embedding modality flag vs separate table
8. Final `media_kind` enum refinement

**Closed in research design (2026-07-24):** intimacy target C; hybrid placement;
hybrid storage; tiered production; taxonomy C; GDAL optional builder; figure wraps
image; COG/media-family deferred; Approach 2 intimate-figure law.

## Decision log

| Date | Decision |
|---|---|
| 2026-07-24 | Research filed; SPEC stub opened as **active**. |
| 2026-07-24 | Research design approved (Approach 2 — intimate-figure law). Folded into this draft. Acceptance still blocked on open questions + review. Primary mode remains research; no ship authorization. |

## References

- `08_Journal/2026-07-24_research-searchable-figures.md`
- `08_Journal/2026-07-24_design-intimate-searchable-figures.md`
- `03_Format_Spec/LUN-FORMAT_v0.3.md`
- SPEC-001 (anchors), SPEC-005 (ledger), SPEC-007 (sketches)
- [GIS Geography — Open Source GIS Software tag](https://gisgeography.com/tag/open-source-gis-software/)
- [13 Free GIS Software Options](https://gisgeography.com/free-gis-software/)
- GeoPackage / IIIF / Unstructured image elements — related art, not normative
