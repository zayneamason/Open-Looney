# .lun Cartridge Format Specification — Version 0.2

**Status:** Shipping (as of 2026-05-12; established by Phases 1–5 of the v0.2 implementation arc)
**Scope:** Cartridge family (`application_id = 0x4C554E43`, `'LUNC'`). The runtime matrix family (`'LUNM'`) is a sibling format with a different schema; it will get its own spec when its schema stabilizes.
**Source:** Reverse-engineered from `PRIESTS_AND_PROGRAMMERS_Lansing.lun` (v0.1, audit 2026-04-21), then evolved through SPEC-006, SPEC-001, SPEC-002, and SPEC-003 (all in `01_Specs/implemented/`).
**Builder version:** `luna.cartridge.builder` plus `luna.cartridge.validation` (validators centralized in Phase 5).
**Superseded by (in development):** v0.3 — removal phase for the integer rowid columns flagged in `meta.deprecated_columns`. No drafted spec yet.

---

## Overview

A v0.2 `.lun` cartridge is a SQLite 3 database with a known `application_id`, an
explicit `user_version`, a contracted `meta` table, anchor classification on every
extraction, ULID columns for portable cross-cartridge identity, and raw LLM signals
in place of the v0.1 hardcoded `confidence` constant. Any SQLite client can open
a cartridge; the format is defined by its pragmas, schema, meta rows, and a small
set of structural invariants.

**Design intent (unchanged from v0.1):** a portable, inspectable, read-optimized
knowledge cartridge that can be distributed as a single file and queried without
Luna-specific tooling. v0.2 hardens this with explicit family identification,
machine-readable version tracking, and provenance signals that make cross-cartridge
governance possible.

**What changed from v0.1, in one sentence:** v0.2 makes file identity, schema
version, anchor provenance, portable IDs, and raw extraction signals into
required contracts; v0.1 left all five as soft conventions or constants.

---

## File identification

- **Extension:** `.lun` (shared with the runtime matrix family — `application_id` discriminates).
- **Magic bytes:** SQLite 3 header (`SQLite format 3\0` at offset 0).
- **Application ID (cartridge family):** `0x4C554E43` (`'LUNC'`, decimal `1280659011`). **Required.** v0.2 readers refuse files where `application_id` does not match (`WrongFamilyError`).
- **Sibling family for comparison:** runtime matrix uses `0x4C554E4D` (`'LUNM'`, decimal `1280659021`).
- **User version:** `PRAGMA user_version = 2`. **Required.** Mirrored by `meta.format_version = '0.2'`. Reader trusts `user_version` as the binding source of truth; `meta.format_version` is human-readable documentation.

Identification flow for an external tool:

1. SQLite header at offset 0 — confirms it's a SQLite database.
2. `PRAGMA application_id` — `0x4C554E43` means cartridge family. Reject (or branch) on any other value.
3. `PRAGMA user_version` — MUST equal `2` for a v0.2 cartridge. v0.1 cartridges (`user_version = 1`) are legitimate members of the `.lun` cartridge family but MUST be migrated before a v0.2 reader will open them; the reader raises `UnsupportedVersionError(1)` with the migration command in the error text. Any other value raises `UnsupportedVersionError`. (Earlier drafts of this spec allowed `user_version IN [1, 2]` via SPEC-002 Q5's integer-only-mode fallback; that fallback was retired 2026-05-22 — see SPEC-002 Q5 RETIRED block.)
4. (Cartridge family only) `meta.cartridge_kind` — must be in `SUPPORTED_CARTRIDGE_KINDS` (currently `{'knowledge'}`). Other values raise `UnsupportedCartridgeKindError`.

Step 2 alone is sufficient to fast-reject "wrong kind of `.lun`" without parsing
the schema. This is the property SPEC-006 was designed to provide.

**Legacy fallback removed (Phase 5).** v0.2 readers no longer tolerate
`application_id = 0`. Pre-SPEC-006 v0.1 cartridges must be migrated via
`python -m luna.cartridge.migrate <path>` before they can be opened.

---

## Schema

### `meta` — manifest

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

Required keys in v0.2:

| Key | Type | Notes |
|-----|------|-------|
| `format_version` | semver string | `'0.2'`; mirrors `PRAGMA user_version = 2`. Replaces the v0.1 `schema_version` integer key. |
| `cartridge_kind` | enum string | `'knowledge'` in v0.2. Validated against `SUPPORTED_CARTRIDGE_KINDS` at read time. |
| `source_filename` | string | Basename only (e.g., `Lansing.pdf`). Replaces v0.1's absolute `source_path`. |
| `source_format` | string | `pdf`, `markdown`, etc. |
| `source_hash` | hex string | SHA-256 of source file. |
| `created_at` | ISO 8601 | Build timestamp. |
| `word_count` | integer | Total words in source. |
| `node_count` | integer | Total rows in `doc_nodes`. Validated equal to `SELECT count(*) FROM doc_nodes`. |
| `embedding_model` | string | e.g., `all-MiniLM-L6-v2`. |
| `embedding_dim` | integer | e.g., `384`. |
| `logprob_base` | string | `'e'` (natural log). Reader refuses any other value. |
| `logprob_attribution` | enum string | `'response_level'` in v0.2. Documents how per-row logprob values were computed. |
| `deprecated_columns` | comma-list | `'doc_nodes.id,extractions.id'` in v0.2. Flags integer rowid columns scheduled for removal in v0.3. |

Optional keys:

| Key | When set |
|-----|----------|
| `source_canonical_path` | Only when the builder is invoked with `--preserve-paths`. Full absolute path. Default behavior omits this key. |

Forbidden keys (v0.1 holdovers that must not appear in v0.2 cartridges):

| Key | Why forbidden |
|-----|---------------|
| `source_path` | v0.1 leaked the absolute builder-machine path. Replaced by `source_filename` + optional `source_canonical_path`. |
| `schema_version` | v0.1 integer key. Replaced by `format_version` semver string + `PRAGMA user_version`. |

**Title validation (Phase 1).** Titles run through a parser-artifact blocklist
before insert. Rejection rules, in order, first failure triggers fallback to
filename stem + `BuilderWarning`:

- `len(title.strip()) < 3` → reject
- `re.match(r"^[/.\\\-_\s]{1,3}\s", title)` → reject (parser artifact prefix; this is what catches the v0.1 `"/. Stephen Lansing"` case)
- `re.search(r"[A-Za-z0-9]", title) is None` → reject (no alphanumeric content)
- `title.strip().casefold() in {"untitled", "document", "document1"}` → reject

Author-as-title is explicitly not rejected — too many false positives for
academic preprints and monographs.

### `doc_nodes` — document tree

```sql
CREATE TABLE doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    type TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT,                       -- nullable; see "Content nullability" below
    meta_json TEXT,                     -- per-source structured metadata; see "meta_json shapes" below
    ulid TEXT NOT NULL,                 -- SPEC-002: portable cross-cartridge identity
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);

CREATE INDEX idx_doc_nodes_parent ON doc_nodes(parent_id);
CREATE INDEX idx_doc_nodes_type ON doc_nodes(type);
CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
```

**Node types** (observed vocabulary): `document`, `section`, `paragraph`, `sentence`, plus the rich Markdown/PDF set `list`, `list_item`, `figure`, `table`, `row`, `cell`.

**Hierarchy:** `document → section → paragraph → sentence` is the dominant chain. Sections may nest under other sections; the diagram describes node-type ordering, not strict depth. The Marcus-Aurelius-Meditations reference cartridge has 128 sections directly under `document` + 48 sections nested under other sections = 176 total. Rich nodes (list/figure/table) appear under paragraphs or sections as appropriate.

**Content nullability.** `doc_nodes.content` is nullable. Container nodes (`document`, `section`, and paragraphs whose text lives in `sentence` children) commonly have `content IS NULL`. In the Marcus-Aurelius-Meditations reference cartridge, 439 of 3813 rows (~11.5%) carry NULL content. Readers should reconstruct paragraph text from sentence children when needed, and treat the `document` and `section` rows as containers rather than content carriers. The FTS triggers (see `nodes_fts` below) `COALESCE` content to empty string so NULL rows insert empty index entries rather than failing.

**`meta_json` per-source shapes.** `meta_json` is a JSON string with source-format-specific keys. Documented shapes:

| Source | Node types | `meta_json` shape |
|---|---|---|
| All sources | `document` (root) | `{"title": str}` |
| PDF | `section`, `paragraph`, `sentence` | `{"page_num": int}`, optionally with `"title": str` on sections |
| Markdown | `section` | `{"title": str, "level": int}` |
| Markdown | `paragraph` (fenced code) | `{"src": str, "language": str}` |

Readers MUST treat unknown keys as forward-compatible — additional keys may appear from future builders or new source formats. Readers SHOULD NOT assume `meta_json` is non-NULL on every row in future cartridges, even though the current builder always populates it.

**ULID column (SPEC-002).** `doc_nodes.id` stays as `INTEGER PRIMARY KEY
AUTOINCREMENT` because FTS5 external content mode requires an INTEGER
`content_rowid`. The new `ulid` column is the portable identity layer used for
any reference that crosses a cartridge boundary; `id` remains the local rowid
for FTS5 and internal joins. The integer column is flagged in
`meta.deprecated_columns` and will be removed in v0.3 (FTS5 reattachment
required at that point).

**ULID format.** 26-char Crockford Base32 uppercase, e.g.
`01HQ3KZXD4FGTW8N5PJKZMBV3R`. First char is in `[0-7]` per the canonical
48-bit timestamp + 80-bit random generator (`ULIDGenerator` in `builder.py`,
hardened in Phase 3.5 — the original `ts << 16 | counter` sketch overflowed
and produced first chars in `[G-Z]` which strict ULID parsers rejected; do not
copy that pattern). Migrated cartridges (added via `ALTER TABLE ADD COLUMN`)
carry `ulid TEXT` without the inline `NOT NULL` + `CHECK` declaration because
`ALTER TABLE ADD COLUMN` cannot retrofit those into the column declaration;
data still passes `validate_ulids()` and the GLOB check applied at the index
level.

### `extractions` — LLM artifacts

```sql
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    -- SPEC-001: anchor classification
    anchor_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (anchor_status IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')),
    anchor_reason TEXT,
    -- SPEC-002: portable identity
    ulid TEXT,
    -- SPEC-003: raw signals (replaces v0.1 hardcoded confidence)
    llm_logprob_sum REAL,
    llm_token_count INTEGER,
    extraction_method TEXT NOT NULL DEFAULT 'llm'
        CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual'))
);

CREATE INDEX idx_extractions_type ON extractions(type);
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);
```

**Extraction types** (observed vocabulary): `claim`, `entity`, `summary`.

**No `confidence` column (SPEC-003).** The v0.1 `confidence REAL DEFAULT 1.0`
column has been dropped. Every v0.1 value was either `0.85` or `0.9`, set
unconditionally by extraction type; the column carried no information. Trust
signals are now decomposed into `anchor_status` (categorical), `extraction_method`
(provenance), and `llm_logprob_sum` / `llm_token_count` (LLM uncertainty at call
scope). Composition into a trust score lives in code (SPEC-004 territory), not
in the cartridge.

**`anchor_status` taxonomy (SPEC-001):**

| Value | Meaning |
|-------|---------|
| `anchored` | Has ≥1 row in `claim_sources` linking to source nodes. |
| `synthesized` | Cross-sentence abstraction with no single source. Has ≥2 rows in `claim_context_nodes` drawn from ≥2 distinct parent lineages. |
| `match_failed` | Source exists but the matcher didn't link it. May carry hints in `claim_context_nodes`. |
| `filtered` | Intentionally dropped by post-processing (frontmatter, attribution). Hidden from primary reads by default. |
| `unknown` | Legacy / not yet classified. **No v0.2 cartridge may ship with `anchor_status = 'unknown'` on any claim** — `validate_anchors()` rejects builds that contain unclassified claims. |

Entity rows are scoped out of SPEC-001 classification and carry `anchor_status =
'unknown'` legitimately in v0.2. Entity anchoring is deferred to a future spec.

**`extraction_method`** distinguishes builder-produced LLM extractions from
future rule-based, NER, or operator-supplied rows. Strict CHECK; new methods
require a spec amendment.

**LLM signal columns:**

- `llm_logprob_sum REAL` — sum of natural-log token probabilities for the
  LLM call that produced this extraction. Range `(-∞, 0]`. NULL when no LLM
  logprob signal available.
- `llm_token_count INTEGER` — number of content tokens covered by
  `llm_logprob_sum`. Paired with `llm_logprob_sum`: both NULL or both
  populated (invariant enforced in `validate_extractions()`).
- **Response-level attribution (v0.2 contract).** All extractions produced by
  a single LLM call share the same `llm_logprob_sum` and `llm_token_count`
  values. Readers must interpret this as call-level uncertainty, not
  per-claim discrimination. Multiple rows with identical logprob values are
  expected, not a bug. Documented machine-readably via
  `meta.logprob_attribution = 'response_level'`; readers refuse to open
  cartridges with any other attribution value (until a future spec adds
  support for `'token_span'` or `'claim_level'`).

### `claim_sources` — claim-to-source anchoring

```sql
CREATE TABLE claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    -- SPEC-001: anchor provenance
    anchor_method TEXT NOT NULL DEFAULT 'auto'
        CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by TEXT,                   -- actor ID; NULL allowed for 'auto'
    anchored_at INTEGER,                -- unix ms; required for 'manual' / 'migrated'
    event_id TEXT,                      -- forward ref to ledger event (SPEC-005); NULL until ledger exists
    -- SPEC-002 shadow ULIDs (Phase 3). Nullable in v0.2; become NOT NULL and join the
    -- composite PK in v0.3 (see SPEC-002 Phase 2 of the two-phase migration plan).
    claim_ulid TEXT,
    node_ulid TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);

CREATE INDEX idx_claim_sources_node ON claim_sources(node_id);
```

**Design intent:** many-to-many bridge from extracted claims to their source
sentences/paragraphs. Builder-produced anchors are currently 1:1; the schema
supports multi-source for future use.

**Type scope (audit clarification, 2026-05-22).** Despite the column name
`claim_id`, the foreign key references any `extractions.id` regardless of
`extractions.kind` — both `claim` and `summary` extractions are anchored via this
table. The Meditations audit confirmed 458 claim + 62 summary rows in
`claim_sources`. The "claim" naming is historical; a v0.3 rename to
`extraction_sources(extraction_id, ...)` is queued per audit finding C-01. In
v0.2, treat `claim_id` as "the extraction this anchor row belongs to" regardless
of extraction kind.

**Provenance columns (SPEC-001).** Distinguish builder-produced anchors from
community-upgraded anchors from migration-time anchors. Non-`auto` methods
require both `anchored_by` and `anchored_at` populated; enforced in
`validate_anchors()`. `event_id` is a forward reference into the SPEC-005
ledger; NULL until that spec ships.

**Shadow ULID columns (SPEC-002 Phase 2).** `claim_ulid` and `node_ulid` mirror
the integer `claim_id` / `node_id` columns in portable form. In v0.2 they are
nullable TEXT, populated by the builder for fresh-built cartridges and by the
migration tool for migrated cartridges. Both columns join the composite primary
key and become NOT NULL in v0.3, replacing the integer FKs entirely. v0.2
readers MAY consume the shadow ULIDs (e.g., for cross-cartridge provenance
display) but MUST NOT rely on them being non-NULL.

**Ambassador upgrade flow** ("upgrade to anchored" ceremony):

1. `INSERT INTO claim_sources (claim_id, node_id, anchor_method, anchored_by, anchored_at, event_id) VALUES (?, ?, 'manual', ?, ?, ?)`
2. `UPDATE extractions SET anchor_status = 'anchored' WHERE id = ?`

Same table is the source of truth for all anchors; `anchor_method` distinguishes
provenance without splitting the anchor graph.

### `claim_context_nodes` — soft anchoring for synthesis claims

```sql
CREATE TABLE claim_context_nodes (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    relevance REAL NOT NULL,            -- 0.0 - 1.0 semantic similarity
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);

CREATE INDEX idx_claim_context_claim ON claim_context_nodes(claim_id);
CREATE INDEX idx_claim_context_node ON claim_context_nodes(node_id);
```

**Purpose (SPEC-001).** For claims classified as `synthesized`, this table
holds the set of source nodes the synthesis was drawn from. May also hold
hint rows for `match_failed` or `filtered` claims.

**Synthesis invariants** (enforced in `validate_anchors()`):

- A claim with `anchor_status = 'synthesized'` must have ≥2 rows here.
- Those rows must reference `doc_nodes` with ≥2 distinct `parent_id` values
  (distinct lineage — prevents two sentences under the same paragraph from
  being mislabeled as synthesis).

### `embeddings` — vector blobs

```sql
CREATE TABLE embeddings (
    node_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (node_id, level)
);

CREATE INDEX idx_embeddings_level ON embeddings(level);
```

**Levels** (observed vocabulary): `paragraph`, `section`.

**Vector format:** raw float32 bytes. For `all-MiniLM-L6-v2` with
`embedding_dim = 384`, each blob is 1536 bytes (384 × 4).

**Consumer contract:** readers MUST validate `length(vector) == embedding_dim * 4`
before interpretation. Model-specific. Switching embedding models requires
dropping and rebuilding this table.

**Coverage policy (audit clarification, 2026-05-22 — finding S-01).** Embedding
coverage is best-effort, not total. Builders MAY skip `doc_nodes` rows with NULL
content, content below a builder-defined length threshold, or other
policy-defined exclusions (e.g., title-page fragments). Readers MUST NOT assume
every `doc_nodes` row has a corresponding `embeddings` row; queries that need to
distinguish "no embedding" from "embedding absent for this run" should LEFT JOIN
rather than INNER JOIN. No reader invariant in v0.2 requires full coverage. The
Meditations reference cartridge (2026-05-22) shows 310/310 paragraph and 149/176
section embeddings — a representative non-total baseline. The set of skipped
rows is not currently recorded in `meta`; a future spec may add a builder
embedding-policy stamp if coverage policy needs to be auditable from the file
itself.

### `nodes_fts` — full-text search

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'
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

External content FTS5 table. Index syncs to `doc_nodes.content` via the three
triggers above. The `COALESCE(content, '')` wrapping handles the nullable
`doc_nodes.content` column — NULL-content rows produce empty index entries
rather than trigger errors. (Earlier drafts of this spec showed the triggers
without `COALESCE`; the canonical builder schema has always emitted them with
`COALESCE`, and the spec is brought in line with the builder here.)

**Shadow tables** (created by FTS5, not directly queried):
`nodes_fts_data`, `nodes_fts_idx`, `nodes_fts_docsize`, `nodes_fts_config`.

**Why `doc_nodes` keeps integer rowid (SPEC-002 D2).** FTS5 external content
mode requires `content_rowid` to be INTEGER. Switching `doc_nodes` to WITHOUT
ROWID would break this. v0.2 adds the `ulid` column alongside; v0.3 will
require FTS5 reattachment if the integer column is removed (one of the
v0.3-design questions yet to be resolved).

### `sqlite_sequence` — AUTOINCREMENT tracking

Created automatically by SQLite. Tracks next rowid for AUTOINCREMENT tables.
Present in v0.2 because `doc_nodes.id` and `extractions.id` are still
AUTOINCREMENT. Goes away in v0.3 when the integer rowids are removed
(`extractions` migrates to `WITHOUT ROWID` with ULID PK per SPEC-002 D3).

### `nexus_refs` — cross-cartridge promotion placeholder (SPEC-005)

```sql
CREATE TABLE nexus_refs (
    local_node_id TEXT NOT NULL,
    nexus_node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (local_node_id, node_type)
);

CREATE INDEX idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
```

**Purpose.** Tracks which local nodes have been promoted to the Nexus (runtime
matrix family, `'LUNM'`). Each row pairs a local cartridge node identity with
the Nexus node identity it was promoted to. `local_node_id` is TEXT for
cross-schema compatibility — cartridge `.lun` files identify nodes by their
ULID (string), and the same `promote_to_nexus()` builder code path also
handles aibrarian `.db` files which already use TEXT UUIDs.

**Status in v0.2.** Empty in all v0.2 cartridges. Created by the v0.2 builder
as a forward-compatibility placeholder for [SPEC-005] (annotation ledger and
cross-cartridge promotion). v0.2 readers should not display this table; it
becomes meaningful when SPEC-005 ships and the promotion flow starts
populating rows. The reference cartridge (`Marcus-Aurelius-Meditations.lun`)
ships with zero rows.

---

## Build pipeline

```
source file (.md/.pdf)
    ↓ parse
doc_nodes (hierarchical, ULIDs assigned at insert)
    ↓ optional LLM pass (Haiku, response-level logprobs captured)
extractions + claim_sources + claim_context_nodes (anchor_status classified)
    ↓ optional embedding pass (MiniLM)
embeddings
    ↓ validate (validate_extractions, validate_ulids, validate_anchors, ...)
    ↓ finalize (optimize, wal_checkpoint, journal_mode=DELETE, VACUUM)
.lun file (SQLite, v0.2 contract)
```

CLI:
```
python -m luna.cartridge.builder input.md [output.lun] [--no-extract] [--no-embed] [--preserve-paths]
```

### Pragma stack at build creation

Set as the first DDL after connection open, before any data inserts:

```sql
PRAGMA application_id = 0x4C554E43;  -- 'LUNC'
PRAGMA user_version = 2;
PRAGMA journal_mode = WAL;            -- during build; switched to DELETE at finalize
PRAGMA busy_timeout = 15000;
PRAGMA foreign_keys = ON;
```

### Finalize stack (after all inserts, before shipping)

```sql
PRAGMA optimize;                       -- ANALYZE-equivalent
PRAGMA wal_checkpoint(TRUNCATE);       -- fold WAL frames into main file
PRAGMA journal_mode = DELETE;          -- shipping mode: no sidecar files
VACUUM;                                -- reclaim space, defragment
```

**Critical for portability:** DELETE journal mode means no `-wal` or `-shm`
sidecar files travel with the cartridge. WAL mode would require the recipient
to have a writable directory and would break the "drop a `.lun` anywhere and
read it" property.

### Validators (run before finalize; build fails on any error)

Centralized in `src/luna/cartridge/validation.py` after Phase 5 Step 5:

- `validate_extractions(conn)` — SPEC-003 invariants (no `confidence` column;
  `llm_logprob_sum ∈ (-1000, 0]` when present; positive token count when
  present; paired-NULL invariant; `extraction_method` ∈ allowed set;
  `meta.logprob_base = 'e'`; `meta.logprob_attribution = 'response_level'`).
- `validate_ulids(conn)` — SPEC-002 invariants (every row has a ULID; format
  matches Crockford Base32 GLOB; uniqueness; first char in `[0-7]`).
- `validate_anchors(conn)` — SPEC-001 invariants (no claim has
  `anchor_status = 'unknown'`; synthesis has ≥2 multi-lineage context nodes;
  anchored claims have ≥1 row in `claim_sources`; non-auto anchors carry
  provenance).
- `validate_cartridge_open(conn)` — read-time gate (right `application_id`,
  `user_version` in supported range, `cartridge_kind` in
  `SUPPORTED_CARTRIDGE_KINDS`).

---

## Read path

### Open contract

```python
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
conn.execute("PRAGMA query_only = 1")
conn.execute("PRAGMA mmap_size = 268435456")    # 256MB
conn.execute("PRAGMA cache_size = -8000")       # 8MB
validate_cartridge_open(conn)
```

`validate_cartridge_open()` enforces:

1. `application_id == 0x4C554E43` (`WrongFamilyError` otherwise; error message
   points at the migration command).
2. `user_version == 2` (`UnsupportedVersionError` otherwise; `user_version == 1` returns `UnsupportedVersionError(1)` with the migrate command in the error text — see SPEC-002 Q5 RETIRED for the rationale behind strict-reject vs. the original integer-only-mode fallback).
3. `meta.cartridge_kind` in `SUPPORTED_CARTRIDGE_KINDS = {'knowledge'}`
   (`UnsupportedCartridgeKindError` otherwise).
4. `meta.format_version` parses to an integer matching `user_version`; on
   mismatch, log warning and trust `user_version`.
5. (v0.2 readers expect SPEC-003 meta markers) `meta.logprob_base = 'e'` and
   `meta.logprob_attribution = 'response_level'`; reject on any other value
   (`UnsupportedAttributionError`).

### Display invariants for `anchor_status`

- **`anchored`** — display with single source quotation. Standard grounding.
- **`synthesized`** — display with "synthesized from N sources" and surface the
  context-node set on request. Do not attribute to a single sentence.
- **`match_failed`** — display with a "⚠ unanchored" badge in any UI surface.
- **`filtered`** — exclude from normal reading/retrieval by default. Audit and
  debug surfaces expose a "Show filtered claims" toggle with a badge showing
  the filter reason.
- **`unknown`** — should not occur in v0.2 cartridges. Readers seeing this on
  a v0.2 file have encountered a bug.

### Trust signal composition

The cartridge carries raw signals only. Composition into a trust score is
application-level and lives in code, not in the file. SPEC-004 (implemented, 2026-05-22) formalizes the multi-axis composition; the reader prototype ships the canonical reference composer `lun.format/reference-v1@1.0.0`. Other engines that compose trust should label any output that does not follow SPEC-004 as
application-level interpretation.

Inputs available to a composer:

- Categorical: `anchor_status`, `anchor_method`, `extraction_method`
- Continuous (at LLM-call scope): `llm_logprob_sum`, `llm_token_count`
- Optional: `claim_context_nodes.relevance` for synthesis claims

---

## Migration from v0.1

Migration is atomic, transactional, and re-runnable. Implemented in
`src/luna/cartridge/migrate.py`:

```
python -m luna.cartridge.migrate <path>
python -m luna.cartridge.migrate --dry-run <path>     # clones to in-memory DB, validates, discards
python -m luna.cartridge.migrate --strict <path>      # fails if any orphan would receive 'migration_unclassified' fallback
```

The migration applies four spec changes in a single transaction:

1. **SPEC-006:** sets `application_id`, `user_version`, adds `meta.format_version`,
   `cartridge_kind`, `source_filename` (from existing `source_path`), removes
   `meta.source_path` and `meta.schema_version`, runs the finalize stack.
2. **SPEC-001:** adds anchor classification columns and `claim_context_nodes`
   table. Existing anchored claims get `anchor_status = 'anchored'`; orphans
   default to `anchor_status = 'match_failed'` with
   `anchor_reason = 'migration_unclassified'`. Existing `claim_sources` rows
   retain `anchor_method = 'auto'` (relabeling as `'migrated'` would falsely
   imply migration-time provenance and would violate the non-auto-requires-
   `anchored_by` invariant since the original builder run has no actor identity).
3. **SPEC-002:** adds ULID columns to `doc_nodes` and `extractions`. ULIDs
   generated within a single timestamp band, so the first char is `0` for all
   migrated rows. The migrated tables get `ulid TEXT` (nullable on the column
   declaration) rather than `ulid TEXT NOT NULL` + inline CHECK — `ALTER
   TABLE ADD COLUMN` can't retrofit those into the declaration, but data
   still passes `validate_ulids()` and the GLOB check applied at the index
   level.
4. **SPEC-003:** drops `extractions.confidence`, adds `llm_logprob_sum`,
   `llm_token_count`, `extraction_method` (defaults to `'llm'` for all
   pre-existing rows), sets `meta.logprob_base = 'e'` and
   `meta.logprob_attribution = 'response_level'`. Logprob/token-count columns
   remain NULL — never fabricated.

The migration is **read-compatible in both directions** at the schema level: v0.2
readers can open v0.1 cartridges only after migration (the legacy `app_id = 0`
fallback was removed in Phase 5). Pre-Phase-5 v0.2 readers did open v0.1
cartridges with a degraded UX (everything `anchor_status = 'unknown'`); current
v0.2 readers do not.

**Cross-table invariants** that span multiple specs are enforced at the
application layer in the centralized validators rather than via SQLite
triggers or `ALTER TABLE ADD CONSTRAINT`. Migration is permissive at
schema-add time, strict at build-time validation, matching the precedent in
SPEC-001's migration mechanics section.

---

## Known v0.2 limitations

Status as of 2026-05-21:

1. **No annotations** — cartridges still cannot accumulate community input. [SPEC-005 accepted 2026-05-21; v0.3 engine implementation pending]
2. **No access log** — no record of who has read or queried a cartridge. [future spec]
3. **No contracts** — no declared rules about permissible use. [future spec]
4. **No ledger** — `claim_sources.event_id` exists but is always NULL; no ledger backs it. [SPEC-005 accepted 2026-05-21; v0.3 engine implementation pending]
5. **No integrity chain** — `source_hash` proves source provenance at build time; nothing chains subsequent annotation events. [SPEC-005 accepted 2026-05-21; v0.3 engine implementation pending]
6. **Single-axis trust composition** — readers must compose `anchor_status` + `extraction_method` + `llm_logprob_sum` into trust scores at the application layer; SPEC-004 (implemented 2026-05-22) defines the four-axis contract and reader v0.3.1 ships the canonical reference composer `lun.format/reference-v1@1.0.0`. v0.2 cartridges yield non-NULL Authority + Temporal axes immediately; Contestation + Resonance require the v0.3 ledger.
7. **Integer rowids still present** — `doc_nodes.id` and `extractions.id` remain as AUTOINCREMENT INTEGER alongside the ULID columns. Flagged in `meta.deprecated_columns`. [v0.3 removal phase drafted in `LUN-FORMAT_v0.3.md`; engine implementation pending]
8. **Response-level logprob attribution only** — all extractions from one LLM call share identical `llm_logprob_sum` / `llm_token_count`. Per-row discrimination would require token-span attribution. [future spec]
9. **Backend logprobs not fully exposed** — `HaikuResult.usage` fields are not surfaced to the builder; logprob columns are NULL on most current builds. The paired-NULL invariant is satisfied, but the response-level signal isn't being captured. [v0.3 backend-side improvement]
10. **Entity anchoring deferred** — entities legitimately carry `anchor_status = 'unknown'` in v0.2. Only claims are subject to the anchor taxonomy. [future spec]
11. **Filtered classification not auto-derived** — Phase 5 migration applies `migration_unclassified` to all orphans rather than running the full synthesis / filtered / match_failed detection. Real classification (multi-lineage similarity, section-type heuristics) is deferred. [SPEC-004 territory or its own spec]

### Carried-forward Phase 5 deferrals (cartridge-build specific)

12. **Lansing 9.5%-baseline measurement undefined.** The Phase 5 Lansing v0.2
    rebuild went through Path B (reconstruction from pre-quarantine DB
    `full_text`, source PDF unavailable). The reconstruction has no `#`
    headings, so `MarkdownParser` produced 1 section spanning 5576 nodes;
    `CartridgeExtractor.extract()` truncated to 8000 chars
    (`extractor.py:167`); Haiku returned 1 summary + 15 entities + 0 claims.
    Headline `claim_match_failed / (claim_anchored + claim_match_failed)`
    ratio is `0/0`. Resolution path: either recover the source PDF, or
    pre-process the reconstruction to inject `#` markers before build.
13. **`\x0c` form-feed artifacts** in cartridge text content. PDF page-break
    characters survive the reconstruction; parser tolerates them but they're
    visible in `doc_nodes.content`. Strip as part of any chapter-splitting
    pre-process.

### Operational ceiling

14. **`magic.txt` not registered.** Both `LUNC` (`0x4C554E43`) and `LUNM`
    (`0x4C554E4D`) are in use locally. Neither is in the SQLite project's
    `magic.txt` registry. Courtesy submission, not load-bearing.

---

## Versioning policy

- **Major version (v1.x → v2.x):** breaking changes; old readers cannot open.
- **Minor version (v0.1 → v0.2):** additive only; old readers open by ignoring
  additions (subject to the v0.2 contract requirements above — v0.2 is the
  first version where the reader can rely on `application_id` being set, so
  v0.1 → v0.2 required a migration tool rather than pure forward-compat).

**v0.1 (`LUN-FORMAT_v0.1.md`):** `meta.schema_version = 1`. No
`application_id`, no `user_version`. Hardcoded `confidence`. No anchor
classification. No ULIDs. Historical reference only; cartridges migrate via
`python -m luna.cartridge.migrate <path>` before use.

**v0.2 (this document):** `PRAGMA application_id = 0x4C554E43`,
`PRAGMA user_version = 2`, `meta.format_version = '0.2'`. SPEC-001 anchor
taxonomy, SPEC-002 ULID identity (additive), SPEC-003 raw signals (with
`confidence` dropped), SPEC-006 application_id contract and hygiene bundle
fully implemented. Currently shipping.

**v0.3 (in development, no spec drafted):** removal phase for the integer
rowid columns flagged in `meta.deprecated_columns`. `extractions` becomes
`WITHOUT ROWID` with ULID PK per SPEC-002 D5. `doc_nodes.id` removal requires
FTS5 reattachment — open design question. Likely bundled with backend logprob
exposure (item 9 above) and entity anchoring (item 10).

---

## Validation checklist

A v0.2 cartridge is valid if:

### Pragma layer

- [ ] SQLite file opens without errors
- [ ] `PRAGMA application_id = 0x4C554E43`
- [ ] `PRAGMA user_version = 2`
- [ ] `PRAGMA journal_mode = 'delete'`
- [ ] No `-wal` or `-shm` sidecar files exist on disk

### Meta layer

- [ ] All required meta keys present (per the table above)
- [ ] `meta.format_version = '0.2'`; parses to integer matching `user_version`
- [ ] `meta.cartridge_kind ∈ SUPPORTED_CARTRIDGE_KINDS`
- [ ] `meta.logprob_base = 'e'`
- [ ] `meta.logprob_attribution = 'response_level'`
- [ ] `meta.source_path` is absent (forbidden v0.1 key)
- [ ] `meta.schema_version` is absent (forbidden v0.1 key)
- [ ] `meta.node_count` equals `SELECT count(*) FROM doc_nodes`
- [ ] Title passes parser-artifact blocklist

### Schema layer

- [ ] `extractions.confidence` column does not exist
- [ ] `extractions` has `anchor_status`, `anchor_reason`, `ulid`, `llm_logprob_sum`, `llm_token_count`, `extraction_method` columns
- [ ] `claim_sources` has `anchor_method`, `anchored_by`, `anchored_at`, `event_id` columns
- [ ] `claim_context_nodes` table exists
- [ ] `doc_nodes` has `ulid` column with unique index

### Data layer

- [ ] No claim has `anchor_status = 'unknown'`
- [ ] Every `anchor_status = 'synthesized'` claim has ≥2 rows in `claim_context_nodes` from ≥2 distinct parent lineages
- [ ] Every `anchor_status = 'anchored'` claim has ≥1 row in `claim_sources`
- [ ] Every `claim_sources` row with `anchor_method != 'auto'` has both `anchored_by` and `anchored_at` populated
- [ ] All `doc_nodes.parent_id` either NULL or valid reference
- [ ] All `claim_sources.claim_id` reference valid `extractions.id`
- [ ] All `claim_sources.node_id` reference valid `doc_nodes.id`
- [ ] All `embeddings.vector` have length `embedding_dim * 4`
- [ ] `nodes_fts` count matches eligible `doc_nodes` count
- [ ] Every ULID matches `^[0-9A-HJKMNP-TV-Z]{26}$` and is unique within its table
- [ ] `llm_logprob_sum IS NULL` iff `llm_token_count IS NULL`
- [ ] `llm_logprob_sum ∈ (-1000, 0]` when populated
- [ ] `llm_token_count > 0` when populated

### SQLite integrity

- [ ] `PRAGMA integrity_check` returns `ok`
- [ ] `PRAGMA foreign_key_check` returns empty

A `lun fsck` tool should implement these checks. The existing
`luna.cartridge.validation` module already implements the data-layer
invariants; a standalone audit tool would wrap those plus the pragma and
meta-layer checks.

---

## References

- `01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md` — `application_id` contract, version tracking, finalize stack, title validation
- `01_Specs/implemented/SPEC-001_orphan-claims.md` — anchor classification taxonomy, provenance columns, synthesis invariants
- `01_Specs/implemented/SPEC-002_portable-ids.md` — ULID design, FTS5 interaction, two-phase migration (v0.2 additive, v0.3 removal)
- `01_Specs/implemented/SPEC-003_meaningful-confidence.md` — raw LLM signals, response-level attribution, reader contract for trust composition
- `03_Format_Spec/LUN-FORMAT_v0.1.md` — superseded; historical reference for the v0.1 shipping format
- `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md` — original v0.1 audit, source of the eight findings that became the v0.2 specs
- `05_Reference/SQLite_Research.md` — Topics 1 (application_id), 2 (portable PKs), 4 (CHECK/triggers), 5 (migration), 6 (ATTACH), 7 (FTS5), 8 (read-only) all directly inform v0.2 design choices
- `06_Prototypes/ReaderPrototype/SPEC.md` — Tauri reader v0.2.0; first independent v0.2 consumer; v1-build findings drove the 2026-05-22 amendments to this spec (`doc_nodes.content` nullability, `meta_json` per-source shapes, `claim_sources` shadow ULIDs, `nexus_refs` declaration, `user_version` strictness, FTS trigger COALESCE)
- `08_Journal/2026-05-10.md` — `application_id` decision and family-split reasoning
- `08_Journal/2026-05-21.md` — status sweep recognizing v0.2 as shipped
