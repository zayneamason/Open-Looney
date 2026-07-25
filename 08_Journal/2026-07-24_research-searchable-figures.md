# RESEARCH — Searchable Figures & Modality-General Node Pattern for Open-Looney / .lun

**Status:** research brief + approved design (not shipping)  
**Created:** 2026-07-24  
**Owner:** Ahab (Zayne)  
**Parent:** Open-Looney (LUNC cartridge family) + Luna Engine assembly/retrieval  
**SPEC draft:** [`01_Specs/active/SPEC-013_searchable-figures.md`](../01_Specs/active/SPEC-013_searchable-figures.md)  
**Design:** [`2026-07-24_design-intimate-searchable-figures.md`](2026-07-24_design-intimate-searchable-figures.md)  
**Trigger question:** Documents contain pictures; those need to be searchable *and intimately understood*. GIS/GeoPackage / open-source GIS are parallels for SQLite-as-container and layered rasters — transferable pattern is structured, addressable units + derived meaning + indexes, not “become GIS.”  
**Primary mode:** research → design → fold SPEC-013 draft. No Engine ship in this pass.  
**Next session goal:** User review of design + SPEC-013 fold; only then optional `writing-plans` / spike.

**Resume prompt (paste next session):**

> Continue RESEARCH — Intimate Searchable Figures.  
> Read `08_Journal/2026-07-24_research-searchable-figures.md`,  
> `08_Journal/2026-07-24_design-intimate-searchable-figures.md`, and SPEC-013.  
> Revise if needed; do not implement Engine/builder unless explicitly requested.  
> Do not implement COG/GeoPackage tiles unless we explicitly open a media-family RFC.

---

## 1. One-line thesis

Images (and other non-text payloads) inside documents should enter the **same cartridge law as text**: addressable nodes → derived artifacts (OCR/captions/claims) → FTS + embeddings → provenance — so retrieval hits *meaning*, not raw pixels.

---

## 2. Problem statement

### 2.1 User-facing failure

- PDFs/Markdown/books often include figures, diagrams, photos, charts.
- If `.lun` only indexes surrounding prose, queries like “the diagram of the Ambassador protocol” or “photo of the Lansing building” miss or hallucinate.
- Dumping full JPEG/PNG into the prompt is expensive, non-portable, and doesn’t compose with FTS/RRF.

### 2.2 Format-facing failure

- Opaque `BLOB` / external file with no node identity breaks the Open-Looney principles:
  - content-addressed over location-addressed
  - separate data from interpretation
  - file is source of truth / auditable with generic SQLite tools
- Chat vision path today (Engine `images.db` + `media/` files) is correct for **inbound chat images**, but is **not** the document-cartridge story.

### 2.3 What this is *not*

- Not “LunaOS must ship GeoTIFF.”
- Not “every cartridge embeds COG tile pyramids.”
- Not stuffing multi‑MB rasters into `memory_matrix.lun` (Wall / soul DB stays lean).

---

## 3. Prior conversation verdicts (carry forward)

| Claim | Verdict |
|---|---|
| GeoPackage proves SQLite can hold mixed media + metadata | **Keep** as rhetoric + existence proof |
| Flat JPEG is a bad knowledge object for AI search | **Keep** |
| Transfer text pattern (nodes → extractions → indexes) to figures | **Core thesis — pursue** |
| Full COG/MrSID in every `.lun` | **Defer** — optimization for huge rasters / map SKU |
| Chat images vs document figures | **Split** — sidecars for chat; cartridge nodes for documents |
| GIS CRS / lat-long | **Only if document is geographic** |

Origin discussion: `_HeyLuna_BETA/Research/Looney_GeminiConversation_001.md` (GIS/image riff) + Cursor architecture session 2026-07-24.

---

## 4. Pattern to generalize (modality-agnostic cartridge law)

Five layers that already describe LUNC text; apply to any payload type:

1. **Atomic addressable units** — ULID nodes in a tree (or typed graph edges later)
2. **Raw payload** — text string, image bytes ref, table rows, audio span, …
3. **Derived artifacts** — extractions, OCR lines, captions, classifications
4. **Indexes** — FTS5, vector embeddings (and later region indexes)
5. **Provenance** — `extraction_sources` / ledger / `anchor_status`

**Working name:** *Structured Payload Pattern* (SPP)  
**Instance for images:** *Searchable Figures*

---

## 5. Scope options (DECIDED 2026-07-24)

| Option | Description | Verdict |
|---|---|---|
| **A. Minimal searchable figures** | figure/image + caption/OCR → FTS | **Absorbed** into Approach 2 as the linguistic spine tier |
| **B. Region-addressable figures** | A + `region` bbox nodes | **Reserved** compatible extension under `image` |
| **C. Media-family cartridge** | GeoPackage-inspired tiles / maps SKU | **Parked** — separate RFC |
| **D. Vision embeddings only** | Embed full image, no OCR | **Rejected** as sole strategy |
| **Approach 2 — Intimate-figure law** | Taxonomy + locus + linguistic + visual appearance + discourse; hybrid storage/placement; tiered enrichment; GDAL optional | **Accepted research design** |

See design doc for full locked decision table.

---

## 6. Proposed data model (draft — Option A → B)

### 6.1 Node types (additive)

- `figure` — logical figure in a document (may wrap one or more bitmaps)
- `image` — single raster asset (optional if `figure` holds payload)
- `region` — (Option B) axis-aligned bbox in image coordinates / normalized [0,1]

### 6.2 Payload storage (decide explicitly)

| Approach | When |
|---|---|
| **External file + hash in `meta_json` / side table** | Large images; matches Engine `ImageStore` philosophy |
| **BLOB in cartridge table** | Small figures; true single-file portability |
| **Both** | `storage=external|embedded` discriminator |

**Open question:** Single-file portability vs cartridge size — ceremonial books may want embedded; research corpora may want external.

### 6.3 Derived text (search spine)

- `caption` — human or LLM caption (extraction type or child node)
- `ocr_text` — full-page or per-region OCR
- Optional: `alt_text`, `figure_label` (“Figure 3”)

All of these must be **FTS-indexed** (via `doc_nodes.content` and/or dedicated FTS).

### 6.4 Embeddings

- Embed **caption + OCR** with existing MiniLM path (reuse text embeddings table)
- Optional later: vision encoder embedding on image/region (new `level` or modality flag)
- Do **not** block searchable figures on vision embeddings

### 6.5 Provenance

- Extractions (claims about what the figure shows) anchor to `figure` / `region` ULIDs via `extraction_sources`
- Ledger events for human-corrected OCR / ambassador upgrades (SPEC-005 pattern)

### 6.6 Sketch schema fragments (non-normative)

```sql
-- Additive node types only; ulid spine unchanged
-- doc_nodes.type CHECK expands: ..., 'figure', 'image', 'region'

-- Optional side table if BLOB preferred over meta_json path:
CREATE TABLE IF NOT EXISTS media_blobs (
  node_ulid TEXT PRIMARY KEY,
  media_type TEXT NOT NULL,  -- image/png, image/jpeg, ...
  width INTEGER,
  height INTEGER,
  sha256 TEXT NOT NULL,
  bytes BLOB,                -- NULL if external
  external_path TEXT,        -- NULL if embedded
  FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;

-- region geometry (Option B)
-- meta_json on region node:
-- {"bbox":[x0,y0,x1,y1], "coord_space":"normalized"|"px", "parent_image_ulid":"..."}
```

---

## 7. Builder pipeline changes (conceptual)

```
Parse document
  → text nodes (existing)
  → detect figures/images
  → create figure/image nodes
  → (optional) run OCR → ocr child nodes / content
  → (optional) caption LLM pass → extraction or caption node
  → embed caption+OCR
  → FTS sync
  → validate anchors
```

**PDF:** need figure extraction (pymupdf / pdfium / etc.) — research spike  
**Markdown:** `![](path)` → image node + read file  
**HTML:** `<img>`, `<figure>`

---

## 8. Retrieval / Luna Engine implications

- Hybrid search already FTS + semantic on node text — **OCR/caption in `content` piggybacks** with little engine change
- Prompt assembly: Thin Live Assembler / honesty — cite figure nodes like text; don’t inject raw base64 unless vision turn requires it
- Wall: figure *content* stays in LUNC satellite; master holds Nexus pointers / annotations only (existing Wall rules)
- Do not conflate with `images.db` chat path — different product surface

---

## 9. Related art to study

| System | Why |
|---|---|
| **GeoPackage** | SQLite + rasters/vectors; standards process |
| **Open-source GIS survey** | [GIS Geography tag](https://gisgeography.com/tag/open-source-gis-software/) + [13 free GIS apps](https://gisgeography.com/free-gis-software/) (QGIS, GRASS, Whitebox, SAGA, gvSIG, …) — layered analysis, drivers, research platforms; not a Luna shopping list |
| **GDAL** | Optional builder; Dataset/bands/window/overview/VRT metaphors |
| **PDF tagged figures / PDF/UA** | Figure structure in source docs |
| **IIIF** | Image API, regions, tiles for cultural heritage |
| **Mukurtu / cultural archive patterns** | Sovereignty + media metadata (TEAM_LUNA FNIGC adjacency) |
| **LlamaIndex / Unstructured image elements** | Industry chunking of figures + image description enrichment |
| **Open-Looney SPEC-001–007** | Anchor, ULID, ledger, sketches — extension surface |
| **Engine `substrate/images.py`** | What *not* to merge into matrix |

---

## 10. Open questions

**Closed (see design):** hybrid bytes; tiered layers (not all-eager); regions reserved not required; SPEC-013 confirmed; intimacy target C; figure wraps image; GDAL optional; Approach 2.

**Still open (acceptance / later plan):**

1. Exact embed-vs-external size/kind thresholds
2. Local OCR stack for offline/sovereign builds
3. Exact extraction type strings + discourse-link representation
4. Sketches (SPEC-007) + figure/OCR terms
5. Reader UX: cite vs optional bbox highlight
6. Ceremonial sensitivity / classification metadata detail
7. Multi-page scanned PDFs typing
8. Vision embedding flag vs separate table
9. Final `media_kind` enum refinement

---

## 11. Success criteria (for an eventual SPEC)

- [ ] Query by keyword found only in figure OCR/caption returns the figure node
- [ ] Semantic query over caption returns figure without loading full bitmap into prompt
- [ ] Claim about figure content anchors to figure ULID with `anchor_status`
- [ ] Old v0.3 readers ignore unknown types safely (additive evolution)
- [ ] Audit with stock `sqlite3` can list figures and OCR text
- [ ] Chat `images.db` path unchanged / documented as non-goal for this SPEC

---

## 12. Explicit non-goals (v1 of this research)

- Full GeoTIFF CRS / map projections
- In-cartridge COG tile pyramids (unless separate media-family RFC)
- Training a micro-JEPA on images
- Blockchain minting of media
- Replacing AiBrarian or Nexus

---

## 13. Suggested next-session agenda

1. User review of design + SPEC-013 fold (research-primary; no ship)
2. Resolve remaining open questions worth closing on paper
3. Only if requested: `writing-plans` or Markdown+PNG conceptual spike
4. Do **not** open media-family / COG RFC unless maps/artifacts are the workload

---

## 14. Pointers into existing trees

| Location | Relevance |
|---|---|
| This repo (`Open-Looney`) | Spec law, format versions, samples |
| `03_Format_Spec/LUN-FORMAT_v0.3.md` | Current node/extraction contracts |
| `01_Specs/implemented/SPEC-001` … `007` | Anchors, ULIDs, ledger, sketches |
| Luna Engine `src/luna/cartridge/` | Builder, schema, validation |
| Luna Engine `src/luna/substrate/images.py` | Chat image sidecar (do not confuse) |
| `TEAM_LUNA/01_WHAT_LUNA_IS.md` + Wall | Sovereignty / satellite content rules |
| `Research/Looney_GeminiConversation_001.md` | Origin of GIS parallel (filter hype) |

---

## 15. Changelog

| Date | Note |
|---|---|
| 2026-07-24 | Initial research brief from Cursor session (architecture discussion + Gemini GIS riff + clarification: searchable document images / transferable Structured Payload Pattern). Filed under Open-Looney journal; SPEC-013 stub opened. |
| 2026-07-24 | Brainstorming: intimacy target C; Approach 2 design approved; GIS Geography open-source survey cited; design doc written; SPEC-013 folded as research draft (not accepted, not shipping). |
