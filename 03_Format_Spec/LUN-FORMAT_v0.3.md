# .lun Cartridge Format Specification — Version 0.3

**Status:** Shipping (2026-05-22; engine commit `407122f`; Meditations v0.3 audit passed)
**Scope:** Cartridge family (`application_id = 0x4C554E43`, `'LUNC'`). The runtime matrix family (`'LUNM'`) is a sibling format with a different schema; it will get its own spec when its schema stabilizes.
**Source:** Consolidates SPEC-005 (annotation ledger, implemented 2026-05-22), SPEC-005_payload-schemas (implemented 2026-05-22), SPEC-002 D5 (integer-rowid removal, named in `01_Specs/implemented/SPEC-002_portable-ids.md` Phase 2), and the C-01 audit follow-up from `04_Audits/AUDIT_2026-05-22_meditations-v02.md` (rename `claim_sources` → `extraction_sources`).
**Builder version:** Luna Engine commit `407122f` (`feat(cartridge): SPEC-005 v0.3 schema + annotation ledger + lun fsck`).
**Supersedes:** v0.2 (`LUN-FORMAT_v0.2.md`). v0.3 is Shipping after the Meditations v0.3 reference cartridge audit passed with no shipping blockers.
**Lifecycle note:** This spec was drafted ahead of engine implementation to consolidate SPEC-005, SPEC-002 D5, and audit finding C-01 into one implementation target. Q1, Q2, and Q3 are resolved: Q1 selected Strategy A for FTS5, Q2 selected the symmetric `extraction_context_nodes` rename, and Q3 was validated by engine commit `407122f` plus the SPEC-005 verification suite. The Meditations v0.3 audit passed on 2026-05-22 and promoted this format to Shipping.

---

## Overview

A v0.3 `.lun` cartridge is a SQLite 3 database with everything v0.2 provided
(known `application_id`, explicit `user_version`, contracted `meta` table,
anchor classification on every extraction, ULID columns for portable
cross-cartridge identity, raw LLM signals in place of v0.1's hardcoded
`confidence`), plus three new capabilities: an append-only annotation ledger
with SHA-256 hash chain (SPEC-005), ULID-primary tables for `extractions` and
the reference tables (`extraction_sources`, `extraction_context_nodes`) with
the v0.2 integer-rowid carry-overs removed, and a renamed
`extraction_sources` table that no longer pretends to anchor "claims" only.
Any SQLite client can open a cartridge; the format is defined by its pragmas,
schema, meta rows, and a small set of structural invariants.

**Design intent (unchanged from v0.1 / v0.2):** a portable, inspectable,
read-optimized knowledge cartridge that can be distributed as a single file
and queried without Luna-specific tooling. v0.3 hardens this further by
making annotation governance integral (every post-build mutation is a ledger
event with a verifiable hash chain) and by retiring the dual-identity
integer + ULID schema that v0.2 carried as a transition state.

**What changed from v0.2, in one sentence:** v0.3 makes annotation
governance integral (append-only ledger with hash chain), removes the v0.2
integer-rowid carry-over columns in favor of ULID-primary tables, and renames
the `claim_sources` table to `extraction_sources` to reflect its true type
scope (per C-01 audit follow-up).

**Three change axes:**

1. **SPEC-005 — annotation ledger.** Two new tables (`annotation_ledger`,
   `annotation_actors`), four new indexes, two append-only triggers, four new
   `meta` keys, SHA-256 hash chain, genesis row at build time. Source of
   truth: [`01_Specs/implemented/SPEC-005_annotation-ledger.md`](../01_Specs/implemented/SPEC-005_annotation-ledger.md).
2. **SPEC-002 D5 — integer-rowid removal.** `extractions` becomes `WITHOUT
   ROWID` with `ulid TEXT PRIMARY KEY`. Reference table composite PKs swap
   from `(claim_id, node_id)` integers to `(extraction_ulid, node_ulid)`
   ULIDs. Per resolved Q1, `doc_nodes.id` survives only as the FTS5
   `content_rowid`; application identity is `doc_nodes.ulid`. Source of truth:
   [`01_Specs/implemented/SPEC-002_portable-ids.md`](../01_Specs/implemented/SPEC-002_portable-ids.md) Phase 2 / D5.
3. **C-01 — table rename.** `claim_sources` → `extraction_sources` (column
   `claim_id` → `extraction_ulid`). Reflects the audit-discovered reality that
   the table anchors any extraction type, not just claims. Source of truth:
   [`04_Audits/AUDIT_2026-05-22_meditations-v02.md`](../04_Audits/AUDIT_2026-05-22_meditations-v02.md) finding C-01.

SPEC-004 (multi-axis imprint weights, implemented 2026-05-22) does not change
the cartridge format — it specifies an application-layer composer contract
over the existing signals. Its sole interaction with v0.3 is that the
Contestation and Resonance axes become non-NULL because the ledger tables
now exist.

---

## File identification

- **Extension:** `.lun` (shared with the runtime matrix family — `application_id` discriminates).
- **Magic bytes:** SQLite 3 header (`SQLite format 3\0` at offset 0).
- **Application ID (cartridge family):** `0x4C554E43` (`'LUNC'`, decimal `1280659011`). **Required.** v0.3 readers refuse files where `application_id` does not match (`WrongFamilyError`). Unchanged from v0.2.
- **Sibling family for comparison:** runtime matrix uses `0x4C554E4D` (`'LUNM'`, decimal `1280659021`). Unchanged from v0.2.
- **User version:** `PRAGMA user_version = 3`. **Required.** Mirrored by `meta.format_version = '0.3'`. Reader trusts `user_version` as the binding source of truth; `meta.format_version` is human-readable documentation.

Identification flow for an external tool:

1. SQLite header at offset 0 — confirms it's a SQLite database.
2. `PRAGMA application_id` — `0x4C554E43` means cartridge family. Reject (or branch) on any other value.
3. `PRAGMA user_version` — MUST equal `3` for a v0.3 cartridge. v0.2 cartridges (`user_version = 2`) are legitimate members of the `.lun` cartridge family but MUST be migrated before a v0.3 reader will open them; the reader raises `UnsupportedVersionError(2)` with the migration command in the error text. v0.1 cartridges (`user_version = 1`) follow the same path, two migration hops away. Any other value raises `UnsupportedVersionError`.
4. (Cartridge family only) `meta.cartridge_kind` — must be in `SUPPORTED_CARTRIDGE_KINDS` (still `{'knowledge'}` in v0.3). Other values raise `UnsupportedCartridgeKindError`.

Step 2 alone is sufficient to fast-reject "wrong kind of `.lun`" without parsing
the schema. This is the property SPEC-006 was designed to provide; v0.3
inherits it unchanged.

**Family-split principle reaffirmed.** v0.3 does NOT change the `application_id`. The
`'LUNC'` family identity is stable across all minor versions; only `user_version`
moves. A cartridge family bump (`'LUNC'` → something else) would only happen on a
major version (v1.x → v2.x) — none planned.

---

## Schema

### `meta` — manifest

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

Required keys in v0.3:

| Key | Type | Notes |
|-----|------|-------|
| `format_version` | semver string | `'0.3'`; mirrors `PRAGMA user_version = 3`. |
| `cartridge_kind` | enum string | `'knowledge'` in v0.3. Validated against `SUPPORTED_CARTRIDGE_KINDS` at read time. |
| `source_filename` | string | Basename only (e.g., `Meditations.pdf`). |
| `source_format` | string | `pdf`, `markdown`, etc. |
| `source_hash` | hex string | SHA-256 of source file. |
| `created_at` | ISO 8601 | Build timestamp. |
| `word_count` | integer | Total words in source. |
| `node_count` | integer | Total rows in `doc_nodes`. Validated equal to `SELECT count(*) FROM doc_nodes`. |
| `embedding_model` | string | e.g., `all-MiniLM-L6-v2`. |
| `embedding_dim` | integer | e.g., `384`. |
| `logprob_base` | string | `'e'` (natural log). Reader refuses any other value. |
| `logprob_attribution` | enum string | `'response_level'` in v0.3 (unchanged from v0.2). |
| `ledger_hash_algorithm` | string | `'sha256'` in v0.3. Locked at genesis; immutable for the cartridge's lifetime. |
| `ledger_genesis_ulid` | ULID | The `annotation_ledger.ulid` of the genesis row. Shortcut for chain verification — avoids a `MIN(seq)` scan. |
| `ledger_head_seq` | integer | The current `MAX(annotation_ledger.seq)`. Updated on every ledger insert. Lets readers fast-check "is there new activity" without scanning. |
| `ledger_head_hash` | hex string | The `entry_hash` at `seq = ledger_head_seq`. The published chain root; this is what an external verifier compares against. |

Optional keys:

| Key | When set |
|-----|----------|
| `source_canonical_path` | Only when the builder is invoked with `--preserve-paths`. Full absolute path. Default behavior omits this key. |

Forbidden keys (v0.1 / v0.2 holdovers that must not appear in v0.3 cartridges):

| Key | Why forbidden |
|-----|---------------|
| `source_path` | v0.1 leaked the absolute builder-machine path. Replaced by `source_filename`. |
| `schema_version` | v0.1 integer key. Replaced by `format_version` semver string + `PRAGMA user_version`. |
| `deprecated_columns` | v0.2 key that named `doc_nodes.id,extractions.id` as scheduled-for-removal. v0.3 *executes* that removal for `extractions.id`; per resolved Q1, `doc_nodes.id` survives for FTS5 only. The key is still removed because its v0.2 warning job is done. |

**Title validation.** Same parser-artifact blocklist as v0.2 (SPEC-006). Unchanged in v0.3.

### `doc_nodes` — document tree

The shape of this table follows the resolved FTS5 reattachment decision —
see §"Open questions" Q1. **v0.3 uses Strategy A as the DDL**
(keep `id INTEGER PRIMARY KEY AUTOINCREMENT` solely as FTS5's
`content_rowid`; ULID is the canonical identifier for all cross-table FKs).
Strategy B (drop the integer entirely, rebuild FTS5) was measured and is
deferred for v0.3.

**Strategy A baseline DDL:**

```sql
CREATE TABLE doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- v0.3: retained ONLY as FTS5 content_rowid (see Q1)
    ulid TEXT NOT NULL                      -- v0.3: canonical identifier; all cross-table FKs target this
         CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*'),
    parent_ulid TEXT,                       -- was: parent_id INTEGER REFERENCES doc_nodes(id)
    type TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT,                           -- nullable; see v0.2 §"Content nullability"
    meta_json TEXT,                         -- per-source structured metadata
    FOREIGN KEY (parent_ulid) REFERENCES doc_nodes(ulid)
);

CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
CREATE INDEX idx_doc_nodes_parent ON doc_nodes(parent_ulid);
CREATE INDEX idx_doc_nodes_type ON doc_nodes(type);
```

**Why `id` survives in Strategy A.** FTS5 external content mode requires
`content_rowid` to be an INTEGER. v0.3's full removal of integer identity in
*application* logic doesn't change that SQLite requirement. Strategy A
accepts a vestigial integer column inside `doc_nodes` so FTS5 keeps working
without redesign; every cross-table FK in v0.3 references `doc_nodes.ulid`,
never `doc_nodes.id`. The integer is implementation detail, not identity.

**Why `parent_id` becomes `parent_ulid`.** Self-FK on the ULID column.
Recursive walks now produce portable identifiers. The shadow ULID in v0.2
was non-PK; v0.3 elevates it to the only identity that matters.

**Node types** (observed vocabulary): `document`, `section`, `paragraph`,
`sentence`, plus the rich Markdown/PDF set `list`, `list_item`, `figure`,
`table`, `row`, `cell`. Unchanged from v0.2.

**Hierarchy:** `document → section → paragraph → sentence` is the dominant chain. Sections may nest under other sections. Same as v0.2.

**Content nullability and `meta_json` shapes.** Unchanged from v0.2 §"Content nullability" and §"meta_json per-source shapes".

**ULID format.** Unchanged from v0.2: 26-char Crockford Base32 uppercase. First char in `[0-7]`. Generator described in `01_Specs/implemented/SPEC-002_portable-ids.md`.

### `extractions` — LLM artifacts

```sql
CREATE TABLE extractions (
    ulid TEXT PRIMARY KEY                   -- was: id INTEGER PRIMARY KEY AUTOINCREMENT in v0.2
         CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*'),
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    -- SPEC-001: anchor classification (unchanged from v0.2)
    anchor_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (anchor_status IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')),
    anchor_reason TEXT,
    -- SPEC-003: raw signals (unchanged from v0.2)
    llm_logprob_sum REAL,
    llm_token_count INTEGER,
    extraction_method TEXT NOT NULL DEFAULT 'llm'
        CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual'))
) WITHOUT ROWID;

CREATE INDEX idx_extractions_type ON extractions(type);
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
```

**What changed from v0.2.** Three structural changes; semantic columns
unchanged:

1. **`id INTEGER PRIMARY KEY AUTOINCREMENT` dropped.** ULID is the only identity.
2. **`ulid` is the PRIMARY KEY**, not a shadow column. CHECK constraint and length validation are inline on the column declaration.
3. **`WITHOUT ROWID`** per [SPEC-002 D3](../01_Specs/implemented/SPEC-002_portable-ids.md). The B-tree is keyed on ULID directly; no parallel integer rowid is maintained. The "random-order TEXT into a WITHOUT ROWID B-tree causes fragmentation" concern from the SPEC-002 alternatives section is mitigated by ULID's timestamp prefix: ULIDs generated in monotonic order produce a near-sequential B-tree insert pattern.

**No `confidence` column (SPEC-003, dropped in v0.2).** Carry-forward — `confidence` stays out in v0.3. Trust composition is application-layer per SPEC-004.

**Extraction types** (observed vocabulary): `claim`, `entity`, `summary`. Unchanged from v0.2.

**`anchor_status` taxonomy (SPEC-001) and LLM signal columns (SPEC-003).** Unchanged from v0.2 in both vocabulary and semantics.

**Entity anchoring still deferred.** Entity rows continue to carry `anchor_status = 'unknown'` legitimately in v0.3 (entity anchoring is a separate future spec, not in v0.3 scope).

### `extraction_sources` — extraction-to-source anchoring (renamed from `claim_sources`)

```sql
CREATE TABLE extraction_sources (
    extraction_ulid TEXT NOT NULL,            -- was: claim_id INTEGER in v0.2
    node_ulid TEXT NOT NULL,                  -- was: node_id INTEGER in v0.2
    -- SPEC-001 provenance columns (unchanged from v0.2)
    anchor_method TEXT NOT NULL DEFAULT 'auto'
        CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by TEXT,                         -- actor ULID; NULL allowed for 'auto'
    anchored_at INTEGER,                      -- unix ms; required for 'manual' / 'migrated'
    event_id TEXT,                            -- annotation_ledger.entry_hash of the event that produced this anchor; non-NULL for ledger-backed anchors
    PRIMARY KEY (extraction_ulid, node_ulid),
    FOREIGN KEY (extraction_ulid) REFERENCES extractions(ulid),
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;

CREATE INDEX idx_extraction_sources_node ON extraction_sources(node_ulid);
```

**Three changes from v0.2 `claim_sources`:**

1. **Table rename:** `claim_sources` → `extraction_sources`. Per audit finding C-01: despite the v0.2 column name `claim_id`, the table anchored both `claim` and `summary` extractions. The new name reflects reality.
2. **Column rename:** `claim_id` → `extraction_ulid`; `node_id` → `node_ulid`. Both columns are now ULIDs (no more integer + shadow-ULID duality). FKs target `extractions(ulid)` and `doc_nodes(ulid)` directly.
3. **`event_id` now populated for ledger-backed anchors.** In v0.2 this was a forward-ref to SPEC-005 and always NULL. In v0.3, ambassador upgrades (per SPEC-005's behavioral changes section) insert an `extraction_sources` row *and* a `claim_anchored` ledger event in the same transaction; `event_id` carries the resulting ledger event's `entry_hash` for verifiable provenance.

**`WITHOUT ROWID`** because the composite ULID PK is the natural key; no benefit to a parallel rowid. Same justification as `extractions`.

**Design intent (unchanged):** many-to-many bridge from extractions to their source sentences/paragraphs. Builder-produced anchors are currently 1:1; the schema supports multi-source for future use.

**Provenance columns (SPEC-001).** `anchor_method` distinguishes builder-produced anchors from community-upgraded anchors from migration-time anchors. Non-`auto` methods require both `anchored_by` and `anchored_at` populated; enforced in `validate_anchors()`.

**Ambassador upgrade flow** (now ledger-coupled, per SPEC-005):

1. `insert_ledger_event(conn, event_type='claim_anchored', actor_id=..., actor_role='ambassador', target_kind='extractions', target_ulid=extraction_ulid, payload=...)` — returns `(seq, entry_hash)`.
2. `INSERT INTO extraction_sources (extraction_ulid, node_ulid, anchor_method, anchored_by, anchored_at, event_id) VALUES (?, ?, 'manual', actor_id, anchored_at_ms, entry_hash)`
3. `UPDATE extractions SET anchor_status = 'anchored' WHERE ulid = ?`

All three operations run in a single transaction. `event_id` ties the
anchor row to the ledger event that produced it.

### `extraction_context_nodes` — soft anchoring for synthesis extractions (renamed from `claim_context_nodes`)

```sql
CREATE TABLE extraction_context_nodes (
    extraction_ulid TEXT NOT NULL,            -- was: claim_id INTEGER
    node_ulid TEXT NOT NULL,                  -- was: node_id INTEGER
    relevance REAL NOT NULL,                  -- 0.0 - 1.0 semantic similarity
    PRIMARY KEY (extraction_ulid, node_ulid),
    FOREIGN KEY (extraction_ulid) REFERENCES extractions(ulid),
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
) WITHOUT ROWID;

CREATE INDEX idx_extraction_context_extraction ON extraction_context_nodes(extraction_ulid);
CREATE INDEX idx_extraction_context_node ON extraction_context_nodes(node_ulid);
```

**Renaming rationale.** Symmetry with `extraction_sources`. The v0.2
audit only named `claim_sources` in finding C-01, but the same naming
mismatch applies here — synthesis is an `extraction` operation, not a
`claim`-specific one. Q2 resolved this in favor of the symmetric rename:
v0.3 uses `extraction_sources` and `extraction_context_nodes`, with no
compatibility alias for the old table names.

**Purpose (SPEC-001).** For extractions classified as `synthesized`, this
table holds the set of source nodes the synthesis was drawn from. May also
hold hint rows for `match_failed` or `filtered` extractions. Unchanged from
v0.2.

**Synthesis invariants** (enforced in `validate_anchors()`, unchanged from v0.2):

- A synthesis extraction must have ≥2 rows here.
- Those rows must reference `doc_nodes` with ≥2 distinct `parent_ulid` values (distinct lineage).

### `embeddings` — vector blobs

```sql
CREATE TABLE embeddings (
    node_ulid TEXT NOT NULL,                  -- was: node_id INTEGER in v0.2
    level TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (node_ulid, level),
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;

CREATE INDEX idx_embeddings_level ON embeddings(level);
```

**One change from v0.2:** `node_id INTEGER` → `node_ulid TEXT`. Composite PK swap. `WITHOUT ROWID` for the same reason as the other ULID-PK tables.

**Levels, vector format, consumer contract:** unchanged from v0.2. Vector blobs are still raw float32 bytes; readers MUST validate `length(vector) == embedding_dim * 4`.

**Coverage policy (S-01 carry-forward from v0.2; classified 2026-07-23):** Embedding coverage is best-effort, not total. Builders MAY skip `doc_nodes` rows with NULL content, sub-threshold-length content, or other policy-defined exclusions — including **section** rows whose recursive subtree yields no non-empty `sentence` / `list_item` / `cell` text (Meditations post-M-01 reference: **149/166** section embeddings). Readers MUST NOT assume every `doc_nodes` row has a corresponding `embeddings` row; LEFT JOIN, not INNER JOIN. No v0.3 reader invariant requires full coverage. Unchanged from the v0.2 §"Coverage policy" clarification added 2026-05-22.

### `nodes_fts` — full-text search

The DDL follows the resolved FTS5 reattachment strategy. Strategy A is the
v0.3 shape; Strategy B remains documented as a measured alternative deferred
for a future format revision.

**Strategy A — keep `doc_nodes.id INTEGER` as FTS5's content_rowid** (resolved v0.3 shape):

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'                        -- unchanged from v0.2
);

CREATE TRIGGER nodes_fts_ai AFTER INSERT ON doc_nodes BEGIN
    INSERT INTO nodes_fts(rowid, content)
    VALUES (new.id, COALESCE(new.content, ''));
END;
CREATE TRIGGER nodes_fts_ad AFTER DELETE ON doc_nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
END;
CREATE TRIGGER nodes_fts_au AFTER UPDATE ON doc_nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
    VALUES ('delete', old.id, COALESCE(old.content, ''));
    INSERT INTO nodes_fts(rowid, content)
    VALUES (new.id, COALESCE(new.content, ''));
END;
```

Under Strategy A, FTS5 is byte-for-byte identical to v0.2. The `doc_nodes.id`
integer survives solely to satisfy FTS5's INTEGER `content_rowid` requirement;
application code never references it.

**Strategy B — drop `doc_nodes.id`; rebuild FTS5** (measured alternative;
deferred for v0.3). Two sub-variants:

- *B1: rowid-mapping table.* Keep `doc_nodes` ULID-only; introduce a
  `_fts_rowid_map(rowid INTEGER PRIMARY KEY AUTOINCREMENT, node_ulid TEXT
  UNIQUE)` table; a contentful FTS5 table stores rows under the mapping
  table's rowid and queries join back to `doc_nodes.ulid`.
- *B2: contentless FTS5.* Drop external-content mode; FTS5 stores index data
  without original row content. Queries join through the mapping table and
  snippets require a manual fallback from `doc_nodes.content`.

Both B-variants were prototyped against the Meditations corpus on 2026-05-22.
B1 failed the 10% threshold on storage, build time, and query p95; B2 failed
the storage threshold and required manual snippets. See §"Open questions" Q1.

**Shadow tables** (created by FTS5, not directly queried):
`nodes_fts_data`, `nodes_fts_idx`, `nodes_fts_docsize`, `nodes_fts_config`. Unchanged.

### `sqlite_sequence` — retained for AUTOINCREMENT tables

The v0.2 `sqlite_sequence` table remains present in v0.3 cartridges, but only
because Strategy A keeps `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` for
FTS5 and SPEC-005 adds `annotation_ledger.seq INTEGER PRIMARY KEY
AUTOINCREMENT`. v0.3 drops `extractions.id` (no more AUTOINCREMENT there), so
`sqlite_sequence` MUST list `doc_nodes` and `annotation_ledger`. Any
`extractions` entry in `sqlite_sequence` indicates an incomplete v0.3
migration.

Strategy B would remove `sqlite_sequence` entirely, but B is deferred for
v0.3 and is not the accepted cartridge shape.

### `nexus_refs` — cross-cartridge promotion

```sql
CREATE TABLE nexus_refs (
    local_node_id TEXT NOT NULL,              -- ULID of the local doc_nodes / extractions row
    nexus_node_id TEXT NOT NULL,              -- ULID in the Nexus (runtime matrix family)
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (local_node_id, node_type)
);

CREATE INDEX idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
```

**Status change from v0.2.** In v0.2, this table was a forward-compat
placeholder, created empty by the builder for SPEC-005. In v0.3, it is
**active** — the ledger's `cartridge_imported` events make cross-cartridge
promotion observable, and `nexus_refs` is the destination table for
local-to-Nexus pairings established by `promote_to_nexus()`.

**DDL unchanged from v0.2.** TEXT identifiers throughout; the v0.2 placeholder
DDL was already shaped correctly for the v0.3 active use.

### `sketches` — bloom-filter shelf pre-filter (SPEC-007)

```sql
CREATE TABLE sketches (
    sketch_kind     TEXT NOT NULL,           -- 'extraction_ulid' | 'node_ulid' | 'entity_surface' | 'fts_term'
    sketch_version  INTEGER NOT NULL,        -- per-kind schema version; v0.3 always 1
    hash_family     TEXT NOT NULL,           -- v0.3 only allows 'murmur3_x64_128'
    num_hashes      INTEGER NOT NULL,        -- k (1..32)
    num_bits        INTEGER NOT NULL,        -- m (positive multiple of 8)
    num_inserted    INTEGER NOT NULL,        -- n (distinct items after § 7.1.1 normalization)
    seed            INTEGER NOT NULL,        -- uint64; 0 for deterministic builds
    bitset          BLOB NOT NULL,           -- ceil(num_bits/8) bytes; LSB-first within byte
    built_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    builder_version TEXT,                    -- e.g. 'luna-builder/0.3.0'
    notes           TEXT,
    PRIMARY KEY (sketch_kind, sketch_version)
);

CREATE INDEX idx_sketches_kind ON sketches(sketch_kind);
```

**Status (added 2026-05-23).** Additive within v0.3 per [SPEC-007 § 7.5](../01_Specs/implemented/SPEC-007_cartridge-sketches.md). Builders SHOULD populate the four sketch kinds (extraction ULIDs, node ULIDs, entity surface forms, FTS5 vocabulary) after extractions / nodes / FTS index are stable and before `VACUUM`; readers MUST tolerate the table's absence (v0.3 cartridges built before this amendment have no `sketches` table at all).

**Meta keys coupled to this table** (REQUIRED iff sketches are populated):
- `meta.sketches_present` — comma-separated, alphabetized list of populated `sketch_kind` values (e.g. `entity_surface,extraction_ulid,fts_term,node_ulid`). Absence ⇒ no sketches.
- `meta.fts_tokenizer_config` — REQUIRED iff `fts_term` is in `meta.sketches_present`. Names the FTS5 tokenizer the sketch was built against (current value: `'unicode61'`). Consumers MUST tokenize query terms with the same tokenizer before computing membership.

**Validation.** `lun fsck --sketches` runs `validate_sketch_row()` against every row (bitset length, hash family, num_hashes range, num_bits multiplicity, num_inserted sanity, sketch_version known for kind) and `validate_meta_sketches_present()` (declared kinds match table; `fts_tokenizer_config` present iff `fts_term` is). Failure of any sketch makes that sketch unusable but does not fail the cartridge open. See [SPEC-007 § Validation rules](../01_Specs/implemented/SPEC-007_cartridge-sketches.md) for the full row-level invariants.

**Sizing baseline** (per [SPEC-007 § 7.4](../01_Specs/implemented/SPEC-007_cartridge-sketches.md) + 2026-05-23 retrofit against the Meditations v0.3 cartridge): 4 sketches total ≈ 14 KB on a 2.5 MB cartridge ≈ 0.56% storage overhead.

### `annotation_ledger` — append-only governance ledger (NEW)

```sql
CREATE TABLE annotation_ledger (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotone sequence within this cartridge
    ulid        TEXT NOT NULL UNIQUE                -- portable cross-cartridge identity
                CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*'),
    entry_ts    INTEGER NOT NULL,                   -- unix ms; monotone non-decreasing with seq
    event_type  TEXT NOT NULL                       -- controlled vocabulary
                CHECK (event_type IN (
                    'claim_anchored',
                    'claim_disputed',
                    'claim_filtered',
                    'claim_reconciled',
                    'summary_overridden',
                    'cartridge_reviewed',
                    'cartridge_imported',
                    'meta'
                )),
    actor_id    TEXT NOT NULL,                      -- ULID identifying the actor
    actor_role  TEXT NOT NULL                       -- role at the time of the event
                CHECK (actor_role IN ('owner', 'ambassador', 'elder', 'oracle', 'system')),
    target_kind           TEXT,                     -- which table the event acts on; NULL for cartridge-wide events
    target_ulid           TEXT,                     -- which row's ULID; NULL for cartridge-wide events
    target_cartridge_ulid TEXT,                     -- which cartridge the target lives in; NULL = current; only valid for cross-cartridge event types
    payload               TEXT NOT NULL,            -- canonical JSON; per-event-type schema in SPEC-005_payload-schemas
    prev_hash             TEXT,                     -- NULL only for the genesis row
    entry_hash            TEXT NOT NULL UNIQUE      -- SHA-256 of canonical 10-field serialization
                          CHECK (length(entry_hash) = 64),
    CHECK ((target_kind IS NULL) = (target_ulid IS NULL)),
    CHECK (target_cartridge_ulid IS NULL
           OR event_type IN ('cartridge_imported', 'cartridge_reviewed')),
    CHECK (prev_hash IS NULL OR length(prev_hash) = 64),
    CHECK (actor_role != 'system' OR event_type = 'meta')
);

CREATE INDEX idx_ledger_target ON annotation_ledger(target_kind, target_ulid);
CREATE INDEX idx_ledger_actor ON annotation_ledger(actor_id);
CREATE INDEX idx_ledger_type ON annotation_ledger(event_type);
CREATE INDEX idx_ledger_ts ON annotation_ledger(entry_ts);
```

**Append-only enforcement (soft covenant).** Two triggers convert any UPDATE
or DELETE into `SQLITE_CONSTRAINT` aborts:

```sql
CREATE TRIGGER annotation_ledger_no_update
BEFORE UPDATE ON annotation_ledger
BEGIN
    SELECT RAISE(ABORT, 'annotation_ledger is append-only: updates forbidden');
END;

CREATE TRIGGER annotation_ledger_no_delete
BEFORE DELETE ON annotation_ledger
BEGIN
    SELECT RAISE(ABORT, 'annotation_ledger is append-only: deletes forbidden');
END;
```

These are a **soft covenant**: an admin with `sqlite3` CLI access can still
bypass them via `PRAGMA writable_schema = ON`, `DROP TRIGGER`, or schema
surgery. The covenant raises the cost and visibility of tampering; it does
not make tampering impossible. SPEC-005 documents this honestly under its
"Soft-covenant honesty" subsection.

**Hash chain.** Each row's `entry_hash` is the SHA-256 of a canonical
10-field serialization:

```
canonical = "|".join([
    str(seq),
    str(entry_ts),
    event_type,
    actor_id,
    actor_role,
    target_kind or "",
    target_ulid or "",
    target_cartridge_ulid or "",
    payload,                   # exact stored bytes
    prev_hash or ""
])
entry_hash = sha256(canonical.encode("utf-8")).hexdigest()
```

The pipe (`|`) is the field separator; NULL fields serialize as empty
string. The `payload` field is taken **exactly as stored** — no
re-serialization at hash time — so on-disk bytes and hash-input bytes are
identical.

**Payload serialization mandate (normative).** Writers MUST produce payload
bytes via canonical JSON: sorted keys, compact separators, UTF-8, no
ASCII-escaping. Python reference form:

```python
payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

Two independent correct writers MUST produce byte-identical payloads for
the same logical event; otherwise the chain fragments by writer
implementation. `validate_payloads()` re-parses each payload and re-serializes
with canonical settings; any byte mismatch is a `BuildError`.

**Hash algorithm: SHA-256** (64 hex chars). Locked at genesis via
`meta.ledger_hash_algorithm = 'sha256'`. Immutable for the cartridge's
lifetime. If SHA-256 is ever broken, the response is at the format-version
layer: v0.4+ cartridges adopt a new algorithm; existing v0.3 cartridges
retain SHA-256 with documented degradation.

**Event types (8) — full payload contracts.** See
[`01_Specs/implemented/SPEC-005_payload-schemas.md`](../01_Specs/implemented/SPEC-005_payload-schemas.md) for the per-event-type required keys, optional keys,
consistency rules, and locked enum vocabularies (severity levels,
resolution outcomes, `target_field`, `relationship`, etc.). Summary:

| `event_type` | Actor roles | Required keys | Purpose |
|---|---|---|---|
| `claim_anchored` | ambassador, elder | `claim_id`, `node_id`, `reason` | Anchor a previously-unanchored extraction. |
| `claim_disputed` | elder | `claim_id`, `reason` | Flag an extraction for reconciliation. |
| `claim_filtered` | ambassador, elder | `claim_id`, `filter_reason` | Post-build filtering. |
| `claim_reconciled` | elder | `dispute_event_hash`, `resolution`, `rationale` | Resolve a prior dispute. |
| `summary_overridden` | elder | `target_field`, `prior_value`, `new_value`, `rationale` | Replace a builder-generated summary. |
| `cartridge_reviewed` | oracle | `decision`, `summary` | Cartridge-level review. |
| `cartridge_imported` | system, owner | `source_application_id`, `source_source_hash`, `source_user_version`, `relationship` | Record cartridge attach/merge/migration. |
| `meta` | system | (genesis: `application_id`, `user_version`, `source_hash`, `format_version`; otherwise open) | Chain-internal events. |

**System actor sentinel.** The system actor's identity is a fixed sentinel
ULID: `SYSTEM_ACTOR_ULID = '00000000000000000000000000'` (26 zero
characters). The sentinel passes the standard ULID format CHECK (`0` is in
the Crockford alphabet) without exemption. Real ULIDs cannot collide: the
first 10 characters encode a unix-ms timestamp, so all-zeros encodes
`1970-01-01T00:00:00Z`, which no real build produces.

**Genesis row.** Inserted at cartridge build time with `event_type = 'meta'`,
`actor_role = 'system'`, `prev_hash = NULL`, and a payload describing the
cartridge identity at genesis (`application_id`, `user_version`,
`source_hash`, `format_version`). Every subsequent event chains back via
`prev_hash`.

### `annotation_actors` — actor registry (NEW)

```sql
CREATE TABLE annotation_actors (
    actor_id     TEXT PRIMARY KEY                   -- ULID
                 CHECK (length(actor_id) = 26 AND actor_id GLOB '[0-9A-HJKMNP-TV-Z]*'),
    display_name TEXT NOT NULL,
    first_seen   INTEGER NOT NULL,                  -- unix ms; first ledger event's entry_ts
    last_seen    INTEGER NOT NULL,                  -- unix ms; most recent ledger event's entry_ts
    primary_role TEXT NOT NULL                      -- most-common role across this actor's events
                 CHECK (primary_role IN ('owner', 'ambassador', 'elder', 'oracle', 'system')),
    public_key   TEXT                               -- optional Ed25519 public key; NULL allowed
) WITHOUT ROWID;
```

**Purpose.** Separate from `annotation_ledger` so actor metadata can be
inspected without scanning every event, and so a key rotation or
display-name change is a single-row UPDATE rather than a ledger event.
Updates to `annotation_actors` are intentional and allowed (this table is
not append-only).

**System actor row.** Registered once at genesis with `display_name =
'system'`, `primary_role = 'system'`, `public_key = NULL`,
`first_seen = last_seen = (genesis entry_ts)`. The sentinel actor ULID is
`'00000000000000000000000000'` (see above).

---

## Build pipeline

```
source file (.md/.pdf)
    ↓ parse
doc_nodes (hierarchical, ULIDs assigned at insert; parent_ulid self-FK)
    ↓ optional LLM pass (Haiku, response-level logprobs captured)
extractions + extraction_sources + extraction_context_nodes (anchor_status classified; ULID PKs)
    ↓ optional embedding pass (MiniLM)
embeddings (node_ulid FK)
    ↓ validate (validate_extractions, validate_ulids, validate_anchors, validate_ledger)
    ↓ insert genesis ledger row (event_type='meta', actor_role='system', system actor sentinel)
    ↓ initialize meta.ledger_* keys; register system actor in annotation_actors
    ↓ finalize (optimize, wal_checkpoint, journal_mode=DELETE, VACUUM)
.lun file (SQLite, v0.3 contract)
```

CLI (unchanged from v0.2):
```
python -m luna.cartridge.builder input.md [output.lun] [--no-extract] [--no-embed] [--preserve-paths]
```

**Source-format expansion note.** Later builders may accept additional source
formats without changing the `.lun` schema when they emit the existing
`doc_nodes` vocabulary. CSV and XLSX are represented as
`document → section → table → row → cell`; spreadsheet formulas, validations,
named ranges, merged ranges, and source coordinates live in `meta_json`.

### Pragma stack at build creation

Set as the first DDL after connection open, before any data inserts:

```sql
PRAGMA application_id = 0x4C554E43;  -- 'LUNC'
PRAGMA user_version = 3;             -- v0.3
PRAGMA journal_mode = WAL;            -- during build; switched to DELETE at finalize
PRAGMA busy_timeout = 15000;
PRAGMA foreign_keys = ON;
```

Only difference from v0.2: `user_version = 3` instead of `2`.

### Genesis ledger row insertion

After body finalize but before the SPEC-006 finalize pragma stack:

```python
genesis_payload = json.dumps({
    "application_id": "0x4C554E43",
    "user_version": 3,
    "source_hash": meta["source_hash"],
    "format_version": meta["format_version"],
}, sort_keys=True, separators=(",", ":"))

(genesis_seq, genesis_hash) = insert_ledger_event(
    conn,
    event_type="meta",
    actor_id=SYSTEM_ACTOR_ULID,
    actor_role="system",
    target_kind=None,
    target_ulid=None,
    payload=genesis_payload,
)
# Initialize meta head pointers
conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ledger_hash_algorithm', 'sha256')")
conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ledger_genesis_ulid', ?)",
             (genesis_ulid,))
conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ledger_head_seq', ?)",
             (str(genesis_seq),))
conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ledger_head_hash', ?)",
             (genesis_hash,))
# Register the system actor
conn.execute("""INSERT INTO annotation_actors
                (actor_id, display_name, first_seen, last_seen, primary_role)
                VALUES (?, 'system', ?, ?, 'system')""",
             (SYSTEM_ACTOR_ULID, genesis_ts_ms, genesis_ts_ms))
```

### Finalize stack (unchanged from v0.2)

```sql
PRAGMA optimize;                       -- ANALYZE-equivalent
PRAGMA wal_checkpoint(TRUNCATE);       -- fold WAL frames into main file
PRAGMA journal_mode = DELETE;          -- shipping mode: no sidecar files
VACUUM;                                -- reclaim space, defragment
```

### Validators (run before finalize; build fails on any error)

Centralized in `src/luna/cartridge/validation.py`:

- `validate_extractions(conn)` — SPEC-003 invariants. Carried forward unchanged.
- `validate_ulids(conn)` — SPEC-002 invariants. Now applies to additional tables (`annotation_ledger.ulid`, `annotation_actors.actor_id`).
- `validate_anchors(conn)` — SPEC-001 invariants. Updated to use the new table names (`extraction_sources`, `extraction_context_nodes`).
- `validate_ledger(conn)` — **new in v0.3.** 7-step pipeline per SPEC-005 §"Validation rules":
  1. Both append-only triggers exist with the expected DDL (whitespace-normalized comparison).
  2. `meta.ledger_head_seq` matches `MAX(seq)` in `annotation_ledger`.
  3. `meta.ledger_head_hash` matches the `entry_hash` at `MAX(seq)`.
  4. Each row's canonical 10-field serialization re-derives correctly.
  5. Each row's SHA-256 hash recomputes to the stored `entry_hash`.
  6. Chain continuity: `prev_hash[N] == entry_hash[N-1]` for all N > 1; `prev_hash[1] IS NULL`.
  7. `entry_ts` is monotone non-decreasing with `seq`.
- `validate_payloads(conn)` — **new in v0.3.** Walks the ledger, parses each payload, and checks it against the per-event-type schema in SPEC-005_payload-schemas. Called as Step 7 of `validate_ledger()` (after chain integrity walk).
- `validate_cartridge_open(conn)` — read-time gate. Updated to require `user_version == 3`.

---

## Read path

### Open contract

```python
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
conn.execute("PRAGMA query_only = 1")
conn.execute("PRAGMA mmap_size = 268435456")    # 256MB
conn.execute("PRAGMA cache_size = -8000")       # 8MB
validate_cartridge_open(conn)
# Optional fast-open ledger check (O(1)):
fast_open_ledger_check(conn)    # verifies trigger DDL + head pointer match
```

`validate_cartridge_open()` enforces:

1. `application_id == 0x4C554E43` (`WrongFamilyError` otherwise).
2. `user_version == 3` (`UnsupportedVersionError` otherwise; v0.2 cartridges raise `UnsupportedVersionError(2)` with the migration command in the error text).
3. `meta.cartridge_kind` in `SUPPORTED_CARTRIDGE_KINDS = {'knowledge'}`.
4. `meta.format_version` parses to an integer matching `user_version`; on mismatch, log warning and trust `user_version`.
5. `meta.logprob_base = 'e'` and `meta.logprob_attribution = 'response_level'`; reject otherwise.
6. `meta.ledger_hash_algorithm = 'sha256'`; reject otherwise (until a future spec defines an alternative algorithm).

`fast_open_ledger_check()` enforces (optional but recommended):

7. Both append-only triggers exist on `annotation_ledger`.
8. `meta.ledger_head_seq` matches `(SELECT MAX(seq) FROM annotation_ledger)`.
9. `meta.ledger_head_hash` matches `(SELECT entry_hash FROM annotation_ledger WHERE seq = MAX(seq))`.

Steps 7-9 are O(1) regardless of ledger size — they touch the meta key-value
table plus a single max-seq lookup. Full chain verification is opt-in (via
`lun fsck --ledger`) because chains grow over time and full verification is
O(n).

### Display invariants for `anchor_status`

Unchanged from v0.2:

- **`anchored`** — display with single source quotation. Standard grounding.
- **`synthesized`** — display with "synthesized from N sources" and surface the context-node set on request.
- **`match_failed`** — display with a "⚠ unanchored" badge.
- **`filtered`** — exclude from normal reading by default. Audit surface exposes a toggle.
- **`unknown`** — should not occur in v0.3 cartridges for `claim` or `summary` extractions. Entities legitimately carry `unknown`.

### Trust signal composition

The cartridge carries raw signals plus the ledger event stream. Composition
into a trust score is application-level per [SPEC-004 (implemented, 2026-05-22)](../01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md).
In v0.3, all four SPEC-004 axes (Authority, Contestation, Temporal,
Resonance) are non-NULL for `claim` and `summary` extractions — the ledger
tables exist, and SPEC-005 events feed the Contestation and Resonance axes.
The v0.2-era caveat "Contestation and Resonance return NULL because no
SPEC-005 ledger exists yet" is resolved by v0.3.

Inputs available to a composer:

- Categorical: `anchor_status`, `anchor_method`, `extraction_method`
- Continuous (at LLM-call scope): `llm_logprob_sum`, `llm_token_count`
- Optional: `extraction_context_nodes.relevance` for synthesis extractions
- **New in v0.3:** ledger event stream via `annotation_ledger` joined to
  `annotation_actors`; per-extraction event history via `target_ulid` index.

### Ledger query patterns

Helper interfaces a v0.3 reader should expose (per SPEC-005 behavioral
changes section):

- `ledger_events(target_ulid)` — full event history for a single extraction
  or doc_node, ordered by `seq`. Uses `idx_ledger_target`.
- `resolve_source_ref(handle, ref, include_provenance=True)` —
  the v0.2 `resolve_source_ref()` augmented to optionally include each
  `extraction_sources.event_id` event's full row (joined from
  `annotation_ledger` by `entry_hash`).
- `latest_event_ts(target_ulid)` — used by SPEC-004's Temporal axis.

---

## Migration from v0.2

Migration is atomic, transactional, and re-runnable. Implemented in
`src/luna/cartridge/migrate.py` (extended from the v1→v2 tool):

```
python -m luna.cartridge.migrate <path>
python -m luna.cartridge.migrate --dry-run <path>     # clones to in-memory DB, validates, discards
python -m luna.cartridge.migrate --strict <path>      # fails if any orphan would receive 'migration_unclassified' fallback
```

**Single transaction; ordered steps:**

1. **Rename reference tables.**
   ```sql
   ALTER TABLE claim_sources RENAME TO extraction_sources;
   ALTER TABLE claim_context_nodes RENAME TO extraction_context_nodes;
   ALTER TABLE extraction_sources RENAME COLUMN claim_id TO extraction_id_int;
   ALTER TABLE extraction_context_nodes RENAME COLUMN claim_id TO extraction_id_int;
   ```
   Temporary `_int` suffix is used to disambiguate; the integer column is
   dropped in step 5.

2. **Create ledger infrastructure.** Create `annotation_ledger`,
   `annotation_actors`, the 4 ledger indexes, the 2 append-only triggers.
   Order matters: table → indexes → triggers (per SPEC-005 "Trigger creation
   order" guidance; the table must exist before triggers reference it). DDL
   is O(1) regardless of cartridge size.

3. **Insert genesis row.** `event_type = 'meta'`, `actor_role = 'system'`,
   `prev_hash = NULL`, payload describing the cartridge identity at
   migration time:
   ```json
   {"application_id": "0x4C554E43", "user_version": 3,
    "source_hash": "<existing meta.source_hash>", "format_version": "0.3"}
   ```

4. **Insert migration event.** Second meta event recording the migration
   itself (per SPEC-005 §"Migration path"):
   ```json
   {"action": "migrated_v2_to_v3", "from_version": 2, "to_version": 3,
    "source_hash": "<existing meta.source_hash>",
    "migrated_at": "<unix-ms timestamp>"}
   ```
   `event_type = 'meta'`, `actor_role = 'system'`. The system actor is
   reserved for chain meta-events, so this cannot be represented as
   `cartridge_imported` without violating the ledger CHECK constraint.

5. **Rewrite `extractions` as `WITHOUT ROWID` with ULID PK.** This is the
   table-rewrite footgun from SPEC-005 §"Trigger interaction with other DDL"
   — but only `extractions` is rewritten here, not `annotation_ledger`, so
   the ledger triggers stay intact. Procedure:
   ```sql
   CREATE TABLE _extractions_new ( ... ULID PK schema ... ) WITHOUT ROWID;
   INSERT INTO _extractions_new SELECT ulid, type, content, anchor_status,
     anchor_reason, llm_logprob_sum, llm_token_count, extraction_method
     FROM extractions;
   DROP TABLE extractions;
   ALTER TABLE _extractions_new RENAME TO extractions;
   CREATE INDEX idx_extractions_type ON extractions(type);
   CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
   ```
   Then backfill the ULID columns on the reference tables:
   ```sql
   UPDATE extraction_sources SET extraction_ulid =
     (SELECT ulid FROM extractions WHERE id = extraction_sources.extraction_id_int);
   UPDATE extraction_sources SET node_ulid =
     (SELECT ulid FROM doc_nodes WHERE id = extraction_sources.node_id);
   -- and likewise for extraction_context_nodes
   ```
   Rewrite the reference tables with the new ULID-only PK:
   ```sql
   CREATE TABLE _extraction_sources_new ( ... ULID composite PK schema ... ) WITHOUT ROWID;
   INSERT INTO _extraction_sources_new SELECT extraction_ulid, node_ulid,
     anchor_method, anchored_by, anchored_at, event_id FROM extraction_sources;
   DROP TABLE extraction_sources;
   ALTER TABLE _extraction_sources_new RENAME TO extraction_sources;
   CREATE INDEX idx_extraction_sources_node ON extraction_sources(node_ulid);
   -- and likewise for extraction_context_nodes and embeddings
   ```

6. **FTS5 reattachment.** Q1 resolved in favor of Strategy A. The FTS5 virtual
   table and its triggers stay attached to `doc_nodes.id`; no work needed
   beyond verifying the triggers survived the reference-table rewrites (they
   did — FTS5 triggers are on `doc_nodes`, not on the rewritten tables).

7. **Verify `sqlite_sequence`.** Strategy A leaves `doc_nodes.id`
   AUTOINCREMENT in place, and `annotation_ledger.seq` is also
   AUTOINCREMENT, so `sqlite_sequence` survives. It MUST list `doc_nodes` and
   `annotation_ledger`; `extractions` must no longer appear.

8. **Update `meta`.**
   ```sql
   UPDATE meta SET value = '0.3' WHERE key = 'format_version';
   DELETE FROM meta WHERE key = 'deprecated_columns';
   INSERT OR REPLACE INTO meta VALUES ('ledger_hash_algorithm', 'sha256');
   INSERT OR REPLACE INTO meta VALUES ('ledger_genesis_ulid', '<genesis ulid>');
   INSERT OR REPLACE INTO meta VALUES ('ledger_head_seq', '2');
   INSERT OR REPLACE INTO meta VALUES ('ledger_head_hash', '<seq=2 entry_hash>');
   ```

9. **Bump pragma.** `PRAGMA user_version = 3`. Must be the last write before finalize.

10. **Finalize stack.** Same as build-time: `optimize → wal_checkpoint(TRUNCATE) → journal_mode=DELETE → VACUUM`.

The migration is **forward-compatible**: v0.3 readers handle both v0.2 and
v0.3 cartridges with appropriate degradation (v0.2 cartridges report "no
ledger" rather than failing). The v0.3 → v0.2 downgrade path is not
defined — once the ledger exists, removing it would discard provenance.

**Cross-table invariants** spanning multiple specs are enforced at the
application layer in `validate_ledger()` and `validate_anchors()`, not via
SQLite triggers or `ALTER TABLE ADD CONSTRAINT` (same pattern as v0.2).

---

## Known v0.3 limitations

Status as of 2026-05-22 (Shipping state; engine implementation landed in
commit `407122f`; Meditations v0.3 audit passed):

1. **Token-span logprob attribution still not available** — all extractions from one LLM call still share identical `llm_logprob_sum` / `llm_token_count`. Per-row discrimination would require token-span attribution. Carried forward from v0.2 §"Known v0.2 limitations" item 8. [Future spec]
2. **Backend logprobs not fully exposed** — `HaikuResult.usage` fields still not surfaced; logprob columns NULL on most builds. The paired-NULL invariant continues to hold. Carried forward from v0.2 item 9. [v0.4 backend-side improvement]
3. **Entity anchoring still deferred** — entities continue to carry `anchor_status = 'unknown'` legitimately. Only `claim` and `summary` extractions are subject to the anchor taxonomy. Carried forward from v0.2 item 10. [Future spec]
4. **Filtered classification not auto-derived** — Phase 5 migration's `migration_unclassified` fallback applies to v0.2→v0.3 migrations of cartridges that originated as v0.1. Real classification (multi-lineage similarity, section-type heuristics) is deferred. Carried forward from v0.2 item 11. [Future spec: orphan semantic classification — see SPEC-004 §4.5]
5. **`magic.txt` upstream registration courtesy** — still not submitted. Carried forward from v0.2 item 14.
6. **No cartridge-level ULID in `meta`** — SPEC-005's `cartridge_imported` event includes an optional `source_cartridge_ulid` payload key, but v0.3 doesn't define a `meta.cartridge_ulid` to fill it. Cross-cartridge identity is currently established via `(application_id, source_hash)` rather than a single ULID. Adding a cartridge-level ULID is a candidate for v0.4. [Future spec]

Items **closed by v0.3** (removed from the v0.2 list):

- ~~v0.2 item 1: No annotations~~ → SPEC-005 annotations live.
- ~~v0.2 item 4: No ledger~~ → SPEC-005 annotation_ledger live.
- ~~v0.2 item 5: No integrity chain~~ → SHA-256 chain live.
- ~~v0.2 item 6: Single-axis trust composition~~ → SPEC-004 multi-axis is the implemented contract; reader v0.3.1 ships reference composer `lun.format/reference-v1@1.0.0`; ledger feeds the full 4-axis composition.
- ~~v0.2 item 7: Integer rowids still present~~ → Removed (subject to Strategy A's `doc_nodes.id` FTS5 carve-out; see Q1).

### Carried-forward Phase 5 deferrals (cartridge-build specific)

8. **Lansing 9.5%-baseline measurement undefined.** Same as v0.2 item 12. Resolution still requires recovering the source PDF or pre-processing the reconstruction. Not blocked by v0.3.
9. **`\x0c` form-feed artifacts** in cartridge text content. Same as v0.2 item 13. Verified absent in Meditations; status depends on the source PDF. Not blocked by v0.3.

---

## Versioning policy

- **Major version (v1.x → v2.x):** breaking changes; old readers cannot open.
- **Minor version (v0.2 → v0.3):** still requires a migration tool, because v0.2 had `deprecated_columns` carrying the integer-rowid deprecation flag and v0.3 executes that removal — old v0.2 readers cannot interpret v0.3's `extraction_sources` table or ledger tables and must be upgraded.

**v0.1 (`LUN-FORMAT_v0.1.md`):** `meta.schema_version = 1`. Historical reference.

**v0.2 (`LUN-FORMAT_v0.2.md`):** `PRAGMA user_version = 2`, `meta.format_version = '0.2'`. Shipping as of 2026-05-12.

**v0.3 (this document):** `PRAGMA application_id = 0x4C554E43`, `PRAGMA user_version = 3`, `meta.format_version = '0.3'`. SPEC-005 annotation ledger live, SPEC-002 D5 integer-rowid removal applied, C-01 table rename applied. Status: Shipping (2026-05-22; engine commit `407122f`; `AUDIT_2026-05-22_meditations-v03.md` found no v0.3 shipping blockers).

**v0.4 (no spec drafted):** candidates include token-span / per-row logprob attribution, entity anchoring spec, cross-cartridge identity / `nexus_refs` semantics formalization, `meta.cartridge_ulid` introduction. Open list — not committed.

---

## Validation checklist

A v0.3 cartridge is valid if:

### Pragma layer

- [ ] SQLite file opens without errors
- [ ] `PRAGMA application_id = 0x4C554E43`
- [ ] `PRAGMA user_version = 3`
- [ ] `PRAGMA journal_mode = 'delete'`
- [ ] No `-wal` or `-shm` sidecar files exist on disk

### Meta layer

- [ ] All required meta keys present (per the table above)
- [ ] `meta.format_version = '0.3'`; parses to integer matching `user_version`
- [ ] `meta.cartridge_kind ∈ SUPPORTED_CARTRIDGE_KINDS`
- [ ] `meta.logprob_base = 'e'`
- [ ] `meta.logprob_attribution = 'response_level'`
- [ ] `meta.ledger_hash_algorithm = 'sha256'`
- [ ] `meta.ledger_genesis_ulid` is a valid ULID; references an actual `annotation_ledger.ulid`
- [ ] `meta.ledger_head_seq` equals `(SELECT MAX(seq) FROM annotation_ledger)`
- [ ] `meta.ledger_head_hash` equals `(SELECT entry_hash FROM annotation_ledger WHERE seq = MAX(seq))`
- [ ] `meta.source_path` is absent (forbidden v0.1 key)
- [ ] `meta.schema_version` is absent (forbidden v0.1 key)
- [ ] `meta.deprecated_columns` is absent (forbidden v0.2 holdover; its job is done in v0.3)
- [ ] `meta.node_count` equals `SELECT count(*) FROM doc_nodes`
- [ ] Title passes parser-artifact blocklist

### Schema layer

- [ ] `extractions.confidence` column does not exist
- [ ] `extractions` is `WITHOUT ROWID` with `ulid TEXT PRIMARY KEY`
- [ ] `extractions` has `anchor_status`, `anchor_reason`, `llm_logprob_sum`, `llm_token_count`, `extraction_method` columns
- [ ] `extraction_sources` table exists (NOT `claim_sources`)
- [ ] `extraction_sources` has columns `extraction_ulid`, `node_ulid`, `anchor_method`, `anchored_by`, `anchored_at`, `event_id`
- [ ] `extraction_context_nodes` table exists (NOT `claim_context_nodes`)
- [ ] `annotation_ledger` table exists with all 14 columns
- [ ] `annotation_actors` table exists, is `WITHOUT ROWID`
- [ ] Both append-only triggers exist on `annotation_ledger` with the expected DDL (whitespace-normalized comparison)
- [ ] All 4 ledger indexes exist (`idx_ledger_target`, `idx_ledger_actor`, `idx_ledger_type`, `idx_ledger_ts`)
- [ ] `doc_nodes` has `ulid` column with unique index; `parent_ulid` FK to `doc_nodes(ulid)`
- [ ] `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` is retained only as FTS5 content_rowid (Q1 Strategy A)
- [ ] `sqlite_sequence` exists with `name IN ('doc_nodes', 'annotation_ledger')`; no `extractions` entry remains

### Data layer

- [ ] No claim or summary has `anchor_status = 'unknown'`
- [ ] Every `anchor_status = 'synthesized'` extraction has ≥2 rows in `extraction_context_nodes` from ≥2 distinct parent lineages
- [ ] Every `anchor_status = 'anchored'` extraction has ≥1 row in `extraction_sources`
- [ ] Every `extraction_sources` row with `anchor_method != 'auto'` has both `anchored_by` and `anchored_at` populated
- [ ] All `doc_nodes.parent_ulid` either NULL or valid reference
- [ ] All `extraction_sources.extraction_ulid` reference valid `extractions.ulid`
- [ ] All `extraction_sources.node_ulid` reference valid `doc_nodes.ulid`
- [ ] All `embeddings.node_ulid` reference valid `doc_nodes.ulid`
- [ ] All `embeddings.vector` have length `embedding_dim * 4`
- [ ] `nodes_fts` count matches eligible `doc_nodes` count, indexed by `doc_nodes.id` as FTS5 content_rowid
- [ ] Every ULID matches `^[0-9A-HJKMNP-TV-Z]{26}$` and is unique within its table
- [ ] `llm_logprob_sum IS NULL` iff `llm_token_count IS NULL`
- [ ] `llm_logprob_sum ∈ (-1000, 0]` when populated
- [ ] `llm_token_count > 0` when populated

### Ledger layer (NEW in v0.3)

- [ ] Genesis row exists at `seq = 1` with `event_type = 'meta'`, `actor_role = 'system'`, `prev_hash IS NULL`, payload contains `application_id`, `user_version`, `source_hash`, `format_version`
- [ ] System actor row exists in `annotation_actors` with `actor_id = '00000000000000000000000000'`, `display_name = 'system'`, `primary_role = 'system'`
- [ ] Hash chain continuity: `prev_hash[N] == entry_hash[N-1]` for all N > 1
- [ ] Every row's SHA-256 of its canonical 10-field serialization equals stored `entry_hash`
- [ ] `entry_ts` monotone non-decreasing with `seq`
- [ ] Every payload validates against its `event_type` schema in SPEC-005_payload-schemas
- [ ] Every `extraction_sources.event_id` (when non-NULL) references an existing `annotation_ledger.entry_hash`
- [ ] CHECK constraints: `(target_kind IS NULL) = (target_ulid IS NULL)`; `target_cartridge_ulid IS NULL OR event_type IN ('cartridge_imported', 'cartridge_reviewed')`; `actor_role != 'system' OR event_type = 'meta'`

### SQLite integrity

- [ ] `PRAGMA integrity_check` returns `ok`
- [ ] `PRAGMA foreign_key_check` returns empty

A `lun fsck` tool implements these checks. The existing
`luna.cartridge.validation` module handles the data-layer invariants; v0.3
adds `validate_ledger(conn)` and `validate_payloads(conn)` for the ledger
layer. `lun fsck --ledger` runs the full chain walk (O(n));
`lun fsck --ledger-head` verifies only the ledger head pointer (O(1)).

---

## Open questions

Q1, Q2, and Q3 are resolved as of 2026-05-22. No open question or lifecycle
gate remains for v0.3 Shipping; the Meditations v0.3 reference-cartridge audit
found no v0.3 shipping blockers.

**Q1 — FTS5 reattachment strategy (resolved 2026-05-22).**

SPEC-002 D2 establishes that FTS5 external content mode requires an INTEGER
`content_rowid`. Removing `doc_nodes.id` in v0.3 conflicts with this. The
prototype in
`/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md`
tested the alternatives against the Meditations corpus and returned
**Recommend A**.

- **Decision:** keep `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` for
  FTS5 only. ULID is the canonical identifier; the integer is an internal
  implementation detail and MUST NOT be used for cross-table application
  identity.
- **Evidence:** Strategy A had FTS/map bytes `323,584`, build median
  `8.163ms`, and query p95 `0.385ms`. B1 preserved native snippets and
  top-20 ULID parity, but failed the 10% threshold on storage (`+187.3%`),
  build time (`+51.7%`), and query p95 (`+13.3%`). B2 preserved top-20 ULID
  parity and was faster, but failed the storage threshold (`+41.8%`) and
  required manual snippet fallback because native snippets were unavailable.
- **Result:** Strategy B1 and B2 are measured and deferred for v0.3. Reopen
  only if a future format revision prioritizes removing the FTS-only integer
  over native snippet support and storage simplicity.

**Resolution:** Strategy A is the v0.3 format decision.

**Q2 — `claim_context_nodes` → `extraction_context_nodes` rename (resolved 2026-05-22).**

Audit finding C-01 named only `claim_sources` for the C-01 rename. The
parallel `claim_context_nodes` table has the same naming issue (it holds
context for any extraction kind, not just claims).

- **Decision:** apply both renames. v0.3 uses `extraction_sources` and
  `extraction_context_nodes`.
- **Reason:** both tables are extraction-scoped, not claim-only. Keeping
  `extraction_sources` next to `claim_context_nodes` would preserve an
  avoidable naming mismatch at the exact release boundary where v0.3 already
  rewrites the tables to ULID primary keys.
- **Compatibility:** no v0.3 compatibility alias is defined for the old table
  names. v0.3 readers/builders should use the new names directly; v0.2 readers
  remain bound to `claim_sources` and `claim_context_nodes`.

**Resolution:** the symmetric rename is the v0.3 format decision.

**Q3 — Migration step order (resolved 2026-05-22).**

The migration sequence in §"Migration from v0.2" interleaves table
rewrites (step 5) with ledger creation (step 2) and event insertion
(steps 3, 4). The draft order assumes:

1. Ledger creation must precede genesis + migration events (steps 2 → 3 → 4): required because events can't insert without the table.
2. Migration events must precede table rewrites (steps 3, 4 → 5): the migration event is auditable provenance for the rewrite; inserting it after would lose the audit trail.
3. Table rewrites must precede pragma bump (steps 5 → 9): rewrites need to complete in a transaction before the version flips.

Engine implementation in Luna Engine commit `407122f` proved this order works.
`tests/test_cartridge_migrate_v3.py::test_q3_migration_order_holds` exercises
the documented order end-to-end, and the final verification run confirmed the
migrated v0.2 copy has ledger rows for genesis plus migration, `user_version =
3`, and `sqlite_sequence` entries for `doc_nodes` and `annotation_ledger`.

**Resolution:** the migration step order is accepted as drafted, with the
migration event amended to the `system + meta` form described above.

---

## References

- [`01_Specs/implemented/SPEC-005_annotation-ledger.md`](../01_Specs/implemented/SPEC-005_annotation-ledger.md) — annotation ledger DDL, hash chain mechanics, append-only triggers, genesis row, migration mechanics, validators
- [`01_Specs/implemented/SPEC-005_payload-schemas.md`](../01_Specs/implemented/SPEC-005_payload-schemas.md) — per-event-type payload contracts (8 event types + genesis), locked enum vocabularies, unknown-key preservation rule
- [`01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md`](../01_Specs/implemented/SPEC-004_multi-axis-imprint-weights.md) — application-layer composer contract; v0.3's ledger unlocks Contestation + Resonance axes
- [`01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md`](../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md) — `application_id` contract (v0.3 inherits unchanged), version tracking, finalize stack, title validation
- [`01_Specs/implemented/SPEC-001_orphan-claims.md`](../01_Specs/implemented/SPEC-001_orphan-claims.md) — anchor classification taxonomy and provenance columns (v0.3 inherits unchanged; only table/column names change per C-01)
- [`01_Specs/implemented/SPEC-002_portable-ids.md`](../01_Specs/implemented/SPEC-002_portable-ids.md) — ULID design, D3 (extractions WITHOUT ROWID), D5 (Phase 2 integer-rowid removal), v0.3 target schemas (lines 185-199, 236-240, 352-357)
- [`01_Specs/implemented/SPEC-003_meaningful-confidence.md`](../01_Specs/implemented/SPEC-003_meaningful-confidence.md) — raw LLM signals, response-level attribution (v0.3 inherits unchanged)
- [`01_Specs/implemented/SPEC-007_cartridge-sketches.md`](../01_Specs/implemented/SPEC-007_cartridge-sketches.md) — additive `sketches` table + `meta.sketches_present` + `meta.fts_tokenizer_config` for shelf-scoped bloom-filter pre-filter; implemented 2026-05-23 (engine slice on top of HEAD `65551ae`). DDL and meta-key contract documented inline at § `sketches` above; full semantics in the spec.
- [`03_Format_Spec/LUN-FORMAT_v0.2.md`](LUN-FORMAT_v0.2.md) — predecessor format spec; structural template for this document; v0.2 → v0.3 migration source state
- [`03_Format_Spec/LUN-FORMAT_v0.1.md`](LUN-FORMAT_v0.1.md) — historical reference; v0.1 → v0.2 → v0.3 migration is two hops
- [`04_Audits/AUDIT_2026-05-22_meditations-v02.md`](../04_Audits/AUDIT_2026-05-22_meditations-v02.md) — Meditations v0.2 audit; C-01 (table rename) and S-01 (embedding coverage policy, carried forward to v0.3)
- [`04_Audits/AUDIT_2026-05-22_meditations-v03.md`](../04_Audits/AUDIT_2026-05-22_meditations-v03.md) — Meditations v0.3 shipping-gate audit; no v0.3 shipping blockers found
- [`05_Reference/SQLite_Research.md`](../05_Reference/SQLite_Research.md) — Topics 3 (hash-chain prior art), 5 (migration), 7 (FTS5) all directly inform v0.3 design
- [`02_Handoffs/HANDOFF_2026-05-22_spec-005-engine-implementation.md`](../02_Handoffs/HANDOFF_2026-05-22_spec-005-engine-implementation.md) — engine-side implementation handoff for SPEC-005; the v0.3 format spec is the design target it implements against
- [`08_Journal/2026-05-22.md`](../08_Journal/2026-05-22.md) — drafting session log for this spec, SPEC-004 promotion, and the C-01/S-01 v0.2 follow-ups
