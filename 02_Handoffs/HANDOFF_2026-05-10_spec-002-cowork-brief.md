# HANDOFF: SPEC-002 brief for Cowork

**Date:** 2026-05-10
**From:** Ahab (with Claude)
**To:** Cowork research session
**Purpose:** Brief for drafting SPEC-002 (Portable Identifiers / ULID)

---

## Objective

Produce SPEC-002. Output file:

```
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/01_Specs/active/SPEC-002_portable-ids.md
```

Use `01_Specs/TEMPLATE.md` as the structure. Status when drafted: `active`. Severity: `high`. Affects format version: `v0.2` (additive) and `v0.3` (removal phase).

## Required reading

If Cowork has access to the Research directory, read these in order:

1. `01_Specs/TEMPLATE.md` — spec template
2. `03_Format_Spec/LUN-FORMAT_v0.1.md` — current cartridge schema
3. `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md` — Finding S-01 (the problem this spec solves) and the overall schema state
4. `05_Reference/SQLite_Research.md` — Topic 2 (Portable PKs) for the design choices; also Topic 6 (ATTACH for multi-cartridge query), Topic 7 (FTS5 shadow tables and migration)
5. `01_Specs/accepted/SPEC-006_v02-hygiene-bundle.md` — the application_id contract and version-tracking dependency
6. `01_Specs/accepted/SPEC-001_orphan-claims.md` — the claim_sources schema that SPEC-002 must extend, including the new provenance columns
7. `08_Journal/2026-05-10.md` — the application_id decision (for context, not directly required)

If Cowork lacks Research dir access, key inline extracts follow in the next section. Either way, write to the output path with Filesystem MCP.

## Inline source material (in case Cowork lacks directory access)

### From the v0.1 audit, Finding S-01

> AUTOINCREMENT integer primary keys on `doc_nodes` and `extractions` mean node IDs are not portable across cartridges. If two `.lun` files are merged or cross-reference each other, ID collisions are guaranteed. For the governance model to work, external references need stable identity (content hash or UUID).

### From SQLite_Research.md Topic 2 — the recommendation

ULID as 26-char TEXT in WITHOUT ROWID tables for any column that will be referenced across cartridge boundaries. Time-ordered (lexicographic sort = chronological sort), globally unique, no collision risk under any reasonable build rate, human-debuggable in SQL.

WITHOUT ROWID with TEXT PK: single clustered B-tree, single lookup, text stored once. Docs claim ~2× faster and ~half the disk space vs. ordinary rowid table with a UNIQUE index on the text column.

Keep INTEGER PK (rowid) for tables that never cross database boundaries — performance is better and there's no portability benefit to spend on.

Why ULID over UUIDv7: similar properties (time-ordered, globally unique), but ULID is 26 chars vs UUIDv7's 36, and ULID's Crockford Base32 encoding is more compact and case-stable than UUID dashed hex. Both work; ULID is the cleaner pick for a SQLite-native TEXT representation.

Why not UUIDv4: random-ordered. Inserts into a WITHOUT ROWID table with random-ordered keys cause B-tree page splits on every write (worst case: one split per insert). Time-ordered IDs cluster nicely.

Why not content hash (Git-style): content addressing implies dedup-by-content, which is correct for blobs (Fossil's `blob` table) but wrong for rows that should be distinct even when their content matches (two identical-text annotations are still two distinct annotations).

### From the v0.1 format spec — affected tables

```sql
-- doc_nodes: AUTOINCREMENT INTEGER PK; referenced by claim_sources, claim_context_nodes, embeddings, AND nodes_fts (FTS5 external content)
CREATE TABLE doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    type TEXT NOT NULL,
    ...
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);

-- extractions: AUTOINCREMENT INTEGER PK; referenced by claim_sources, claim_context_nodes
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ...
);

-- claim_sources: composite PK referencing both
CREATE TABLE claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    -- (SPEC-001 also adds: anchor_method, anchored_by, anchored_at, event_id)
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);

-- nodes_fts: FTS5 external content, requires content_rowid to be INTEGER
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'   -- this is the constraint that prevents WITHOUT ROWID on doc_nodes
);
```

---

## Pre-made architectural decisions

These are settled. Document them in the spec; don't re-litigate.

### D1. ULID as TEXT, not BLOB

- 26-char Crockford Base32, uppercase canonical form
- Stored as `TEXT` in SQLite — no binary encoding dependencies, queryable directly in `sqlite3` CLI, human-readable
- Validate format at the application layer (regex `^[0-9A-HJKMNP-TV-Z]{26}$`); reject malformed values at insert time
- ULIDs are monotonic-per-millisecond per the ULID spec — batch generators must produce strictly increasing values within the same ms timestamp

### D2. `doc_nodes` keeps its INTEGER rowid; adds a `ulid` column for portable identity

- **Why:** FTS5 external content mode requires `content_rowid` to be INTEGER. Switching `doc_nodes` to WITHOUT ROWID would break the existing `nodes_fts` virtual table and require re-architecting full-text search. Not worth it.
- `doc_nodes.id` stays as `INTEGER PRIMARY KEY AUTOINCREMENT` (rowid)
- Add `doc_nodes.ulid TEXT UNIQUE NOT NULL` with a Python-generated value at build time
- All cross-cartridge references go through `ulid`, not `id`
- FTS5 continues to use the integer rowid for its internal linkage

### D3. `extractions` migrates to ULID PK in a WITHOUT ROWID table

- **Why:** No FTS5 dependency, no other constraints forcing rowid retention. WITHOUT ROWID's space/speed advantage applies cleanly.
- New schema:
  ```sql
  CREATE TABLE extractions (
      ulid TEXT PRIMARY KEY,        -- ULID, e.g. '01HQ3KZXD4FGTW8N5PJKZMBV3R'
      type TEXT NOT NULL,
      content TEXT NOT NULL,
      -- ... existing columns plus SPEC-001's anchor_status, anchor_reason
      CHECK (ulid GLOB '[0-9A-HJKMNP-TV-Z][0-9A-HJKMNP-TV-Z]*')
  ) WITHOUT ROWID;
  ```
- Migration is non-trivial because `claim_sources.claim_id` and `claim_context_nodes.claim_id` reference the old integer `id`. See migration phases below.

### D4. Reference tables migrate to TEXT ULID columns

- `claim_sources`: `claim_id` and `node_id` both become `TEXT NOT NULL` (the ULIDs). FK references go to `doc_nodes(ulid)` and `extractions(ulid)`. SQLite supports FK references to any `UNIQUE` column (not just PRIMARY KEY), so this works.
- `claim_context_nodes` (from SPEC-001): same pattern — `claim_id` and `node_id` become TEXT.
- `embeddings`: `node_id` becomes TEXT; composite PK `(node_id, level)` stays composite.

### D5. Two-phase migration plan

The migration cannot be single-step without breaking v0.2 readers that haven't been updated yet. Phase the work:

- **v0.2 (additive, this spec):** Add new ULID columns alongside existing integer columns. Both work. Builder writes both. Readers can use either. The integer columns remain as the live PK; the ULID columns are "shadow IDs" that prove out for cross-cartridge use.
- **v0.3 (removal, future spec):** Once all readers have moved to ULID-only, drop the integer columns. `extractions` becomes WITHOUT ROWID with ULID PK. References fully switch to ULID FKs.

This staging lets v0.2 ship without coordination across the entire codebase. The cost is temporary schema bloat (every row carries two IDs) for a release cycle.

### D6. ULID generation happens in Python at build/migration time

- SQLite has no native ULID function
- Python ULID implementation is trivial (~30 lines) or available as `python-ulid` package
- Generate at insert/migration time; never at read time
- Migration tool generates ULIDs in `(id, ulid)` pairs and writes them in a single transaction with deterministic ordering (oldest rowid → earliest ULID timestamp) to preserve a sense of temporal sequence even though the original integer order didn't carry timestamps

### D7. Cross-cartridge references via ULID are application-enforced, not FK-enforced

- SQLite FKs don't work across attached databases (per SQLite_Research.md Topic 6)
- A future cross-cartridge annotation referencing `node_id = '01H...'` in cartridge A from cartridge B has no FK enforcement
- Application code is responsible for resolving cross-cartridge refs and handling missing references gracefully
- This is a property of SQLite, not a limitation of the spec

---

## Architectural questions to address (Cowork should reason through these)

These don't have pre-baked answers; the brief expects Cowork to think and document a recommendation, citing the research where applicable.

### Q1. Index strategy for the new ULID columns in v0.2

What indexes should the spec require? `doc_nodes.ulid UNIQUE` is already needed for the FK target. What about `extractions.ulid` — does the v0.2 additive phase need a separate index, or does the eventual PRIMARY KEY in v0.3 make this moot? Same question for reference tables.

### Q2. Validation of hand-edited cartridges with malformed ULIDs

The CHECK constraint enforces the GLOB pattern, but only on writes. Read-time validation in `lun fsck` should catch any malformed ULIDs that somehow made it in. Spec the validation regex and the fsck check.

### Q3. Whether to expose old integer IDs as deprecated columns in v0.2 or remove silently in v0.3

Two stances: (a) keep the integer columns visible but mark them deprecated in `meta` (e.g., `meta.deprecated_columns = 'extractions.id,doc_nodes.id'`), so external tools know to ignore them; (b) just drop in v0.3 with no marker. Recommend one with reasoning.

### Q4. Sanity check between rowid and ULID during migration

Should the migration tool record the rowid→ULID mapping somewhere durable (e.g., a transient `_migration_log` table), or generate-and-forget? Forensic value vs. schema clutter.

### Q5. Behavior when a v0.1 cartridge is opened by a v0.2 reader

The reader expects ULID columns to exist. v0.1 cartridges don't have them. Two paths: (a) reader detects missing columns and falls back to integer mode (compatibility shim); (b) reader requires migration before opening. Recommend one. Cross-reference SPEC-006's read-time validation pattern.

---

## Spec sections to produce

Match the depth and style of SPEC-006 and SPEC-001 (both in `01_Specs/accepted/`). Include real SQL DDL for every schema change. Include Python pseudocode for migration and validation. Be honest about trade-offs in Alternatives.

### Problem statement
- One paragraph stating the cross-cartridge identity problem
- Anchor to the governance arc: without portable identity, annotations can't reference claims across cartridge boundaries (SPEC-005 dependency)

### Observed evidence
- S-01 from the April audit
- SQLite_Research.md Topic 2 background on how PK choices affect cross-DB references

### Root cause
- AUTOINCREMENT is a per-database counter
- Rowids collide on merge
- The format has no stable cross-DB identity layer

### Proposed solution

Schema changes: full DDL for each affected table (`doc_nodes`, `extractions`, `claim_sources`, `claim_context_nodes`, `embeddings`).

Behavioral changes: how the builder generates ULIDs, how the reader resolves references, how the validate function checks ULID format.

Migration path: the two-phase plan. v0.2 additive phase in detail. v0.3 removal phase sketched (full spec deferred).

### Migration mechanics (SQLite-specific)

Document the ADD COLUMN semantics for each new ULID column. `ALTER TABLE ... ADD COLUMN ulid TEXT` is O(1); `ADD COLUMN ulid TEXT UNIQUE` is O(n) (must build the unique index). Plan the migration to add the column first, populate it in a separate UPDATE pass, THEN add the UNIQUE constraint (in 3.47+ via `ADD CONSTRAINT`, or in older versions by creating a UNIQUE index post-populate).

Cross-reference: SQLite_Research.md Topic 5.

### Validation rules

- Build time: every row has a valid ULID, ULIDs are unique within their table, reference columns point to valid ULIDs in the target table
- Read time: same checks, plus format regex
- The fsck check function in Python pseudocode

### Governance implications

- Cross-cartridge annotations need stable identity (this spec enables them)
- ULID's temporal ordering gives a free "this claim was created before that one" signal that the ledger can use
- Cross-cartridge FK enforcement is application-level per D7

### Alternatives considered

Pre-evaluated; document with the rejection rationale from SQLite_Research.md Topic 2:

1. UUIDv4 (random) — rejected, B-tree page splits
2. UUIDv7 (time-ordered) — viable but ULID more compact and cleaner in TEXT form
3. Content hash (Git-style) — rejected, conflates content with identity
4. Keep INTEGER PKs and add a separate UUID lookup table — rejected, indirection without benefit
5. Single-phase migration (drop integer columns immediately in v0.2) — rejected, coordination burden on the codebase

### Open questions

List anything genuinely undecidable from this brief (Q1–Q5 above plus anything Cowork discovers during drafting).

### Dependencies

- SPEC-006 (accepted) — establishes `application_id` and the v0.2 version-tracking pair. SPEC-002 builds on top.
- SPEC-001 (accepted) — establishes the `claim_sources` provenance columns that SPEC-002's migration must preserve.

Blocks:
- SPEC-005 (planned): Annotation ledger needs portable identity to reference claims across cartridges.
- v0.3 removal-phase spec (future): drops the integer columns.

---

## Cross-reference checklist

The drafted spec MUST cite each of these at least once in the appropriate section:

- [ ] `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md` (Finding S-01) — in Observed evidence
- [ ] `05_Reference/SQLite_Research.md`, Topic 2 — in Proposed solution and Alternatives
- [ ] `05_Reference/SQLite_Research.md`, Topic 5 — in Migration mechanics
- [ ] `05_Reference/SQLite_Research.md`, Topic 6 — in Governance implications (cross-DB FK note)
- [ ] `05_Reference/SQLite_Research.md`, Topic 7 — in the FTS5 constraint discussion
- [ ] `01_Specs/accepted/SPEC-006_v02-hygiene-bundle.md` — in Dependencies and read-time validation
- [ ] `01_Specs/accepted/SPEC-001_orphan-claims.md` — in Dependencies (claim_sources schema)
- [ ] `08_Journal/2026-05-10.md` — optional but useful, in context

## Style and depth notes

- Match SPEC-006 and SPEC-001 detail level (each is ~6KB)
- SQL DDL for every schema change, exactly as the implementation should write it
- Python pseudocode for migration and validation functions, with realistic variable names
- "Alternatives considered" should be substantive — explain why each was rejected, not just list them
- Open questions, if any remain after drafting, should be numbered and specific (Ahab will resolve them in review before the spec moves to `accepted/`)
- Sentence-level voice: direct, technical, no marketing language. Match the existing accepted specs.

---

## What NOT to do

- Don't draft the v0.3 removal-phase spec (out of scope; this brief covers v0.2 additive only)
- Don't invent new test data or audit findings — the only audit evidence is from `PRIESTS_AND_PROGRAMMERS_Lansing.lun`
- Don't propose changes to FTS5 setup — D2 settles that
- Don't recommend UUIDv4, content-hash PKs, or any non-time-ordered scheme — those are pre-rejected
- Don't introduce a new cartridge_kind or application_id; both are settled by SPEC-006

---

## Verification before delivery

Cowork should self-check the draft against:

1. Every "Pre-made decision" (D1–D7) appears in the spec exactly as specified
2. Every "Cross-reference checklist" item is cited at least once
3. Every section in `TEMPLATE.md` is present in the draft (Problem statement → Implementation notes)
4. The SQL DDL is syntactically valid SQLite
5. Open questions section either lists genuine remaining decisions OR says "None remaining" with brief resolution notes
6. The spec's tone, length, and detail level matches SPEC-006 and SPEC-001

Once the draft is complete, output to the path in the Objective section. Ahab will review before any move to `accepted/`.
