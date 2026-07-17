# .lun Cartridge Format Specification — Version 0.1

**Status:** Shipping (as of 2026-04-10)
**Scope:** Cartridge family (`application_id = 0x4C554E43`, `'LUNC'`). The runtime matrix family (`'LUNM'`) is a sibling format with a different schema; it will get its own spec when its schema stabilizes.
**Source:** Reverse-engineered from `PRIESTS_AND_PROGRAMMERS_Lansing.lun`
**Builder version:** `luna.cartridge.builder` (schema.py line 10)
**Superseded by:** v0.2 (shipping as of 2026-05-12) — see `01_Specs/implemented/` for the four bundled specs (SPEC-006, SPEC-001, SPEC-002, SPEC-003) and the forthcoming `LUN-FORMAT_v0.2.md`

---

## Overview

A `.lun` cartridge file is a SQLite 3 database. The file extension is shared
with the runtime matrix family (per `00_README/README.md` and SPEC-006); the
two are distinguished by their `application_id` pragma values. Any SQLite
client can open a cartridge. The format is defined by its schema, meta rows,
and a set of structural conventions.

**Design intent:** a portable, inspectable, read-optimized knowledge cartridge
that can be distributed as a single file and queried without Luna-specific
tooling.

---

## File identification

- **Extension:** `.lun` (shared with the runtime matrix family — `application_id` discriminates)
- **Magic bytes:** SQLite 3 header (`SQLite format 3\0` at offset 0)
- **Application ID (cartridge family):** `0x4C554E43` (`'LUNC'`).
  Not set in v0.1 builds; established as a required contract in v0.2 (SPEC-006).
- **Sibling family for comparison:** runtime matrix uses `0x4C554E4D` (`'LUNM'`).
- **User version:** Not set in v0.1; v0.2 sets `PRAGMA user_version = 2`
  alongside `meta.format_version = '0.2'` (SPEC-006).

Because cartridge `.lun` files are SQLite, identification works via:
1. SQLite header at offset 0
2. `application_id` pragma at offset 68 — cartridge: `0x4C554E43`, matrix: `0x4C554E4D` (v0.2+)
3. Presence of `meta` table with key `format_version` (cartridge family) or `schema_version` (v0.1 cartridges, deprecated key)

---

## Schema

### `meta` — manifest

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

Required keys (as of v0.1):

| Key | Type | Example |
|-----|------|---------|
| `title` | string | Document title |
| `source_path` | string | Absolute path of source file at build time |
| `source_format` | string | `pdf`, `md`, etc. |
| `source_hash` | hex string | SHA-256 of source file |
| `created_at` | ISO 8601 | Build timestamp |
| `schema_version` | integer | Currently `1` (replaced by `format_version` string in v0.2 per SPEC-006) |
| `word_count` | integer | Total words in source |
| `node_count` | integer | Total rows in `doc_nodes` |
| `embedding_model` | string | e.g. `all-MiniLM-L6-v2` |
| `embedding_dim` | integer | e.g. `384` |

**Changes in v0.2** (per SPEC-006, accepted):
- `schema_version` (integer) → `format_version` (semver string, e.g. `'0.2'`); mirrors `PRAGMA user_version`
- `source_path` (absolute) → `source_filename` (basename only); `source_canonical_path` opt-in via `--preserve-paths`
- Adds `cartridge_kind` (currently `'knowledge'`; strict-validated against `SUPPORTED_CARTRIDGE_KINDS`)
- Title now validated against a parser-artifact blocklist before insert

### `doc_nodes` — document tree

```sql
CREATE TABLE doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    type TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    -- ... additional columns truncated in audit
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);

CREATE INDEX idx_doc_nodes_parent ON doc_nodes(parent_id);
CREATE INDEX idx_doc_nodes_type ON doc_nodes(type);
```

**Node types** (observed): `document`, `section`, `paragraph`, `sentence`

**Hierarchy:** `document → section → paragraph → sentence`

**v0.2 changes** (per SPEC-002, accepted): `doc_nodes.id` keeps INTEGER
AUTOINCREMENT (FTS5 requirement); a new `doc_nodes.ulid TEXT UNIQUE` column
provides portable cross-cartridge identity. Cross-cartridge references go
through `ulid`, not `id`. Integer rowid is removed in v0.3.

### `extractions` — LLM artifacts

```sql
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0
    -- ... additional columns truncated in audit
);
```

**Extraction types** (observed): `claim`, `entity`, `summary`

**Known concern:** `confidence` column is effectively hardcoded in current
builder (0.85 for claims/entities, 0.9 for summaries). The column exists
but carries no information. See SPEC-003 (pending).

**v0.2 additions** (per SPEC-001, accepted): `anchor_status TEXT CHECK (...)
DEFAULT 'unknown'` and `anchor_reason TEXT` — classify orphan claims into
`anchored | synthesized | match_failed | filtered | unknown`.

### `claim_sources` — claim-to-source anchoring

```sql
CREATE TABLE claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);
```

**Design intent:** many-to-many bridge from extracted claims to their source
sentences/paragraphs.

**Observed behavior:** 1:1 in practice — current builder anchors each claim
to a single source node. See SPEC-001 (orphan claims) and future SPEC on
multi-source anchoring.

### `embeddings` — vector blobs

```sql
CREATE TABLE embeddings (
    node_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (node_id, level)
    -- FOREIGN KEY likely to doc_nodes(id), truncated in audit
);
```

**Levels** (observed): `paragraph`, `section`

**Vector format:** raw float32 bytes. For `all-MiniLM-L6-v2` with
`embedding_dim=384`, each blob is 1536 bytes (384 × 4).

**Consumer contract:** readers MUST validate `length(vector) ==
embedding_dim * 4` before interpretation. Model-specific.

### `nodes_fts` — full-text search

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'
);
```

External content FTS5 table. Index is synced to `doc_nodes.content` via
triggers (truncated in audit — confirm trigger definitions).

**Shadow tables** (created by FTS5, not directly queried):
- `nodes_fts_data`
- `nodes_fts_idx`
- `nodes_fts_docsize`
- `nodes_fts_config`

**SPEC-002 interaction note:** FTS5 external content mode requires
`content_rowid` to be an INTEGER. SPEC-002 (accepted) preserves the integer
rowid on `doc_nodes` and adds a `ulid` column alongside, keeping FTS5 fully
compatible. See `05_Reference/SQLite_Research.md` Topic 7.

### `sqlite_sequence` — AUTOINCREMENT tracking

Created automatically by SQLite. Tracks next rowid for AUTOINCREMENT tables.
Not a concern but worth noting — its presence means the format depends on
AUTOINCREMENT behavior. SPEC-002 (accepted) adds ULID columns alongside in
v0.2; the v0.3 removal phase eliminates AUTOINCREMENT entirely.

---

## Build pipeline (v0.1)

```
source file (.md/.pdf)
    ↓ parse
doc_nodes (hierarchical)
    ↓ optional LLM pass (Haiku)
extractions + claim_sources
    ↓ optional embedding pass (MiniLM)
embeddings
    ↓
.lun file (SQLite)
```

CLI:
```
python -m luna.cartridge.builder input.md [output.lun] [--no-extract] [--no-embed]
```

**v0.2 additions to finalization** (per SPEC-006, accepted):
- `PRAGMA optimize` — ANALYZE-equivalent
- `PRAGMA wal_checkpoint(TRUNCATE)` — fold WAL into main file
- `PRAGMA journal_mode = DELETE` — shipping mode, no sidecar files
- `VACUUM` — defragment

---

## Read path (v0.1)

1. `register_cartridge()` ingests `.lun` read-only into collection tables
   (aibrarian_engine.py line 2440)
2. `resolve_source_ref()` looks up provenance from a node back to section
   path and claims (init.py line 26)

**v0.2 read-open pattern** (per SPEC-006):
- Connect with `?mode=ro` URI flag
- Set `PRAGMA query_only = 1`, `PRAGMA mmap_size = 268435456`, `PRAGMA cache_size = -8000`
- Validate `application_id` matches family; refuse otherwise

---

## Known v0.1 limitations

Status as of 2026-05-10 in brackets:

1. **No annotations** — file is a read artifact, cannot accumulate community input [SPEC-005 planned]
2. **No access log** — no record of who has read or queried a cartridge [future spec]
3. **No contracts** — no declared rules about permissible use [future spec]
4. **No ledger** — no append-only history of events [SPEC-005 planned]
5. **No integrity chain** — `source_hash` exists but no ongoing chain of trust [SPEC-005 planned]
6. **Confidence is a constant** — column carries no information [SPEC-003 pending]
7. **Integer IDs not portable** — AUTOINCREMENT breaks cross-cartridge references [SPEC-002 accepted]
8. **No `application_id`** — cannot distinguish `.lun` from other SQLite files without reading the schema [SPEC-006 accepted]
9. **No multi-axis weights** — single `confidence` column conflates multiple dimensions [SPEC-004 planned]
10. **Title and meta values not validated** — garbage meta survives to production [SPEC-006 accepted]

---

## Versioning policy

- **Major version (v1.x → v2.x):** breaking changes; old readers cannot open
- **Minor version (v0.1 → v0.2):** additive only; old readers open by ignoring
  additions

**v0.1 (this document):** Current `schema_version` in meta is `1` (integer).
No `application_id` or `user_version` pragmas set.

**v0.2 (in development, see SPEC-006 in `01_Specs/accepted/`):**
`meta.format_version` is a semver string (`'0.2'`); `PRAGMA user_version` is
the integer mirror (`2`); `PRAGMA application_id` is set to the family value
(`0x4C554E43` for cartridge). The reader trusts `user_version` as the binding
source of truth; `meta.format_version` is human-readable documentation.

---

## Validation checklist

A v0.1 cartridge is valid if:

- [ ] SQLite file opens without errors
- [ ] `meta` table exists with all required keys
- [ ] `meta.schema_version = 1`
- [ ] `meta.node_count = SELECT count(*) FROM doc_nodes`
- [ ] All `doc_nodes.parent_id` either NULL or valid reference
- [ ] All `claim_sources.claim_id` references valid `extractions.id`
- [ ] All `claim_sources.node_id` references valid `doc_nodes.id`
- [ ] All `embeddings.vector` have length `embedding_dim * 4`
- [ ] `nodes_fts` count matches eligible `doc_nodes` count
- [ ] `PRAGMA integrity_check` returns `ok`
- [ ] `PRAGMA foreign_key_check` returns empty

**v0.2 adds** (per SPEC-006):
- [ ] `PRAGMA application_id = 0x4C554E43`
- [ ] `PRAGMA user_version` is set and in supported range
- [ ] `meta.format_version` parses to an integer matching `user_version`
- [ ] `meta.cartridge_kind` ∈ `{'knowledge'}` (`SUPPORTED_CARTRIDGE_KINDS`)
- [ ] `meta.source_path` is absent (replaced by `meta.source_filename`)
- [ ] Title passes parser-artifact validation
- [ ] `PRAGMA journal_mode = 'delete'`; no `-wal` or `-shm` sidecar files

A `lun fsck` tool should implement these checks.
