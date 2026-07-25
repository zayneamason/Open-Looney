# DESIGN — Intimate Searchable Figures (Research)

**Status:** research design (approved in dialogue 2026-07-24); not an implementation plan  
**Primary mode:** research → design → fold into SPEC-013 (draft). No Engine ship in this pass.  
**Parent research:** [`2026-07-24_research-searchable-figures.md`](2026-07-24_research-searchable-figures.md)  
**SPEC fold target:** [`01_Specs/active/SPEC-013_searchable-figures.md`](../01_Specs/active/SPEC-013_searchable-figures.md)  
**Approach:** Intimate-figure law (Approach 2)

---

## 1. Research framing

Documents contain pictures. Those pictures must enter the same cartridge law as text: **addressable units → derived meaning → indexes → provenance**. The GIS / open-source geospatial world is **related art and vocabulary**, not the product requirement.

**Intimate knowledge** (design target) means that when a cartridge is loaded, the engine can know—from *materialized layers*, not implied vision—what a figure looks like, what kind it is, where it sits in the document, how it relates to surrounding text, and what searchable linguistic content it carries.

**Not shipping:** this document does not authorize builder/Engine work. Spikes and plans are optional follow-ons after SPEC review.

---

## 2. Decisions locked (dialogue)

| Topic | Decision |
|---|---|
| Scope posture | Research-primary; design then fold into SPEC-013 draft |
| Intimacy target | **C** — taxonomy + locus + linguistic spine + visual appearance + discourse binding; **regions (D)** reserved |
| Meaning placement | **Hybrid** — FTS-critical text on/near node; taxonomy/appearance/discourse as extractions; machine fields in `meta_json` / side table |
| Bytes | **Hybrid** `storage=embedded\|external` + sha256 |
| Layer production | **Tiered** — locus+bytes+hash at build; cheap linguistic spine when available; visual/taxonomy/discourse as enrichment |
| Taxonomy | Closed **`media_kind`** + optional open style/tags |
| GDAL role | **Optional builder tool**; Dataset/bands/window/overview/VRT as normative *patterns* |
| Node types | **`figure` wraps `image`(+)**; `region` reserved under `image` |
| COG / tile matrices | Out of SPEC-013 — media-family RFC later |
| Chat vision | `images.db` / `media/` unchanged; non-goal |

---

## 3. Architecture

```
document
  └─ section / paragraph …
       └─ figure                    ← logical unit (label, discourse, caption role)
            ├─ image                ← one raster (bytes + machine meta)
            │    └─ region*         ← reserved; bbox under image
            ├─ FTS-visible text     ← caption / OCR / alt / figure_label
            └─ extractions          ← media_kind/tags, visual_description,
                                       discourse links, claims about the figure
```

**Structured Payload Pattern (SPP) layers:**

1. Atomic addressable units — `figure` / `image` / (`region`)
2. Raw payload — hybrid embedded|external + sha256
3. Derived artifacts — linguistic, taxonomic, visual, discourse (tiered)
4. Indexes — FTS + MiniLM on linguistic spine; vision embed optional later
5. Provenance — `extraction_sources` / ledger for corrections

**Honesty:** the engine “knows” what is materialized. Missing enrichment = incomplete intimacy, not hallucinated pixels at load time.

---

## 4. Nodes, layers, placement

### 4.1 Node types

| Type | Role | Typical `content` |
|---|---|---|
| `figure` | Logical figure in the document | Search blend: caption and/or rollup of OCR/alt/label |
| `image` | One raster asset | Often NULL; machine fields elsewhere |
| `region` | Reserved under `image` | Optional per-bbox OCR snippet |

Note: LUN-FORMAT v0.3 already lists `figure` in observed vocabulary; Markdown already emits `figure` with alt + `src`. This design elevates that stub into an intimate spine (`figure` → child `image`).

### 4.2 Hybrid placement

| Data | Where |
|---|---|
| FTS-critical linguistic spine | `figure.content` and/or thin children (`caption`, `ocr_text`, `alt`, `figure_label`) |
| Taxonomy | Extraction (e.g. `media_classification`) → closed `media_kind` + open tags |
| Appearance | Extraction `visual_description`; optional later vision embedding on `image` |
| Discourse | Extractions / soft links to nearby paragraph/sentence ULIDs; “see Fig. N” claims → `figure` |
| Machine fields | `meta_json` and/or `media_blobs`: storage, sha256, media_type, dims, path, page/src |

### 4.3 Payload sketch (non-normative DDL)

```sql
CREATE TABLE IF NOT EXISTS media_blobs (
  node_ulid TEXT PRIMARY KEY,       -- image node
  media_type TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  sha256 TEXT NOT NULL,
  storage TEXT NOT NULL CHECK (storage IN ('embedded', 'external')),
  bytes BLOB,                       -- NULL if external
  external_path TEXT,               -- NULL if embedded
  FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;
```

Thumbnail / overview (GDAL-analogue) may be a small embedded preview — optional enrichment, not validity.

### 4.4 Tiered completeness

| Tier | Present when | May be empty |
|---|---|---|
| Build | `figure`+`image`, locus, sha256, storage | — |
| Cheap build | caption/alt/OCR when available → FTS | — |
| Enrichment | — | `media_kind`/tags, visual_description, discourse |
| Later | — | `region`, vision embeddings |

Readers MUST tolerate missing enrichment.

### 4.5 `media_kind` (closed, research draft set)

`photo` | `diagram` | `chart` | `map` | `painting` | `schematic` | `screenshot` | `other`

Open tags carry style nuance (`oil_painting`, `watercolor`, `blueprint`, …). Exact enum may refine before SPEC acceptance.

---

## 5. GDAL and open-source GIS related art

### 5.1 Pattern transfer (normative metaphors)

| Concept | `.lun` analogue | Required? |
|---|---|---|
| Dataset | `image` + payload handle | Yes |
| Metadata domains / bands | Parallel derived layers | Yes (as layers) |
| RasterIO window | Reserved `region` | Reserved |
| Overviews | Optional thumbnail | Optional |
| VRT | Bytes + derived tables without rewriting PNG | Yes (composition) |
| Drivers | Builder openers per source format | Conceptual |
| GDAL/rasterio library | Large/odd rasters, dims, overviews, windows | Optional builder |
| CRS / GeoTIFF | Only if geographic / `media_kind=map` | Non-default |
| COG / in-SQLite tiles | Media-family RFC | Non-goal here |

### 5.2 Open-source GIS survey (reference)

Primary index used in this research pass:

- Tag hub: [Open Source GIS Software — GIS Geography](https://gisgeography.com/tag/open-source-gis-software/)
- Survey article: [13 Free GIS Software Options](https://gisgeography.com/free-gis-software/)

**How to read that corpus for Luna (not a shopping list):**

| Ecosystem signal | Transfer to intimate figures | Do not transfer |
|---|---|---|
| QGIS / plugin drivers | Many formats behind one UX ≈ builder drivers behind one node law | Desktop GIS UX in Reader |
| GRASS (350+ raster/vector tools) | Analysis as *operations on layers*, separate from storage | Requiring GRASS at read time |
| Whitebox / SAGA (terrain, LiDAR, morphometry) | Specialized windowed / multi-band analysis for huge rasters | Making every book figure a DEM workflow |
| gvSIG / OrbisGIS (research platforms) | Research-friendly open containers and sharing | Geographic CRS by default |
| GeoPackage / SQLite-native GIS (ecosystem cousin) | Rhetoric: SQLite as rich mixed-media container | Tile matrix sets inside every LUNC book |

**GDAL** sits under much of this stack (QGIS and peers). For Open-Looney: optional builder dependency when TIFF/GeoTIFF/large plates need it; ordinary Markdown PNG/JPEG does not require GDAL.

---

## 6. Retrieval, assembly, honesty

- Search hits **figure** (and optionally `image`) ULIDs via FTS/semantic on materialized text — not raw pixels.
- Assembler default: label, caption, taxonomy, short visual_description, discourse neighbors; bitmap only for explicit vision turns.
- Prefer thumbnail if any visual tokens are used.
- Wall: figure content in LUNC satellite; no multi‑MB rasters in `memory_matrix.lun`.
- Governance: figures may need classification/consent metadata; ledger records human OCR/taxonomy corrections (SPEC-005 pattern).

---

## 7. Success criteria (research / eventual SPEC)

- [ ] Keyword only in OCR/caption → figure ULID
- [ ] Semantic hit on caption/visual_description → figure without full bitmap
- [ ] `media_kind` filter works when enrichment present
- [ ] Claims anchor to figure ULID with `anchor_status`
- [ ] Discourse links figure ↔ surrounding text when enrichment present
- [ ] v0.3 readers ignore unknown types / empty enrichment
- [ ] Stock `sqlite3` lists figures, sha256, linguistic spine
- [ ] Chat `images.db` unchanged

### Validation candidates

- Payload ⇒ sha256 + valid `storage` discriminant (embedded XOR external)
- Linguistic spine present ⇒ FTS-visible
- `media_kind` ∈ closed set when classification extraction exists
- `region` (if any) ⇒ parent image ULID + valid bbox

---

## 8. Non-goals

- COG/MrSID in-cartridge tile pyramids
- GeoTIFF CRS for non-map documents
- GDAL required to read ordinary PNG/JPEG figures
- Vision foundation training / micro-JEPA
- Replacing AiBrarian, Nexus, or chat vision sidecars
- Engine/builder implementation in this research pass
- Media-family cartridge product (maps, museum plates) — separate RFC

---

## 9. Fold path

1. This design stays the research-approved model.
2. Fold normative intent into SPEC-013 (**active draft**, not accepted).
3. Update research brief decision log + open questions that are now closed.
4. Implementation plan / Markdown+PNG spike only if explicitly requested later (`writing-plans` / separate session).

---

## 10. Changelog

| Date | Note |
|---|---|
| 2026-07-24 | Design written from brainstorming dialogue (Approach 2). GIS Geography open-source survey cited as related art. Research-primary; SPEC fold next. |
