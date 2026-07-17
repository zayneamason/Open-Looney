# AUDIT: PRIESTS_AND_PROGRAMMERS_Lansing.lun

**Audit date:** 2026-04-21
**Auditor:** Ahab (via SQLite Fiddle WASM)
**Cartridge built:** 2026-04-10T09:08:07.328648+00:00
**Format version:** v0.1 (schema_version=1)
**Tool:** sqlite3 CLI (WASM) at https://sqlite.org/fiddle/
**Source file:** PRIESTS AND PROGRAMMERS_Lansing.pdf

---

## Purpose

First audit of a production `.lun` cartridge using generic SQLite tooling
(no Luna code). Validates the portability thesis of the format and
surfaces schema/data issues before they become entrenched.

## Method

1. Loaded cartridge into SQLite Fiddle via "Load DB..." button
2. Ran schema inspection queries (`SELECT sql FROM sqlite_master`)
3. Ran integrity queries against meta, doc_nodes, extractions, claim_sources
4. Sampled orphan claims to classify failure modes

## Meta contents

```
title           | /. Stephen Lansing
source_path     | /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/PRIESTS AND PROGRAMMERS_Lansing.pdf
source_format   | pdf
source_hash     | bf3538aff1356fe5eb1ff50e302a327a256242163012f8f22d1dbfd125f1ba64
created_at      | 2026-04-10T09:08:07.328648+00:00
schema_version  | 1
word_count      | 80794
node_count      | 4124
embedding_model | all-MiniLM-L6-v2
embedding_dim   | 384
```

### Meta findings

- **FINDING M-01 (medium):** `title = "/. Stephen Lansing"` is a parser
  artifact. Real title is *Priests and Programmers: Technologies of Power
  in the Engineered Landscape of Bali*. The PDF parser appears to have
  treated a bullet/dot glyph as text and then concatenated the author name.
  No validation catches this at build time.

- **FINDING M-02 (low):** `source_path` records an absolute local path from
  the build machine. This leaks builder environment into the cartridge.
  Probably want just the filename (`PRIESTS AND PROGRAMMERS_Lansing.pdf`)
  in v0.2, with full path optionally separate.

- **POSITIVE:** All required meta keys present. `source_hash` is SHA-256
  format as expected. `embedding_model` and `embedding_dim` are both set,
  enabling reader validation.

## Document tree

```
type      | count
----------|------
document  |     1
section   |   237
paragraph |   314
sentence  | 3572
----------|------
TOTAL     | 4124
```

### Tree findings

- **POSITIVE:** Total (4124) matches `meta.node_count`. Builder is
  internally consistent on node counting.
- **OBSERVATION:** Sentence-to-paragraph ratio is 3572/314 = 11.4. High for
  prose but plausible for academic writing with heavy footnoting. Worth
  sanity-checking the sentence splitter on a few paragraphs to confirm it
  isn't over-splitting.
- **OBSERVATION:** Only 4 node types present. No subsection, no footnote,
  no figure/caption, no reference. The schema supports any `type` string
  but the builder has chosen a minimal vocabulary. Acceptable for v0.1 but
  worth documenting as a controlled vocabulary rather than open string.

## Extractions

```
type    | count | avg(confidence)
--------|-------|----------------
claim   |  1593 |            0.85
entity  |  2324 |            0.85
summary |   207 |            0.90
```

### Extraction findings

- **FINDING E-01 (high):** Confidence is effectively constant. Two
  different types (claim, entity) with identical 0.85 average across ~4000
  items is not a distribution — it's a hardcoded value. The column carries
  no information. Either compute real confidence (log probs, self-
  consistency) or remove the column. See forthcoming SPEC-003.

- **OBSERVATION:** 2324 entities vs 1593 claims — more entities than
  claims, which tracks for a book dense with place names, temple names,
  and people. Reasonable ratio.

- **OBSERVATION:** Only 207 summaries for 237 sections suggests the
  summary generator is running per-section but occasionally failing or
  skipping. Worth investigating the 30-section gap.

## Claim anchoring

```
Total claims (extractions.type='claim') : 1593
Total rows in claim_sources             : 1442
Distinct claim_id in claim_sources      : 1442
-> ORPHAN CLAIMS: 151 (9.5%)
-> Claims with multiple sources: 0 (confirms 1:1 in practice)
```

### Anchoring findings

- **FINDING C-01 (medium) — see SPEC-001:** 151 claims have no entry in
  `claim_sources`. Sample of 20 orphans reveals four distinct patterns
  (synthesis, frontmatter, paraphrase drift, nested attribution).

- **FINDING C-02 (medium):** `claim_sources` schema supports many-to-many
  anchoring, but 0 claims have multiple sources. The feature exists in
  schema but not in practice. Either the builder should produce multi-
  source anchors for eligible claims, or the schema should acknowledge
  single-source as the design.

### Orphan claim sample (20 of 151)

Patterns identified:

| Pattern | Approx % | Example ID |
|---------|----------|-----------|
| Synthesis (cross-sentence abstraction) | ~40% | 63, 664, 603 |
| Frontmatter / acknowledgments | ~20% | 386, 388, 389, 463 |
| Paraphrase drift (source exists) | ~30% | 431, 447 (duplicates) |
| Nested attribution (claims about claims) | ~10% | 640, 642, 512 |

Full sample recorded in SPEC-001. Data is the basis for proposing four-
state `anchor_status` taxonomy.

## Schema sanity

Full schema dump via `SELECT sql FROM sqlite_master WHERE type='table'`:

- `meta` — key/value manifest ✓
- `doc_nodes` — hierarchical tree with AUTOINCREMENT id ⚠
- `sqlite_sequence` — automatic, from AUTOINCREMENT
- `extractions` — LLM artifacts with AUTOINCREMENT id ⚠
- `claim_sources` — many-to-many bridge with proper FKs ✓
- `embeddings` — composite PK (node_id, level), BLOB vector ✓
- `nodes_fts` — FTS5 virtual table with external content ✓
- `nodes_fts_data`, `nodes_fts_idx`, `nodes_fts_docsize`,
  `nodes_fts_config` — FTS5 shadow tables, normal

Indexes: `idx_doc_nodes_parent`, `idx_doc_nodes_type` — reasonable for
traversal and filtering.

### Schema findings

- **FINDING S-01 (low, strategic):** AUTOINCREMENT integer primary keys on
  `doc_nodes` and `extractions` mean node IDs are not portable across
  cartridges. If two `.lun` files are merged or cross-reference each
  other, ID collisions are guaranteed. For the governance model to work,
  external references need stable identity (content hash or UUID).

- **FINDING S-02 (low):** No `application_id` pragma set. A file renamed
  to `.sqlite` is indistinguishable from `.lun` without reading the
  `meta` table. Setting `PRAGMA application_id = 0x4C554E01` or similar
  at build time gives a fast identification path.

- **FINDING S-03 (low):** No `PRAGMA user_version` set. The format
  tracks schema version in `meta.schema_version` but not in the SQLite
  pragma. Dual-tracking is redundant; pick one. Recommendation: set
  both at build time, have reader validate they agree.

- **POSITIVE:** Foreign keys declared on `claim_sources`. Composite
  primary key on `embeddings`. FTS5 with external content (avoids
  duplication). These are the right choices.

- **UNKNOWN:** Full schema of `doc_nodes` and `extractions` truncated
  in Fiddle output. Need to inspect builder source or re-query with
  wider output to confirm full column list.

## Summary of findings

| ID | Severity | Area | Summary | Spec |
|----|----------|------|---------|------|
| M-01 | medium | meta | Parser artifact title "/. Stephen Lansing" | TBD |
| M-02 | low | meta | source_path leaks builder environment | TBD |
| E-01 | high | extractions | Confidence is hardcoded constant, not data | SPEC-003 |
| C-01 | medium | anchoring | 151/1593 claims unanchored (9.5%) | SPEC-001 |
| C-02 | medium | anchoring | many-to-many schema, 1:1 in practice | SPEC-001 |
| S-01 | low (strategic) | schema | AUTOINCREMENT IDs not portable | SPEC-002 |
| S-02 | low | schema | No application_id pragma | TBD |
| S-03 | low | schema | No user_version pragma | TBD |

## Validation thesis proof

**The portability thesis holds.** A `.lun` file was opened in a generic
browser-based SQLite tool, queried with standard SQL, and audited without
any Luna-specific code. Every finding above was produced from SQL only.
This is exactly the property the format needs to preserve as it evolves.

**Corollary:** future `lun fsck` tooling can be a thin wrapper around
these same queries. No custom parser needed.

## Actions taken

1. SPEC-001 drafted for orphan claim classification
2. Findings tagged to forthcoming SPEC-002 (portable IDs) and SPEC-003
   (meaningful confidence)
3. Format v0.1 spec documented in `03_Format_Spec/LUN-FORMAT_v0.1.md`
   with "Known v0.1 limitations" section capturing these findings

## Recommended follow-ups

1. Re-run audit with full schema dump (no truncation) to capture complete
   column lists for `doc_nodes` and `extractions`
2. Audit a second cartridge to confirm findings are format-wide vs.
   specific to this build
3. Write `lun fsck` as a standalone Python script — no Luna imports, just
   sqlite3 + these queries — and commit to `06_Prototypes/`
4. Investigate the 30-section summary gap (207 summaries vs 237 sections)
