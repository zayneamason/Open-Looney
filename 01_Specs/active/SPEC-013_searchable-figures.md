# SPEC-013: Searchable Figures (Structured Payload Pattern — Images)

**Status:** active (research-backed draft; not ready for acceptance)  
**Severity:** medium  
**Author:** Ahab  
**Created:** 2026-07-24  
**Last updated:** 2026-07-24  
**Affects format version:** TBD (candidate additive slice toward v0.4)  
**Research brief:** [`08_Journal/2026-07-24_research-searchable-figures.md`](../../08_Journal/2026-07-24_research-searchable-figures.md)

---

## Problem statement

Document cartridges often contain pictures (figures, diagrams, photos, charts).
Today those payloads are either omitted from the node tree or stored as opaque
bytes without a first-class search spine. Retrieval and honest citation then
depend on surrounding prose or raw vision dumps into the prompt — both fail
Open-Looney’s “structure + meaning + indexes” law for text nodes.

## Observed evidence

- User intent (2026-07-24): fold searchable images into `.lun` because documents
  contain pictures that must be findable; GIS/GeoPackage cited as transferable
  *container* pattern, not as the required image schema.
- Engine already separates **chat** vision (`substrate/images.py` → `images.db` +
  `media/` files) from **document** cartridges — correct split, but LUNC lacks
  the figure-node story.
- LUNC text path already proves the pattern: `doc_nodes` → extractions →
  embeddings → FTS5 → provenance (SPEC-001–007, LUN-FORMAT v0.3).

## Root cause analysis

Text was modeled as addressable units with derived meaning; images were treated
as optional binary attachments. Search systems index what is structured. Unstructured
rasters are invisible to FTS/RRF unless OCR/captions (or vision embeddings) are
materialized as first-class derived artifacts.

## Proposed solution

Apply the **Structured Payload Pattern** to figures (research §4):

1. Additive `doc_nodes` types: at least `figure` / `image` (Option A); `region` later (Option B).
2. Persist bytes as embedded BLOB and/or external file + sha256 (decision open).
3. Require a **search spine**: caption and/or OCR text that is FTS-indexed and embeddable via existing MiniLM path.
4. Allow extractions to anchor to figure/region ULIDs (SPEC-001 machinery).
5. Keep chat `images.db` out of scope.

**First slice recommendation:** Option A (minimal searchable figures).  
**Deferred:** COG/MrSID tile pyramids, GeoTIFF CRS, separate media-family cartridge (Option C) as its own RFC.

## Schema changes

TBD after Option A vs B decision — see research §6. Prefer additive types and
optional `media_blobs` side table; do not break v0.3 readers (ignore unknown types).

## Migration path

Additive only. Existing cartridges remain valid. Builder flag to extract figures
on rebuild (`--figures` / default TBD).

## Validation rules

TBD. Candidates:

- Figure/image nodes with embedded/external payload must have `sha256`.
- If `ocr_text` / caption present, they must appear in FTS-visible content.
- `region` nodes (if any) must reference a parent image ULID and valid bbox.

## Governance implications

Images often carry higher sensitivity than prose (likeness, ceremonial content).
Classification / consent metadata may need explicit guidance for figure nodes
(align with future role-based access specs). Ledger (SPEC-005) should record
human OCR corrections.

## Open questions

See research brief §10 (BLOB vs external, OCR mandatory vs lazy, local OCR stack,
regions timing, sketches, Reader UX, scanned PDFs, vision embeddings).

## Decision log

| Date | Decision |
|---|---|
| 2026-07-24 | Research filed; SPEC stub opened as **active**. Acceptance blocked on Option A vs B + schema draft completeness. |

## References

- `08_Journal/2026-07-24_research-searchable-figures.md`
- `03_Format_Spec/LUN-FORMAT_v0.3.md`
- SPEC-001 (anchors), SPEC-005 (ledger), SPEC-007 (sketches)
- GeoPackage / IIIF as related art (research §9) — not normative requirements
