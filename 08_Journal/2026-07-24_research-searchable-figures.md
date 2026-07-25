# RESEARCH — Searchable Figures & Modality-General Node Pattern for Open-Looney / .lun

**Status:** draft research brief (session handoff)  
**Created:** 2026-07-24  
**Owner:** Ahab (Zayne)  
**Parent:** Open-Looney (LUNC cartridge family) + Luna Engine assembly/retrieval  
**SPEC stub:** [`01_Specs/active/SPEC-013_searchable-figures.md`](../01_Specs/active/SPEC-013_searchable-figures.md)  
**Trigger question:** Documents contain pictures; those need to be searchable. GIS/GeoPackage is a parallel for SQLite-as-container — the transferable pattern is structured, addressable units + derived meaning + indexes, not “become GIS.”  
**Next session goal:** Turn this into an accepted SPEC (or reject with reasons) and a thin prototype plan.

**Resume prompt (paste next session):**

> Continue RESEARCH — Searchable Figures & Modality-General Node Pattern.  
> Read `08_Journal/2026-07-24_research-searchable-figures.md` and `01_Specs/active/SPEC-013_searchable-figures.md`.  
> Decide Option A vs B; harden SPEC-013; optionally spike Markdown+PNG → searchable figure node.  
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

## 5. Scope options (decide next session)

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A. Minimal searchable figures** | `doc_nodes.type` ∈ {figure, image}; caption + OCR text as child nodes or fields; FTS on OCR/caption; bytes as file path or BLOB | Fits v0.3 evolution; shippable | No region-level search yet |
| **B. Region-addressable figures** | A + `region` nodes with bbox; OCR/claims anchored to regions | True “search inside image” | Harder builder (layout/OCR pipeline) |
| **C. Media-family cartridge** | Separate `application_id` / kind for maps & large rasters (GeoPackage-inspired tiles) | Right tool for huge assets | New family; don’t block A/B |
| **D. Vision embeddings on whole image only** | Embed full image, no OCR | Easy | Weak keyword search; opaque |

**Recommendation to debate:** Accept **A** as first SPEC slice; design **B** as compatible extension; park **C** as future family RFC; reject **D** as sole strategy.

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

## 9. Related art to study (next session reading list)

| System | Why |
|---|---|
| **GeoPackage** | SQLite + rasters/vectors; standards process |
| **PDF tagged figures / PDF/UA** | Figure structure in source docs |
| **IIIF** | Image API, regions, tiles for cultural heritage |
| **Mukurtu / cultural archive patterns** | Sovereignty + media metadata (TEAM_LUNA FNIGC adjacency) |
| **LlamaIndex / Unstructured image elements** | Industry chunking of figures |
| **Open-Looney SPEC-001–007** | Anchor, ULID, ledger, sketches — extension surface |
| **Engine `substrate/images.py`** | What *not* to merge into matrix |

---

## 10. Open questions (answer in next session)

1. Embedded BLOB vs external file vs hybrid — default for v0.4?
2. Is OCR mandatory at build time, or lazy/on-demand?
3. Which OCR stack (local) for offline/sovereign builds?
4. Region model now or after captions work?
5. Confirm SPEC number **SPEC-013** (stub already opened)?
6. How do sketches (SPEC-007) include figure/OCR terms?
7. Reader prototype: render figure + show search hit highlight on bbox?
8. Copyright / ceremonial cartridges: images often higher sensitivity — classification metadata?
9. Multi-page scanned PDFs: is each page an `image` under a `section`, or a different type?
10. Does vision embedding belong in LUNC core or optional builder flag (`--embed-vision`)?

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

## 13. Suggested next-session agenda (90–120 min)

1. Re-read this brief + `03_Format_Spec/LUN-FORMAT_v0.3.md` node/extraction sections
2. Decide Option **A vs B** for first SPEC slice
3. Harden `01_Specs/active/SPEC-013_searchable-figures.md` from template completeness
4. Spike: one Markdown doc with one PNG → builder creates figure node + caption/OCR → FTS hit
5. Write AUDIT on a sample cartridge after spike
6. Update Open-Looney open concerns when SPEC moves accepted → implemented

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
