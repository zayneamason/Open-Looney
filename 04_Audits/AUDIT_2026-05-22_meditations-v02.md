# AUDIT: Marcus-Aurelius-Meditations.lun

**Audit date:** 2026-05-22
**Auditor:** Claude (research-repo session, on behalf of Ahab)
**Cartridge built:** 2026-05-22T06:36:23+00:00 (engine commit `325c68b`)
**Format version:** v0.2 (`user_version = 2`, `application_id = 0x4C554E43`)
**Tool:** `sqlite3` CLI + Tauri reader at `06_Prototypes/ReaderPrototype/`
**Source file:** Marcus-Aurelius-Meditations.pdf

---

## Purpose

First audit of a v0.2 production cartridge using generic SQLite tooling AND the standalone
Tauri reader. Validates the v0.2 portability claim end-to-end (a `.lun` cartridge is
inspectable by a stock SQLite client AND renderable by an independent reader implementation
with zero Luna runtime dependencies).

The v0.2-era baseline: this audit's measurements anchor what a healthy v0.2 cartridge looks
like. Future cartridges and specs will be measured against the values recorded here.

## Method

Two-tool approach:

1. **`sqlite3` CLI** for structured queries. All queries are inline in this document so the
   findings are reproducible by anyone with `sqlite3` and the cartridge file.
2. **Tauri reader** at `06_Prototypes/ReaderPrototype/` — opened the same cartridge through
   the `open_cartridge` / `get_meta` / `list_all_nodes` / `list_extractions` /
   `get_claim_sources` / `search` Tauri commands. The reader is the v0.2-era portability
   proof; it builds and runs with no Luna engine dependency (`Cargo.toml` and `package.json`
   contain zero `luna`-named deps) and passes 21 Rust tests against this cartridge.

Cross-reference between the two tools: every count produced by the `sqlite3` queries below
matches what the reader displays in its tree / extractions / search panes.

## Meta contents

```sql
SELECT key, value FROM meta ORDER BY key;
```

```
cartridge_kind          knowledge
created_at              2026-05-22T06:36:23.354052+00:00
deprecated_columns      doc_nodes.id,extractions.id
embedding_dim           384
embedding_model         all-MiniLM-L6-v2
format_version          0.2
logprob_attribution     response_level
logprob_base            e
node_count              3813
source_filename         Marcus-Aurelius-Meditations.pdf
source_format           pdf
source_hash             b02870de0fbf771af42aefcdd6a6ccc4386381fa20d3513242acf8b4f9c25aca
title                   The meditations of
word_count              76651
```

### Meta findings

- **FINDING M-01 (medium).** `title = "The meditations of"` is semantically truncated. The
  expected title is "The Meditations of Marcus Aurelius" or similar. The string passes the
  v0.2 title parser-artifact blocklist (length 18 ≥ 3, no leading-glyph prefix, contains
  alphanumerics, not in `{untitled, document, document1}`) — but it ends in a function word
  ("of") and is clearly mid-phrase. The blocklist catches *parser garbage*; it does not catch
  *truncation at a legitimate word boundary*. Mirror of the v0.1-era title-parser issue in
  spirit but a different failure mode. Forward-ref: extend the blocklist with a
  "trailing-function-word" or minimum-word-count heuristic, or move title selection further
  into the document body. (Builder/parser concern; engine-repo follow-up.)

- **POSITIVE.** All required v0.2 meta keys present (per `LUN-FORMAT_v0.2.md` ln 70-83):
  `format_version`, `cartridge_kind`, `source_filename`, `source_format`, `source_hash`,
  `created_at`, `word_count`, `node_count`, `embedding_model`, `embedding_dim`,
  `logprob_base`, `logprob_attribution`, `deprecated_columns`.

- **POSITIVE.** `format_version = '0.2'` matches `PRAGMA user_version = 2`. The
  reader-trusted source-of-truth (`user_version`) and the human-readable mirror agree.

- **POSITIVE.** `cartridge_kind = 'knowledge'` ∈ `SUPPORTED_CARTRIDGE_KINDS`.

- **POSITIVE.** `logprob_base = 'e'` and `logprob_attribution = 'response_level'` — SPEC-003
  v0.2 contract markers present.

- **POSITIVE.** `deprecated_columns = 'doc_nodes.id,extractions.id'` — SPEC-002's machine-
  readable signal for v0.3 removal is in place.

- **POSITIVE.** `source_filename = 'Marcus-Aurelius-Meditations.pdf'` is basename only —
  no leaked builder-machine paths (v0.1 M-02 cleanly addressed by SPEC-006).

- **POSITIVE.** Forbidden v0.1 keys (`source_path`, `schema_version`) absent — verified by
  their omission from the meta dump.

- **POSITIVE.** `source_hash` is 64-char hex (SHA-256 format), `embedding_model` and
  `embedding_dim` both set (reader validation enabled).

- **POSITIVE.** `meta.node_count = 3813` matches `SELECT count(*) FROM doc_nodes`
  (cross-checked below).

## Document tree

```sql
SELECT type, COUNT(*) FROM doc_nodes GROUP BY type ORDER BY 2 DESC;
```

```
sentence    3326
paragraph    310
section      176
document       1
            -----
TOTAL       3813
```

```sql
SELECT 'direct_sections_under_document', COUNT(*) FROM doc_nodes
  WHERE type='section' AND parent_id=1
UNION ALL
SELECT 'sections_nested_under_other_sections', COUNT(*) FROM doc_nodes
  WHERE type='section' AND parent_id IN (SELECT id FROM doc_nodes WHERE type='section');
```

```
direct_sections_under_document         128
sections_nested_under_other_sections    48
total_sections                         176
```

```sql
SELECT type,
       SUM(CASE WHEN content IS NULL THEN 1 ELSE 0 END) AS null_content,
       COUNT(*) AS total
FROM doc_nodes GROUP BY type;
```

```
document      1 of    1
paragraph   310 of  310
section     128 of  176
sentence      0 of 3326
            ------------
TOTAL       439 of 3813   (11.5%)
```

### Tree findings

- **POSITIVE.** Total (3813) matches `meta.node_count`. Builder is internally consistent on
  node counting.

- **POSITIVE.** Node-type vocabulary observed = `{document, section, paragraph, sentence}`,
  the dominant v0.2 set. The richer `list/list_item/figure/table/row/cell` vocabulary (now
  declared in the format spec per Phase 1) is not exercised by this PDF source — expected,
  the rich nodes are primarily Markdown territory.

- **POSITIVE.** Container nullability matches the format-spec contract (Phase 1 amendment):
  the single `document` row carries NULL content; all 310 `paragraph` rows carry NULL content
  (their text lives in 3326 `sentence` children); 128 of 176 `section` rows carry NULL
  content (the title-page sections and page-marker sections); all 3326 `sentence` rows have
  non-NULL content. Total NULL-content rate of 11.5% is consistent with a PDF source where
  paragraphs are container-only and sections are often page-marker boundaries.

- **OBSERVATION.** Sentence-to-paragraph ratio is 3326 / 310 = 10.73. Comparable to dense
  prose; reasonable for a 76,651-word translated classical text with heavy aphoristic
  structure. No sentence-splitter sanity flag.

- **OBSERVATION.** Section nesting distribution: 128 sections directly under the document
  (book-level or page-level dividers) + 48 nested sections (sub-sections within books or
  page-spanning sections). This is the structural reality the Reader Prototype's v1 build
  surfaced; the format spec's `doc_nodes` hierarchy now documents that sections may nest
  (Phase 1 amendment).

- **FINDING T-01 (low).** PDF parser splits the title page into multiple section rows.
  Sampling the first sections by ID:

  ```
  id=6  section  "The meditations of"
  id=7  section  "Marcus Aurelius Antoninus"
  id=8  section  "Originally translated by Meric Casaubon"
  ```

  These three rows are the title block on the source's title page, but the parser treats
  each visual line as a separate `section`. The title-truncation finding M-01 above stems
  from `meta.title` pulling the content of section id=6 verbatim. Resolution path:
  recognize multi-line title blocks at parse time, OR pull `meta.title` from a more
  authoritative source (PDF metadata, first H1-like heading further in). (Builder/parser
  concern; engine-repo follow-up.)

## Extractions

```sql
SELECT type, COUNT(*) FROM extractions GROUP BY type ORDER BY 2 DESC;
```

```
entity    532
claim     512
summary    62
          ---
TOTAL    1106
```

```sql
SELECT extraction_method, COUNT(*) FROM extractions GROUP BY extraction_method;
```

```
llm    1106    (100%)
```

```sql
SELECT (llm_logprob_sum IS NULL) AS lp_null,
       (llm_token_count IS NULL) AS tc_null,
       COUNT(*)
FROM extractions GROUP BY lp_null, tc_null;
```

```
lp_null=1, tc_null=1, count=1106    (all NULL, paired-NULL invariant holds)
```

### Extraction findings

- **POSITIVE.** `extraction_method` is `'llm'` for 100% of rows — matches the v0.2 SPEC-003
  CHECK constraint (`'llm' | 'rule' | 'ner' | 'manual'`). No rule/NER/manual rows in this
  cartridge.

- **POSITIVE.** Paired-NULL invariant from SPEC-003 holds: every row has both
  `llm_logprob_sum` and `llm_token_count` either both NULL or both populated. Here, all 1106
  rows are paired-NULL.

- **KNOWN (carried-forward).** All `llm_logprob_sum` / `llm_token_count` values are NULL.
  This is the Phase 5 deferral item 5 from `08_Journal/2026-05-21.md` ("Backend logprobs not
  fully exposed — `HaikuResult.usage` fields are not surfaced to the builder"). Documented;
  not a new finding.

- **OBSERVATION.** Extraction counts: 512 claims + 532 entities + 62 summaries. Entities
  slightly outnumber claims, consistent with a text dense with proper nouns (Stoics, Roman
  political figures, places, philosophical concepts named as nouns). Reasonable.

- **OBSERVATION.** 62 summaries for 176 sections = 35.2% section coverage. The builder ran
  per-section summaries but produced output for only ~1/3 of sections. Possible causes:
  short-section skipping (the title-page sections at ids 6-8 above are very short), or
  summary-skipping on sections with NULL content (128 of 176 sections are content-NULL).
  Worth investigating. Forward-ref: builder/parser engineering question.

## Claim anchoring

```sql
SELECT anchor_status, COUNT(*) FROM extractions WHERE type='claim' GROUP BY anchor_status;
```

```
anchored        458
match_failed     54
                ---
TOTAL claims    512
```

Headline ratio: `match_failed / (anchored + match_failed) = 54/512 = 10.5%` for this v0.2
cartridge. (Reported absolute; no comparison to other cartridges.)

```sql
SELECT anchor_status, COUNT(*) FROM extractions WHERE type='entity' GROUP BY anchor_status;
SELECT anchor_status, COUNT(*) FROM extractions WHERE type='summary' GROUP BY anchor_status;
```

```
entity   unknown    532    (per SPEC-001: entities scoped out of v0.2 anchor classification)
summary  anchored    62    (every summary is anchored 1:1 to a node)
```

```sql
SELECT COUNT(*) AS claim_source_rows,
       COUNT(DISTINCT claim_id) AS distinct_extraction_ids
FROM claim_sources;

SELECT COUNT(*) FROM extractions
  WHERE type='claim' AND anchor_status='anchored'
    AND id NOT IN (SELECT claim_id FROM claim_sources);

SELECT COUNT(*) FROM claim_sources
  WHERE node_id NOT IN (SELECT id FROM doc_nodes);

SELECT COUNT(*) FROM claim_sources
  WHERE claim_id NOT IN (SELECT id FROM extractions);
```

```
claim_source_rows               520
distinct_extraction_ids         520
orphan_anchored_claims          0
cs_pointing_at_missing_nodes    0
cs_with_missing_extractions     0
```

Per-extraction source distribution:

```sql
SELECT source_count, COUNT(*) AS num_extractions FROM (
    SELECT claim_id, COUNT(*) AS source_count FROM claim_sources GROUP BY claim_id
) GROUP BY source_count;
```

```
source_count=1, num_extractions=520    (100% are 1:1)
```

Type-of-extraction breakdown for `claim_sources`:

```sql
SELECT e.type, COUNT(DISTINCT cs.claim_id) AS distinct_ids, COUNT(*) AS rows
FROM claim_sources cs JOIN extractions e ON cs.claim_id = e.id
GROUP BY e.type;
```

```
claim      458    458
summary     62     62
```

`claim_context_nodes` row count: `0` (no `synthesized` claims in this cartridge — vacuously
satisfies the SPEC-001 ≥2-multi-lineage invariant).

### Anchoring findings

- **POSITIVE.** Every `anchor_status = 'anchored'` claim has at least one `claim_sources` row
  (SPEC-001 invariant holds, 0 orphans).

- **POSITIVE.** Every `claim_sources` row points at a valid `doc_nodes` row and references a
  valid `extractions` row (referential integrity holds).

- **POSITIVE.** No claim carries `anchor_status = 'unknown'` (SPEC-001 hard gate holds —
  this would otherwise block the build at `validate_anchors()`).

- **POSITIVE.** Entities carry `unknown` legitimately per SPEC-001 (entity anchoring deferred
  to a future spec); explained, not flagged.

- **OBSERVATION.** All anchored extractions (458 claims + 62 summaries = 520) are 1:1
  anchored — no extraction has multiple sources. The `claim_sources` schema supports
  many-to-many; the builder produces 1:1 in practice. Matches the format spec's note at
  `LUN-FORMAT_v0.2.md` § "Provenance columns" ("Builder-produced anchors are currently 1:1;
  the schema supports multi-source for future use"). Continues the same v0.1-era pattern;
  not a new structural concern for v0.2.

- **FINDING C-01 (low).** The `claim_sources` table is named for claims but actually anchors
  multiple extraction types: 458 claim rows + 62 summary rows. The format spec calls the
  table "claim-to-source anchoring" (`LUN-FORMAT_v0.2.md` § `claim_sources`), and the column
  is named `claim_id`. The semantic mismatch is small (summaries-are-claim-like in this
  taxonomy) but a future spec should either (a) rename the column/table to
  `extraction_sources` / `extraction_id`, or (b) document explicitly in the format spec that
  `claim_sources.claim_id` references any `extractions.id` regardless of type. Forward-ref:
  format-spec clarification or v0.3 schema rename.

- **OBSERVATION.** Match-failed rate of 10.5% (54/512) is the v0.2 Meditations baseline.
  Future cartridges will be measured against this.

## Schema sanity

```sql
PRAGMA application_id;
PRAGMA user_version;
PRAGMA journal_mode;
```

```
application_id    1280659011    (= 0x4C554E43 = 'LUNC')
user_version      2
journal_mode      delete
```

```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
```

```
claim_context_nodes      claim_sources         doc_nodes
embeddings               extractions           meta
nexus_refs               nodes_fts             nodes_fts_config
nodes_fts_data           nodes_fts_docsize     nodes_fts_idx
sqlite_sequence          sqlite_stat1          sqlite_stat4
```

```sql
SELECT COUNT(*) FROM nexus_refs;
SELECT COUNT(*) FROM nodes_fts WHERE nodes_fts MATCH 'virtue';
```

```
nexus_refs row count             0    (SPEC-005 placeholder, empty in v0.2 per format spec)
FTS5 'virtue' MATCH hits        25
```

```sql
SELECT substr(ulid, 1, 1) AS first_char, COUNT(*) FROM doc_nodes GROUP BY first_char;
SELECT substr(ulid, 1, 1), COUNT(*) FROM extractions GROUP BY 1;

SELECT COUNT(*) FROM doc_nodes
  WHERE NOT (length(ulid)=26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*');
SELECT COUNT(*) FROM extractions
  WHERE NOT (length(ulid)=26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*');
```

```
doc_nodes ULID first-char distribution    : '0' x 3813
extractions ULID first-char distribution  : '0' x 1106
doc_nodes ULID format violations          : 0
extractions ULID format violations         : 0
```

```sql
SELECT level, COUNT(*) FROM embeddings GROUP BY level;
SELECT length(vector) FROM embeddings LIMIT 1;
SELECT COUNT(*) FROM doc_nodes
  WHERE type='section' AND id NOT IN (SELECT node_id FROM embeddings WHERE level='section');
```

```
embeddings.paragraph   310    (matches paragraph count: 310/310)
embeddings.section     149    (vs 176 sections: 84.7% coverage)
vector byte length     1536   (= 384 dim × 4 bytes/float; matches meta.embedding_dim contract)
sections without embedding    27
```

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

```
integrity_check     ok
foreign_key_check   (empty)
```

Form-feed (`\x0c`) artifact check (per `08_Journal/2026-05-21.md` item 13):

```sql
SELECT COUNT(*) FROM doc_nodes WHERE content LIKE '%' || char(12) || '%';
```

```
rows_with_formfeed    0
```

### Schema findings

- **POSITIVE.** `application_id`, `user_version`, `journal_mode` all match v0.2 contract.
  Shipping mode (no `-wal` / `-shm` sidecar files travel with the cartridge).

- **POSITIVE.** All ULIDs pass format validation: 26 chars, Crockford Base32 alphabet, first
  char in `[0-7]`. All 4919 ULIDs (3813 doc_nodes + 1106 extractions) start with `0`,
  consistent with generation in a single short timestamp band on 2026-05-22; no GLOB
  violations.

- **POSITIVE.** `nexus_refs` table present with zero rows — matches the format spec's
  Phase-1-amended declaration ("Placeholder table created by v0.2 builders for SPEC-005;
  empty in v0.2 cartridges").

- **POSITIVE.** Composite primary keys on `embeddings (node_id, level)`,
  `claim_sources (claim_id, node_id)`, `claim_context_nodes (claim_id, node_id)`,
  `nexus_refs (local_node_id, node_type)`.

- **POSITIVE.** FTS5 `nodes_fts` virtual table responds to queries; sample search for
  "virtue" returns 25 hits (semantically reasonable for a Stoic text). All four FTS5 shadow
  tables (`nodes_fts_data`, `nodes_fts_idx`, `nodes_fts_docsize`, `nodes_fts_config`) are
  present.

- **POSITIVE.** `sqlite_stat1` and `sqlite_stat4` tables present — `PRAGMA optimize` in the
  finalize stack ran successfully and produced query-planner statistics.

- **POSITIVE.** Embedding vector length is exactly `384 × 4 = 1536` bytes, matching
  `meta.embedding_dim = 384` and the SPEC-003 reader contract.

- **POSITIVE.** `PRAGMA integrity_check = 'ok'` and `PRAGMA foreign_key_check` is empty.

- **POSITIVE.** No `\x0c` form-feed characters in `doc_nodes.content`. The artifact flagged
  by the 2026-05-21 journal item 13 is absent from this cartridge — either this PDF source
  is clean of form-feed glyphs, or the parser handles them. Item 13 does not apply here.

- **FINDING S-01 (low) — CLASSIFIED 2026-07-23: expected builder policy, not a defect.**
  Original v0.2 observation: 149 of 176 sections had a `section`-level embedding (84.7%);
  27 sections did not. Post-M-01 reference cartridge (`Marcus-Aurelius-Meditations.v03.lun`)
  is **149/166** (17 skipped): M-01 merged ~10 fragmented headings, so the old 149/176
  denominator is obsolete. Classification: the builder skips section embed when the
  recursive subtree yields no non-empty `sentence` / `list_item` / `cell` content (textless
  structural sections). Paragraph embeddings remain 310/310. Format already documents
  best-effort coverage (`LUN-FORMAT_v0.3.md` Coverage policy). No Engine embedder change
  required.

## Portability proof

The v0.2 portability thesis holds.

The same `.lun` file was opened, validated, browsed, and searched through **two independent
implementations** with zero shared code beyond the SQLite library:

1. **`sqlite3` CLI** — every finding above was produced from SQL queries against the file.
   The queries are reproducible by anyone with a stock SQLite 3.x install.
2. **Tauri reader** at `06_Prototypes/ReaderPrototype/` — Rust + `rusqlite` + React. The
   reader's `Cargo.toml` and `package.json` contain zero `luna`-named dependencies; the
   project builds and runs from a clean checkout without the engine repo present. It
   passes 21 Rust tests against this cartridge, covering the 5-step open contract (6
   rejection paths), full-tree reconstruction, rich Markdown node-type compatibility, and
   `list_nodes` / `get_node` / `list_extractions` / `get_extraction_counts` /
   `get_claim_sources` / `search` against the reference cartridge.

The format's `application_id = 0x4C554E43` (`'LUNC'`) makes fast-rejection a single-pragma
check: tools that don't speak the cartridge family can refuse the file before any schema
parsing. SPEC-006's central design claim is exercised here.

**Corollary.** A `lun fsck` tool can still be a thin wrapper around the queries in this
audit — no custom parser needed, just `sqlite3` + the validation rules.

## Summary of findings

| ID   | Severity | Area         | Summary                                                     | Forward-ref                                |
|------|----------|--------------|-------------------------------------------------------------|--------------------------------------------|
| M-01 | medium   | meta         | `title = "The meditations of"` is semantically truncated    | Builder/parser; extend title blocklist     |
| T-01 | low      | tree         | PDF parser splits title-page text into separate sections    | Builder/parser; title-block recognition    |
| C-01 | low      | anchoring    | `claim_sources.claim_id` actually anchors any extraction type (claims AND summaries) | Format-spec clarification or v0.3 rename |
| S-01 | low      | schema       | Section embedding coverage — CLASSIFIED expected policy; post-M-01 **149/166** (was 149/176) | Closed 2026-07-23 — no builder defect |

Plus 22 POSITIVE findings (meta contract complete, all SPEC-001/002/003/006 invariants hold,
integrity check passes, reader/sqlite3 cross-validation succeeds), 4 OBSERVATIONS (counts and
ratios for future-cartridge baseline), and 1 KNOWN carried-forward issue (Phase 5 deferral
item 5: backend logprobs not exposed). Findings already resolved by the Phase 1 format-spec
amendments — `doc_nodes.content` nullability, `meta_json` per-source shapes,
`claim_sources` shadow ULIDs, `nexus_refs` declaration — are not re-discovered here (per
roadmap §219; see `06_Prototypes/ReaderPrototype/SPEC.md` § "Findings produced during v1
build (2026-05-22)").

The v0.2 contract is healthy. Every numeric expectation from the four implemented specs
holds in this cartridge.

## Recommended follow-ups

### Research-repo

1. **Format-spec clarification on `claim_sources` semantics (C-01).** Either declare
   explicitly that `claim_sources.claim_id` references any `extractions.id` regardless of
   type, or queue a v0.3 rename to `extraction_sources` / `extraction_id`. Low priority but
   easy to fix in a doc-only amendment.

2. **Format-spec note on embedding coverage policy (S-01).** Add a note to the
   `embeddings` schema block that section-level embedding coverage is best-effort and may
   skip very-short or NULL-content sections; clarify whether full coverage is required for
   any reader invariant.

3. **Establish v0.2 baseline measurements doc.** This audit's key numbers
   (54/512 = 10.5% match-failed claims, 84.7% section embedding coverage, 35.2% section
   summary coverage, 11.5% NULL-content rate on doc_nodes, etc.) should be collected into a
   shared "v0.2 cartridge baselines" reference that future cartridges' audits can compare
   against without re-deriving. Could live in `04_Audits/` as a baselines doc, or as a
   section in the format spec.

4. **Update `MEMORY.md` project_v02_status.** Add "audit complete 2026-05-22; reference
   baselines captured" to the project-state memory so future sessions don't re-derive the
   numbers.

### Engine-repo (named only — drafting belongs to a separate engine-side handoff)

1. **Title parser improvement (M-01, T-01).** PDF parser's title block recognition needs
   to handle multi-line titles, and the title-validation blocklist needs a "trailing
   function-word" or minimum-meaningful-word-count heuristic.

2. **Section embedding gap investigation (S-01).** Identify why 27 sections lack
   `section`-level embeddings. If intentional (short-section skipping), document the
   threshold; if accidental, fix.

3. **Section summary gap investigation (62/176 = 35.2%).** Why does the per-section
   summarizer produce output for only ~1/3 of sections? May be the same root cause as the
   embedding gap (short / NULL-content sections being skipped).

4. **Backend logprob exposure (carried-forward Phase 5 item 5).** `HaikuResult.usage`
   fields are not surfaced to the builder; current paired-NULL satisfies SPEC-003 vacuously
   but the response-level signal isn't being captured. v0.3 backend-side improvement,
   already on the deferred list.
