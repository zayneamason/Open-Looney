# SPEC-002: Portable Identifiers for Cross-Cartridge References

**Status:** implemented
**Severity:** high
**Author:** Ahab (with Cowork)
**Created:** 2026-05-10
**Last updated:** 2026-05-21 (Phase 5 shipped; moved to implemented/)
**Affects format version:** v0.2 (additive), v0.3 (removal — separate spec)

---

## Problem statement

Every table in the v0.1 `.lun` cartridge that carries identity across a cartridge boundary
uses `INTEGER PRIMARY KEY AUTOINCREMENT`. AUTOINCREMENT is a per-database counter — `doc_nodes`
in `cartridge_A.lun` and `doc_nodes` in `cartridge_B.lun` start at 1 and increment
independently. When the governance model requires a claim in cartridge A to be referenced
by an annotation in cartridge B, or when two cartridges are merged into a single corpus,
integer IDs from different files collide by design. The format has no stable cross-database
identity layer. Until that layer exists, SPEC-005 (annotation ledger) cannot proceed: an
annotation that says `node_id = 42` means nothing outside the cartridge it was written
against.

## Observed evidence

From `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`, Finding S-01:

> AUTOINCREMENT integer primary keys on `doc_nodes` and `extractions` mean node IDs are
> not portable across cartridges. If two `.lun` files are merged or cross-reference each
> other, ID collisions are guaranteed. For the governance model to work, external references
> need stable identity (content hash or UUID).

The audit schema dump confirms both tables use `INTEGER PRIMARY KEY AUTOINCREMENT`:

```
doc_nodes   → id INTEGER PRIMARY KEY AUTOINCREMENT
extractions → id INTEGER PRIMARY KEY AUTOINCREMENT
sqlite_sequence → created automatically; tracks next AUTOINCREMENT value per table
```

`sqlite_sequence` in the schema dump is the runtime artefact of this design: a table
that only exists because AUTOINCREMENT is in use, and whose values reset to zero on
each fresh cartridge build.

`05_Reference/SQLite_Research.md`, Topic 2 documents the identity failure mode in full:
a standard rowid table with a text UNIQUE column requires two B-tree lookups per read
(integer index, then main table), stores the text twice, and still doesn't solve
cross-boundary identity unless the text value itself is globally unique.

## Root cause analysis

Three independent causes converge on the same symptom:

1. **AUTOINCREMENT is a per-database counter.** `sqlite_sequence` initialises at 1 for each
   new database. There is no inter-database coordination, no namespace, no uniqueness
   guarantee beyond the file boundary.

2. **The format has no identity layer separate from storage order.** The integer `id` column
   serves two purposes — storage row identity (what SQLite uses internally) and application
   identity (what code passes around when referring to a node). These should be distinct.
   Storage identity can stay integer (efficient, FTS5-compatible); application identity needs
   to be globally unique.

3. **No spec-time decision was made about cross-cartridge references.** v0.1 was built as
   a single-file read artefact. The governance arc (SPEC-005: annotations that travel across
   cartridges) was not in scope at build time. The identity shortcut was locally correct;
   it became globally wrong when the scope expanded.

## Proposed solution

### Design decisions (settled — do not re-litigate)

The following decisions were made prior to this spec and are documented here as constraints,
not proposals.

**D1. ULID as TEXT, not BLOB.** 26-char Crockford Base32, uppercase canonical form, stored
as `TEXT` in SQLite. Human-readable in the `sqlite3` CLI and any SQL GUI. No binary encoding
dependencies. Validate format at the application layer using the regex
`^[0-9A-HJKMNP-TV-Z]{26}$`; reject malformed values at insert time. Per the ULID spec,
batch generators must produce strictly increasing values within the same millisecond
timestamp (monotonic sub-ms counter).

**D2. `doc_nodes` keeps its INTEGER rowid; adds a `ulid` column for portable identity.**
FTS5 external content mode (`nodes_fts USING fts5(..., content='doc_nodes', content_rowid='id')`)
requires `content_rowid` to be an INTEGER rowid. Switching `doc_nodes` to WITHOUT ROWID
would break `nodes_fts` and require re-architecting the full-text search layer
(`05_Reference/SQLite_Research.md`, Topic 7). Not worth it. `doc_nodes.id` stays
as `INTEGER PRIMARY KEY AUTOINCREMENT`; a new `doc_nodes.ulid TEXT UNIQUE NOT NULL` column
carries the portable identity. All cross-cartridge references go through `ulid`, not `id`.

**D3. `extractions` migrates to ULID PK in a WITHOUT ROWID table — in v0.3.** No FTS5
dependency, no other constraint forcing rowid retention. In v0.2, `extractions` also adds
a `ulid TEXT UNIQUE NOT NULL` shadow column (same pattern as `doc_nodes`). The WITHOUT ROWID
conversion is deferred to v0.3 to avoid a breaking migration in the same release as D2.

**D4. Reference tables get ULID shadow columns in v0.2.** `claim_sources`, `claim_context_nodes`
(from SPEC-001), and `embeddings` each get new TEXT columns mirroring their integer FK
references. The integer composite PKs remain live in v0.2. In v0.3, these shadow columns
become the composite PK.

**D5. Two-phase migration.** v0.2 (this spec) is additive: ULID columns added alongside
integer columns. Both are written by the builder. The integer columns remain the live PK.
v0.3 (separate spec) drops the integer columns and fully converts to ULID-primary tables.
This staging avoids codebase-wide coordination inside a single release.

**D6. ULID generation happens in Python at build/migration time.** SQLite has no native ULID
function. Generate at insert or migration time, never at read time. The migration tool
assigns ULIDs in `ORDER BY id` with strictly increasing timestamps to preserve a sense of
temporal sequence from the original build order.

**D7. Cross-cartridge references are application-enforced, not FK-enforced.** SQLite FK
constraints do not work across attached databases (`05_Reference/SQLite_Research.md`, Topic 6).
Application code is responsible for resolving cross-cartridge ULID references and handling
missing references gracefully. This is a SQLite structural property, not a spec limitation.

---

### Schema changes (v0.2 additive phase)

#### `doc_nodes` — add portable identity column

```sql
-- Step 1: add column without UNIQUE (O(1) — schema text change only)
ALTER TABLE doc_nodes ADD COLUMN ulid TEXT;

-- Step 2: populate (migration tool, one UPDATE per row, described in Migration mechanics)

-- Step 3: add UNIQUE index after population (O(n) — one-time index build)
CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
```

Post-migration, the effective schema for `doc_nodes` is:

```sql
CREATE TABLE doc_nodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,   -- integer rowid; FTS5 anchor; stays
    parent_id INTEGER,
    type      TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    -- ... existing columns unchanged ...
    ulid      TEXT NOT NULL,                       -- ULID v0.2+; portable cross-cartridge identity
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);

CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
CREATE INDEX idx_doc_nodes_parent ON doc_nodes(parent_id);   -- existing, unchanged
CREATE INDEX idx_doc_nodes_type   ON doc_nodes(type);        -- existing, unchanged
```

`nodes_fts` is unaffected: it uses `content_rowid='id'`, which remains the integer rowid.
The FTS shadow tables (`nodes_fts_data`, `nodes_fts_idx`, `nodes_fts_docsize`, `nodes_fts_config`)
do not change.

#### `extractions` — add portable identity column (v0.2 shadow; v0.3 PK)

```sql
-- Step 1: add column without UNIQUE (O(1))
ALTER TABLE extractions ADD COLUMN ulid TEXT;

-- Step 2: populate (migration tool)

-- Step 3: add UNIQUE index after population (O(n))
CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);
```

Post-migration effective schema:

```sql
CREATE TABLE extractions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,  -- integer rowid; stays through v0.2
    type           TEXT NOT NULL,
    content        TEXT NOT NULL,
    confidence     REAL DEFAULT 1.0,
    anchor_status  TEXT CHECK (anchor_status IN (      -- from SPEC-001
                       'anchored', 'synthesized', 'match_failed', 'filtered', 'unknown'
                   )) DEFAULT 'unknown',
    anchor_reason  TEXT,                               -- from SPEC-001
    ulid           TEXT NOT NULL,                      -- ULID v0.2+
    CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*')
);

CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);
```

The v0.3 target schema (full spec deferred) will be:

```sql
CREATE TABLE extractions (
    ulid          TEXT PRIMARY KEY,   -- was: id INTEGER PRIMARY KEY AUTOINCREMENT
    type          TEXT NOT NULL,
    content       TEXT NOT NULL,
    confidence    REAL DEFAULT 1.0,
    anchor_status TEXT CHECK (anchor_status IN (
                      'anchored', 'synthesized', 'match_failed', 'filtered', 'unknown'
                  )) DEFAULT 'unknown',
    anchor_reason TEXT,
    CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*')
) WITHOUT ROWID;
```

WITHOUT ROWID is deferred to v0.3 because adding it to an existing table requires a full
table rewrite, and doing that in the same migration pass that introduces ULIDs would create
an unnecessarily complex single-phase migration. Add the column now; convert the table
structure in a future release when readers are ULID-aware.

#### `claim_sources` — add ULID shadow columns

`claim_sources` also carries SPEC-001 provenance columns (`anchor_method`, `anchored_by`,
`anchored_at`, `event_id`). Those columns are unchanged. Only ULID shadows are added here.

```sql
-- O(1) — no constraints, nullable, old rows default to NULL
ALTER TABLE claim_sources ADD COLUMN claim_ulid TEXT;
ALTER TABLE claim_sources ADD COLUMN node_ulid  TEXT;
```

Post-migration effective schema:

```sql
CREATE TABLE claim_sources (
    claim_id      INTEGER NOT NULL,      -- integer FK; stays through v0.2
    node_id       INTEGER NOT NULL,      -- integer FK; stays through v0.2
    anchor_method TEXT NOT NULL DEFAULT 'auto'
                  CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by   TEXT,
    anchored_at   INTEGER,
    event_id      TEXT,
    claim_ulid    TEXT,                  -- ULID mirror of claim_id (v0.2+)
    node_ulid     TEXT,                  -- ULID mirror of node_id (v0.2+)
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id)  REFERENCES doc_nodes(id)
);
```

The v0.3 composite PK will be `(claim_ulid, node_ulid)` with FK references to
`extractions(ulid)` and `doc_nodes(ulid)`. SQLite allows FK references to any UNIQUE column,
not just PRIMARY KEY, so the FK target on `doc_nodes.ulid` and `extractions.ulid` is valid
once those columns have their UNIQUE index.

#### `claim_context_nodes` — add ULID shadow columns

```sql
-- O(1) — nullable, no constraints on new columns
ALTER TABLE claim_context_nodes ADD COLUMN claim_ulid TEXT;
ALTER TABLE claim_context_nodes ADD COLUMN node_ulid  TEXT;
```

Post-migration effective schema:

```sql
CREATE TABLE claim_context_nodes (
    claim_id   INTEGER NOT NULL,
    node_id    INTEGER NOT NULL,
    relevance  REAL NOT NULL,
    claim_ulid TEXT,   -- ULID mirror of claim_id (v0.2+)
    node_ulid  TEXT,   -- ULID mirror of node_id (v0.2+)
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id)  REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);
```

#### `embeddings` — add ULID shadow column

```sql
-- O(1)
ALTER TABLE embeddings ADD COLUMN node_ulid TEXT;
```

Post-migration effective schema:

```sql
CREATE TABLE embeddings (
    node_id    INTEGER NOT NULL,
    level      TEXT    NOT NULL,
    vector     BLOB    NOT NULL,
    node_ulid  TEXT,              -- ULID mirror of node_id (v0.2+)
    PRIMARY KEY (node_id, level)
    -- FOREIGN KEY (node_id) REFERENCES doc_nodes(id) — assumed present
);
```

---

### Behavioral changes

**Builder (`src/luna/cartridge/builder.py`):**

1. At `doc_nodes` insert time, generate a ULID and write it alongside the integer rowid.
   The ULID must be generated monotonically: if multiple rows are inserted in a loop, each
   call to the ULID generator must produce a value greater than the previous one.
2. At `extractions` insert time, same pattern.
3. At `claim_sources` insert time, look up the ULID for `claim_id` and `node_id` and write
   them to `claim_ulid` and `node_ulid` in the same INSERT.
4. Same for `claim_context_nodes` and `embeddings`.
5. Call `validate_ulids()` (see Validation rules) as a build-time check before finalizing
   the cartridge.

**Reader (`src/luna/cartridge/reader.py`):**

For cross-cartridge use cases, resolve references via `ulid`, not `id`. The `id` column
remains valid for single-cartridge internal navigation (e.g., tree traversal through
`parent_id`, FTS5 rowid lookups). A convenience on `user_version` (per SPEC-006) determines
the operating mode:

```python
def get_id_mode(conn) -> str:
    """Returns 'ulid' if cartridge has ULID columns, 'integer' for v0.1 fallback."""
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    return "ulid" if user_ver >= 2 else "integer"
```

See Open Questions (Q5) for the full compatibility shim specification.
**Note (2026-05-22):** Q5's integer-only-mode fallback is RETIRED. The function above
remains in the codebase as historical documentation of the original Phase 3 widening
(see implementation log entry 16), but v0.2 readers MUST NOT use the `'integer'` branch
for new code paths — v0.1 cartridges are now strictly rejected with a migrate-first
error. See the Q5 RETIRED block for rationale.

### Migration path

**Phase 1 — v0.2 (this spec): additive.**

New cartridges built with the v0.2 builder get ULID columns from birth. Existing
`PRIESTS_AND_PROGRAMMERS_Lansing.lun` is rebuilt from source per SPEC-006 (cartridges are
deterministic from source), so it gets ULID columns in the rebuild.

The migration tool (`lun migrate v1-to-v2`) applies to any cartridge with `user_version = 1`
that must be migrated in place rather than rebuilt:

1. ADD COLUMN `ulid TEXT` on `doc_nodes` and `extractions` (O(1) each).
2. Populate `doc_nodes.ulid` in a single `BEGIN EXCLUSIVE` transaction:
   generate ULIDs in ascending `id` order, assign strictly increasing ULID timestamps.
3. Create `UNIQUE INDEX uq_doc_nodes_ulid` (O(n), builds the index; all values are populated
   and unique, so no violations).
4. Populate `extractions.ulid` in the same transaction; create `uq_extractions_ulid`.
5. ADD COLUMN `claim_ulid`, `node_ulid` on `claim_sources`, `claim_context_nodes`, and
   `node_ulid` on `embeddings` (O(1) each).
6. Populate the shadow columns on reference tables by joining to the already-populated
   `doc_nodes.ulid` and `extractions.ulid`.
7. Apply SPEC-001 migration if not already applied (anchor columns on `extractions` and
   `claim_sources`; `claim_context_nodes` table). SPEC-001 and SPEC-002 migrations are
   designed to be independent and composable.
8. Bump `PRAGMA user_version = 2` and `UPDATE meta SET value='0.2' WHERE key='format_version'`
   inside the same transaction.

After this migration, the cartridge is a valid v0.2 file. Integer columns are present and
live; ULID columns are populated and indexed.

**Phase 2 — v0.3 (deferred spec): removal.**

The integer columns (`doc_nodes.id` / AUTOINCREMENT, `extractions.id` / AUTOINCREMENT)
are dropped. `extractions` is rebuilt as WITHOUT ROWID with ULID PK. Reference table
composite PKs switch to `(claim_ulid, node_ulid)`. The v0.3 spec covers this in full.

---

## Migration mechanics (SQLite-specific)

Cross-reference: `05_Reference/SQLite_Research.md`, Topic 5.

The three-step pattern for any new ULID column that needs a UNIQUE constraint:

```sql
-- Step 1: ADD COLUMN — O(1), no constraint, nullable
ALTER TABLE doc_nodes ADD COLUMN ulid TEXT;

-- Step 2: POPULATE — O(n), one UPDATE per row (or batched)
-- Do this in the migration script, not here.

-- Step 3: CREATE INDEX — O(n), one-time index build after data is populated
-- This validates uniqueness. All values are builder-generated ULIDs, so no violations.
CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
```

Why not `ALTER TABLE doc_nodes ADD COLUMN ulid TEXT UNIQUE`? That form is also O(n) — SQLite
must scan all existing rows to build the index, and in this case the column just added has
`NULL` for all existing rows, which means the UNIQUE constraint fails immediately (multiple
NULLs are treated as distinct by UNIQUE, so that actually passes — but the column has no
useful values yet). The three-step pattern makes intent explicit and works correctly on all
SQLite versions ≥ 3.37.

For the shadow columns on reference tables (`claim_sources.claim_ulid`, etc.):

```sql
-- O(1) — nullable, no index needed in v0.2 (integer PK is the primary access path)
ALTER TABLE claim_sources ADD COLUMN claim_ulid TEXT;
ALTER TABLE claim_sources ADD COLUMN node_ulid  TEXT;
```

No UNIQUE index on shadow columns in reference tables. In v0.3, when these columns become
the composite PK, the index is implicit in the WITHOUT ROWID structure.

The full migration in Python:

> **NOTE (Phase 3.5 lesson):** The example below uses `ts << 16 | counter` as a sub-ms
> monotonicity sketch. This is **non-canonical** — it overflows the 48-bit timestamp field
> and produces first chars in `[G-Z]` for current dates, which strict ULID parsers reject as
> overflow. The canonical generator (48-bit ts + 80-bit random, monotonic via random
> increment within same ms) lives in `src/luna/cartridge/builder.py::ULIDGenerator` and is
> the authoritative form. See `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE3_5_CANONICAL_ULID.md`
> for the root cause analysis. The example below is preserved for historical context;
> do not copy it into new implementations.

```python
import sqlite3
import time

ULID_EPOCH_MS = 0  # milliseconds since Unix epoch

def generate_ulid(ts_ms: int, counter: int) -> str:
    """
    Minimal ULID generator. ts_ms = Unix timestamp in ms, counter = sub-ms sequence.
    Returns 26-char Crockford Base32 string.
    Caller ensures ts_ms is non-decreasing and counter resets per ts_ms.
    """
    CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    # 10 chars timestamp (48 bits), 16 chars random (80 bits)
    # For migration: use (ts_ms << 16 | counter) as the full 48-bit timestamp value
    # to produce strictly increasing ULIDs.
    ts = (ts_ms << 16) | (counter & 0xFFFF)
    rand = __import__('os').urandom(10)
    rand_int = int.from_bytes(rand, 'big')

    val = (ts << 80) | rand_int
    result = []
    for _ in range(26):
        result.append(CROCKFORD[val & 0x1F])
        val >>= 5
    return ''.join(reversed(result))


def migrate_v1_to_v2(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # suspend FK checks during migration

    with conn:  # BEGIN / COMMIT
        conn.execute("BEGIN EXCLUSIVE")

        # 1. ADD COLUMN (O(1) each)
        for sql in [
            "ALTER TABLE doc_nodes       ADD COLUMN ulid       TEXT",
            "ALTER TABLE extractions     ADD COLUMN ulid       TEXT",
            "ALTER TABLE claim_sources   ADD COLUMN claim_ulid TEXT",
            "ALTER TABLE claim_sources   ADD COLUMN node_ulid  TEXT",
            "ALTER TABLE claim_context_nodes ADD COLUMN claim_ulid TEXT",
            "ALTER TABLE claim_context_nodes ADD COLUMN node_ulid  TEXT",
            "ALTER TABLE embeddings      ADD COLUMN node_ulid  TEXT",
        ]:
            conn.execute(sql)

        # 2. Populate doc_nodes.ulid in id order (preserves temporal sequence)
        ts_base = int(time.time() * 1000)
        rows = conn.execute("SELECT id FROM doc_nodes ORDER BY id").fetchall()
        for i, (row_id,) in enumerate(rows):
            ulid = generate_ulid(ts_base, i)
            conn.execute(
                "UPDATE doc_nodes SET ulid = ? WHERE id = ?", (ulid, row_id)
            )
        conn.execute(
            "CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid)"
        )

        # 3. Populate extractions.ulid in id order
        rows = conn.execute("SELECT id FROM extractions ORDER BY id").fetchall()
        for i, (row_id,) in enumerate(rows):
            ulid = generate_ulid(ts_base + 1, i)  # +1ms offset avoids collision with nodes
            conn.execute(
                "UPDATE extractions SET ulid = ? WHERE id = ?", (ulid, row_id)
            )
        conn.execute(
            "CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid)"
        )

        # 4. Populate shadow columns on reference tables via join
        conn.execute("""
            UPDATE claim_sources
            SET claim_ulid = (SELECT ulid FROM extractions WHERE id = claim_sources.claim_id),
                node_ulid  = (SELECT ulid FROM doc_nodes   WHERE id = claim_sources.node_id)
        """)
        conn.execute("""
            UPDATE claim_context_nodes
            SET claim_ulid = (SELECT ulid FROM extractions WHERE id = claim_context_nodes.claim_id),
                node_ulid  = (SELECT ulid FROM doc_nodes   WHERE id = claim_context_nodes.node_id)
        """)
        conn.execute("""
            UPDATE embeddings
            SET node_ulid = (SELECT ulid FROM doc_nodes WHERE id = embeddings.node_id)
        """)

        # 5. Bump version (SPEC-006 contract)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            "UPDATE meta SET value = '0.2' WHERE key = 'format_version'"
        )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
```

No `_migration_log` table is created. See Open Questions (Q4) for the reasoning.

---

## Validation rules

### Build time

Runs in `validate_ulids(conn)` before cartridge finalization. Called from the builder
after all inserts and before the pragma finalization stack (per SPEC-006).

```python
import re

ULID_RE = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')

def validate_ulids(conn) -> None:
    """
    Verify every ULID column is populated, correctly formatted, and unique.
    Called at build time and during lun fsck.
    Raises BuildError on any violation.
    """
    checks = [
        ("doc_nodes",           "ulid",       "id"),
        ("extractions",         "ulid",       "id"),
        ("claim_sources",       "claim_ulid", "claim_id"),
        ("claim_sources",       "node_ulid",  "claim_id"),
        ("claim_context_nodes", "claim_ulid", "claim_id"),
        ("claim_context_nodes", "node_ulid",  "claim_id"),
        ("embeddings",          "node_ulid",  "node_id"),
    ]

    for table, ulid_col, pk_col in checks:
        # NULL check — every ULID column must be populated after migration
        nulls = conn.execute(
            f"SELECT {pk_col} FROM {table} WHERE {ulid_col} IS NULL"
        ).fetchall()
        if nulls:
            raise BuildError(
                f"{table}.{ulid_col} is NULL on {len(nulls)} rows. "
                f"All rows must have a ULID after v0.2 migration."
            )

        # Format check — Python regex is authoritative; SQL GLOB is a write gate only
        bad_format = [
            row for row in conn.execute(
                f"SELECT {ulid_col} FROM {table}"
            ).fetchall()
            if not ULID_RE.match(row[0])
        ]
        if bad_format:
            raise BuildError(
                f"{table}.{ulid_col} has {len(bad_format)} malformed ULID(s). "
                f"Expected 26-char Crockford Base32 matching ^[0-9A-HJKMNP-TV-Z]{{26}}$"
            )

    # Uniqueness sanity (belt-and-suspenders; UNIQUE index should catch this earlier)
    for table, ulid_col in [("doc_nodes", "ulid"), ("extractions", "ulid")]:
        dupes = conn.execute(
            f"SELECT {ulid_col}, count(*) c FROM {table} "
            f"GROUP BY {ulid_col} HAVING c > 1"
        ).fetchall()
        if dupes:
            raise BuildError(
                f"{table}.{ulid_col} has duplicate values: {dupes[:5]}"
            )

    # Cross-reference integrity: shadow columns on reference tables must match
    bad_claim_ref = conn.execute("""
        SELECT cs.claim_id, cs.claim_ulid, e.ulid
        FROM claim_sources cs
        JOIN extractions e ON e.id = cs.claim_id
        WHERE cs.claim_ulid != e.ulid
    """).fetchall()
    if bad_claim_ref:
        raise BuildError(
            f"claim_sources.claim_ulid mismatch on {len(bad_claim_ref)} rows: "
            f"shadow ULID does not match extractions.ulid for the same claim_id."
        )

    bad_node_ref = conn.execute("""
        SELECT cs.node_id, cs.node_ulid, dn.ulid
        FROM claim_sources cs
        JOIN doc_nodes dn ON dn.id = cs.node_id
        WHERE cs.node_ulid != dn.ulid
    """).fetchall()
    if bad_node_ref:
        raise BuildError(
            f"claim_sources.node_ulid mismatch on {len(bad_node_ref)} rows."
        )
```

### Read time (`lun fsck`)

Same checks as build time, plus format regex on every ULID value. The fsck check function:

```python
def fsck_ulids(conn) -> list[str]:
    """
    Returns a list of violation strings. Empty list = pass.
    Suitable for lun fsck output.
    """
    violations = []
    ULID_RE = re.compile(r'^[0-9A-HJKMNP-TV-Z]{26}$')

    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_ver < 2:
        violations.append(
            f"user_version={user_ver}: ULID columns not expected on v0.1 cartridges. "
            f"Run 'lun migrate v1-to-v2' before fsck."
        )
        return violations

    checks = [
        ("doc_nodes",           "ulid",       "id"),
        ("extractions",         "ulid",       "id"),
        ("claim_sources",       "claim_ulid", "claim_id"),
        ("claim_sources",       "node_ulid",  "claim_id"),
        ("claim_context_nodes", "claim_ulid", "claim_id"),
        ("claim_context_nodes", "node_ulid",  "claim_id"),
        ("embeddings",          "node_ulid",  "node_id"),
    ]

    for table, ulid_col, pk_col in checks:
        nulls = conn.execute(
            f"SELECT count(*) FROM {table} WHERE {ulid_col} IS NULL"
        ).fetchone()[0]
        if nulls:
            violations.append(f"{table}.{ulid_col}: {nulls} NULL values")

        bad_fmt = conn.execute(
            f"SELECT count(*) FROM {table} "
            f"WHERE {ulid_col} IS NOT NULL "
            f"AND (length({ulid_col}) != 26 "
            f"     OR upper({ulid_col}) != {ulid_col})"
        ).fetchone()[0]
        if bad_fmt:
            violations.append(f"{table}.{ulid_col}: {bad_fmt} malformed ULID(s)")

    return violations
```

---

## Governance implications

**SPEC-005 (annotation ledger) is directly unblocked by this spec.** An annotation entry
that says "claim `01HQZXYZABCDE...` from cartridge `PRIESTS_AND_PROGRAMMERS_Lansing.lun`
was contested by ambassador `amb_0012`" requires stable cross-cartridge claim identity.
Without this spec, the ledger entry can only reference `extractions.id = 42`, which is
meaningless outside the file it was written against.

ULID's temporal ordering gives the annotation ledger a free partial ordering: a claim
with ULID `01HQ...` was created before a claim with ULID `01HR...`. The ledger can use
this as a tiebreaker when reconciling concurrent annotations.

**Cross-cartridge traversal via `ATTACH DATABASE`** (`05_Reference/SQLite_Research.md`,
Topic 6): when the plug-in collection system attaches multiple cartridges, JOIN conditions
across attached schemas can use ULID values as the join key:

```sql
ATTACH DATABASE 'cartridgeB.lun' AS b;

SELECT a_claims.ulid, b_annot.body
FROM main.extractions a_claims
JOIN b.annotation_targets b_annot
    ON b_annot.claim_ulid = a_claims.ulid;
```

This works because text equality comparison is universal across attached databases. FK
enforcement across attached databases is not available (D7); the application layer must
validate that `b_annot.claim_ulid` exists in `a_claims.ulid` before treating the join
result as trusted.

**Memory Matrix integration:** The runtime matrix (`memory_matrix.lun`, `LUNM` family,
SPEC-006) references cartridge content when it ingests claims into `memory_nodes`. Currently
it stores integer `node_id` references. After this spec lands, the matrix ingestion path
should store `node_ulid` alongside (or instead of) the integer reference, so that memory
nodes can be traced back to their cartridge source even if the cartridge is rebuilt (new
integer IDs, same ULID).

---

## Alternatives considered

The following were evaluated against the research in `05_Reference/SQLite_Research.md`,
Topic 2.

**Alt 1: UUID v4 (random) as TEXT.** Rejected. UUID v4 is randomly ordered. Inserting
random-order TEXT values into a WITHOUT ROWID table with TEXT PRIMARY KEY causes a B-tree
page split on nearly every insert (random key lands in the middle of an existing B-tree
page). For a cartridge with 4000+ `doc_nodes` rows, this degrades insert performance
significantly and leaves the B-tree fragmented. Time-ordered IDs cluster writes at the end
of the B-tree, which is optimal.

**Alt 2: UUID v7 (time-ordered, RFC 9562).** Viable but not chosen. UUIDv7 has the same
time-ordering and global uniqueness properties as ULID. The difference: ULID encodes as
26 chars of Crockford Base32; UUIDv7 encodes as 36 chars of dashed hex. In a WITHOUT ROWID
TEXT PK B-tree, the 26 vs. 36 char difference affects intermediate node fan-out. ULID is
more compact and more legible in a SQLite CLI without any decoding. Both choices are
technically sound; ULID is the cleaner pick for a SQLite-native format.

**Alt 3: Content hash (SHA-256, Git-style).** Rejected. Content addressing conflates content
with identity. Two annotations with identical text (e.g., two different ambassadors making
the same observation independently) are distinct rows with distinct identities — they should
not share a key. Content hashing is correct for immutable artifact storage (Fossil's `blob`
table) where deduplication is the point. For records with identity beyond their content,
it is wrong. Content hashing is used inside the format — as `entry_hash` in the SPEC-005
annotation ledger chain — but not as a row PK.

**Alt 4: Keep INTEGER PKs, add a separate UUID lookup table.** Rejected. This doubles the
schema surface area (every table that needs portable identity now has two tables), requires
an extra join on every cross-cartridge reference, and creates a new category of inconsistency
(UUID table out of sync with integer PK table). Adding a ULID column directly to the
existing table is strictly simpler.

**Alt 5: Single-phase migration (drop integer columns immediately in v0.2).** Rejected.
Dropping `extractions.id` in v0.2 requires updating every code path that currently reads
or writes the integer `id` column — builder, reader, `aibrarian_engine.py`, test fixtures,
and any external scripts. Doing this simultaneously with introducing ULIDs creates a
coordination cliff. The two-phase approach lets ULID columns be introduced and validated
against real data in v0.2, while the integer columns are removed in v0.3 once all readers
have moved to ULID-first access patterns.

---

## Open questions

**Q1 — Index strategy for ULID columns in v0.2 (resolved here):**

`doc_nodes.ulid` and `extractions.ulid` require a `UNIQUE INDEX` because they are FK
targets for reference tables in v0.3. The UNIQUE index is created via `CREATE UNIQUE INDEX`
(not `ADD COLUMN ... UNIQUE`) to allow the three-step populate-then-index migration pattern.
Shadow columns on reference tables (`claim_sources.claim_ulid`, etc.) do not need explicit
indexes in v0.2 — the integer composite PK is the primary access path, and the shadow
columns are only queried for cross-cartridge use cases where the table is small enough
(1442 rows in `claim_sources` for the Lansing cartridge) that a scan is acceptable.
In v0.3, the shadow columns become the composite PK and gain implicit WITHOUT ROWID
indexing. **Recommendation: index only `doc_nodes.ulid` and `extractions.ulid` in v0.2.
Defer shadow-column indexes to v0.3.**

**Q2 — Validation regex and fsck check for malformed ULIDs (resolved here):**

The authoritative regex is `^[0-9A-HJKMNP-TV-Z]{26}$` (26 chars, Crockford Base32 alphabet:
0–9 and A–Z excluding I, L, O, U). The CHECK constraint on the schema is a write gate only:
`length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*'` (approximate; GLOB cannot enforce
exact length or full Crockford alphabet). The Python regex is the authoritative check and
runs at build time and in `lun fsck`. A cartridge with rows violating the Python regex but
passing the GLOB has been hand-edited or produced by a non-conformant tool; `fsck` flags
these as violations.

**Q3 — Whether to expose deprecated integer columns in `meta` (resolved here):**

Add `meta.deprecated_columns = 'doc_nodes.id,extractions.id'` in the v0.2 builder. This
gives external tools that have adapted to integer IDs a machine-readable signal to update.
The cost is one INSERT into `meta`. The benefit: `lun fsck` in a future release can warn
"this cartridge still has deprecated columns; run `lun migrate v2-to-v3` to remove them."
Dropping silently in v0.3 without any prior signal would be acceptable, but the meta marker
is cheap and the precedent (SPEC-006 used `meta` for human-readable version documentation)
supports it.

**SQL for the meta insert:**

```sql
INSERT OR IGNORE INTO meta (key, value)
VALUES ('deprecated_columns', 'doc_nodes.id,extractions.id');
```

**Q4 — Migration log: durable vs. ephemeral (resolved here):**

No `_migration_log` table in the cartridge. Reasoning: the rowid → ULID mapping is
deterministic from the migration run (generated in `ORDER BY id` with a fixed timestamp
base). If the mapping needs to be recovered, re-run the migration on a fresh copy of the
v0.1 cartridge — it produces the same output. A persistent migration log in the cartridge
adds schema noise visible to every reader and has no ongoing operational value after the
migration commits. If a forensic audit trail is needed, the migration tool should write a
CSV or JSON log to disk (external to the `.lun` file) before the transaction commits.
**Recommendation: no `_migration_log` table; ephemeral mapping only, with optional external
log written by the migration CLI.**

**Q5 — Behavior when a v0.1 cartridge is opened by a v0.2 reader:**

> **RETIRED 2026-05-22.** The integer-only-mode fallback specified below is superseded by
> strict v0.2-only reads. v0.2 readers MUST reject partially-migrated cartridges
> (`user_version = 1` + `application_id = 0x4C554E43`) with an `UnsupportedVersionError(1)`
> that includes the migration command in the error text. The Reader Prototype v0.2.0
> implements this strict-reject behavior and is treated as the canonical v0.2 reader.
>
> **Rationale.** The fallback added permanent reader-side complexity (dual identity-column
> code paths, conditional ULID handling, UI affordances for "you're reading a v0.1
> cartridge") to support a state the migration tool produces only transiently. The
> user-facing cost of "migrate first" is a single CLI invocation that runs in
> milliseconds; the engineering cost of supporting the fallback is permanent and
> compounds with every reader implementation. The format-spec contract is also cleaner:
> v0.2 cartridges have `user_version = 2`, period. Cross-reference: `LUN-FORMAT_v0.2.md`
> § "File identification" step 3 and § "Open contract" item 2, both amended 2026-05-22
> to cite this retirement.
>
> *Original Q5 text preserved below for historical record:*

> ~~SPEC-006's `validate_cartridge_for_read` already handles most of this: a v0.1 cartridge
> has `application_id = 0`, which triggers `WrongFamilyError` before ULID column presence
> is ever checked. The case where Q5 matters is a partially-migrated file: `application_id =
> 0x4C554E43` set manually but `user_version = 1` (ULID columns absent). In this case, the
> v0.2 reader sees `user_version = 1`, which is within `MIN_SUPPORTED_VERSION = 1`. **The
> reader MUST operate in integer-only mode rather than raising an error:** use `doc_nodes.id`
> and `extractions.id` as the identity columns, suppress ULID-dependent cross-cartridge
> features, and surface a notice: "This cartridge has not been migrated to v0.2. Run `lun
> migrate v1-to-v2` for full ULID-based cross-cartridge support." Requiring migration before
> opening is a worse UX for the common case (reading a single cartridge); the compatibility
> shim cost is a one-line version check in the reader.~~
>
> ~~Cross-reference: SPEC-006 `validate_cartridge_for_read`, `MIN_SUPPORTED_VERSION = 1`.~~

---

## Dependencies

**Must be accepted before this spec can be implemented:**

- **SPEC-006 (accepted)** — establishes `application_id`, `user_version`, and the read-open
  validation pattern. SPEC-002 builds on the version check (`user_version >= 2`) to determine
  ULID column presence and the compatibility shim.
- **SPEC-001 (accepted)** — establishes the `claim_sources` provenance columns
  (`anchor_method`, `anchored_by`, `anchored_at`, `event_id`) that SPEC-002's migration
  must preserve. The v0.2 migration for both specs must compose without conflict.

**This spec blocks:**

- **SPEC-005 (accepted 2026-05-21)** — annotation ledger requires stable cross-cartridge claim identity.
  Portable ULIDs are the prerequisite. No annotation event can safely reference a claim
  without a stable `claim_ulid`.
- **`LUN-FORMAT_v0.3.md` (active draft)** — drops integer columns and converts
  `extractions` to WITHOUT ROWID with ULID PK. This spec's v0.2 additive work
  is the prerequisite.

---

## Implementation notes

**Status:** in progress (Phase 3 of v0.2 spec arc — additive ULID columns)
**Implementer:** Claude (Opus 4.7) in coordination with Ahab
**Implementation date:** 2026-05-12 (in progress)
**Handoff:** `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE3_PORTABLE_IDS.md` (rev 2)
**Phase 2 baseline:** commit `c4f346e`

### Smoke evidence

#### 1. Pre-flight grep output (captured before any code changes)

```
$ grep -n "CREATE TABLE\|CREATE INDEX\|CREATE VIRTUAL TABLE\|CREATE TRIGGER" src/luna/cartridge/schema.py
12:CREATE TABLE IF NOT EXISTS meta (
18:CREATE TABLE IF NOT EXISTS doc_nodes (
29:CREATE TABLE IF NOT EXISTS extractions (
41:CREATE TABLE IF NOT EXISTS claim_sources (
56:CREATE TABLE IF NOT EXISTS claim_context_nodes (
67:CREATE TABLE IF NOT EXISTS embeddings (
76:CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
83:CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON doc_nodes BEGIN
88:CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON doc_nodes BEGIN
93:CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON doc_nodes BEGIN
104:CREATE TABLE IF NOT EXISTS nexus_refs (
113:CREATE INDEX IF NOT EXISTS idx_doc_nodes_parent ON doc_nodes(parent_id);
114:CREATE INDEX IF NOT EXISTS idx_doc_nodes_type ON doc_nodes(type);
115:CREATE INDEX IF NOT EXISTS idx_extractions_type ON extractions(type);
116:CREATE INDEX IF NOT EXISTS idx_claim_sources_node ON claim_sources(node_id);
117:CREATE INDEX IF NOT EXISTS idx_embeddings_level ON embeddings(level);
118:CREATE INDEX IF NOT EXISTS idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
120:CREATE INDEX IF NOT EXISTS idx_extractions_anchor_status ON extractions(anchor_status);
121:CREATE INDEX IF NOT EXISTS idx_claim_context_claim ON claim_context_nodes(claim_id);
122:CREATE INDEX IF NOT EXISTS idx_claim_context_node ON claim_context_nodes(node_id);

$ grep -rn "ulid\|ULID\|claim_ulid\|node_ulid" src/luna/cartridge/ src/luna/substrate/
(zero matches — clean starting state)

$ grep -n "INSERT INTO doc_nodes" src/luna/cartridge/builder.py
239:                "INSERT INTO doc_nodes (parent_id, type, position, content, meta_json) "

$ grep -n "INSERT INTO extractions" src/luna/cartridge/extractor.py
152:                    "INSERT INTO extractions (type, content, confidence, anchor_status) "
173:                    "INSERT INTO extractions (type, content, confidence) VALUES (?, ?, ?)",
202:                    "INSERT INTO extractions "

$ grep -n "INSERT.*claim_sources\|INSERT OR IGNORE INTO claim_sources" src/luna/cartridge/extractor.py
158:                    "INSERT INTO claim_sources "
240:                    "INSERT OR IGNORE INTO claim_sources "
254:                    "INSERT OR IGNORE INTO claim_sources "

$ grep -n "INSERT INTO embeddings\|INSERT OR IGNORE INTO embeddings" src/luna/cartridge/embedder.py
(zero matches — DISCREPANCY from handoff "expected 1 hit")

Investigation: the embedder uses INSERT OR REPLACE, not INSERT INTO or INSERT OR IGNORE.
Confirmed two INSERT sites:
  src/luna/cartridge/embedder.py:72-75   paragraph-level INSERT OR REPLACE INTO embeddings
  src/luna/cartridge/embedder.py:116-119 section-level   INSERT OR REPLACE INTO embeddings
Both sites need node_ulid threading. Plan adjusted: Step 5 updates BOTH sites identically.

$ grep -n "anchor_status.*anchor_reason\|c\[\"anchor_reason\"\]" src/luna/cartridge/__init__.py
130:        # the claims array. v0.1 cartridges lack anchor_status/anchor_reason
155:                SELECT e.id, e.content, e.confidence, e.anchor_status, e.anchor_reason
168:                    "anchor_reason": c["anchor_reason"],
238:        sql = "SELECT id, type, content, confidence, anchor_status, anchor_reason FROM extractions"

Investigation: handoff "expected 4 hits" was a grep-regex artefact. The pattern
"anchor_status.*anchor_reason" only matches lines that contain BOTH tokens. The v0.1
legacy branches in resolve_source_ref and list_extractions SYNTHESIZE anchor_status
(they don't SELECT it from a column that doesn't exist), so the regex skips them.
Structural assumption from the handoff is correct: both functions have both branches.
  resolve_source_ref: is_v01 at :89, v0.1 SELECT 133-141 + v0.1 dict 142-151,
                      v0.2 SELECT 153-161 + v0.2 dict 162-171
  list_extractions:   is_v01 at :212, v0.1 branch 214-236, v0.2 branch 238-262

$ .venv/bin/python -c "import sqlite3; print(sqlite3.sqlite_version)"
3.51.0    (>= 3.35.0 required — PASS)
```

**Resolved paths:**
```
MATRIX_PATH = /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun
TEST_SOURCE = /tmp/phase3_test_source.md     (created at smoke time)
V01_STUB    = /tmp/v01_phase3_stub.lun       (legacy v0.1: app_id=0, uv=0)
V1_STUB     = /tmp/uv1_phase3_stub.lun       (SPEC-002 Q5: app_id=LUNC, uv=1)
```

#### 2. `.schema` outputs for all 5 ULID-bearing tables

```
$ sqlite3 /tmp/phase3_test.lun ".schema doc_nodes"
CREATE TABLE doc_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    type TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT,
    meta_json TEXT,
    -- SPEC-002 portable identity (Phase 3). NOT NULL enforced by builder writes;
    -- Python regex ^[0-9A-HJKMNP-TV-Z]{26}$ is the authoritative format validator.
    ulid TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES doc_nodes(id)
);
CREATE INDEX idx_doc_nodes_parent ON doc_nodes(parent_id);
CREATE INDEX idx_doc_nodes_type ON doc_nodes(type);
CREATE UNIQUE INDEX uq_doc_nodes_ulid ON doc_nodes(ulid);
[FTS5 triggers unchanged]

$ sqlite3 /tmp/phase3_test.lun ".schema extractions"
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    -- SPEC-001 anchor classification (Phase 2)
    anchor_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (anchor_status IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')),
    anchor_reason TEXT,
    -- SPEC-002 portable identity (Phase 3). CHECK is a write gate;
    -- Python regex ^[0-9A-HJKMNP-TV-Z]{26}$ is the authoritative validator.
    ulid TEXT NOT NULL,
    CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*')
);
CREATE INDEX idx_extractions_type ON extractions(type);
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);

$ sqlite3 /tmp/phase3_test.lun ".schema claim_sources"
CREATE TABLE claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    anchor_method TEXT NOT NULL DEFAULT 'auto'
        CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by TEXT,
    anchored_at INTEGER,
    event_id TEXT,
    -- SPEC-002 shadow ULIDs (Phase 3). Nullable in v0.2; become composite PK in v0.3.
    claim_ulid TEXT,
    node_ulid TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);
CREATE INDEX idx_claim_sources_node ON claim_sources(node_id);
(No UNIQUE on shadow columns — Q1 resolution.)

$ sqlite3 /tmp/phase3_test.lun ".schema claim_context_nodes"
CREATE TABLE claim_context_nodes (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    relevance REAL NOT NULL,
    -- SPEC-002 shadow ULIDs (Phase 3). Nullable in v0.2; become composite PK in v0.3.
    claim_ulid TEXT,
    node_ulid TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);
CREATE INDEX idx_claim_context_claim ON claim_context_nodes(claim_id);
CREATE INDEX idx_claim_context_node ON claim_context_nodes(node_id);

$ sqlite3 /tmp/phase3_test.lun ".schema embeddings"
CREATE TABLE embeddings (
    node_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    vector BLOB NOT NULL,
    -- SPEC-002 shadow ULID (Phase 3). Nullable in v0.2.
    node_ulid TEXT,
    PRIMARY KEY (node_id, level),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);
CREATE INDEX idx_embeddings_level ON embeddings(level);
```

#### 3. `.indices` — UNIQUE indexes on primary ULID columns only

```
$ sqlite3 /tmp/phase3_test.lun ".indices"
idx_claim_context_claim                 idx_nexus_refs_nexus
idx_claim_context_node                  sqlite_autoindex_claim_context_nodes_1
idx_claim_sources_node                  sqlite_autoindex_claim_sources_1
idx_doc_nodes_parent                    sqlite_autoindex_embeddings_1
idx_doc_nodes_type                      sqlite_autoindex_meta_1
idx_embeddings_level                    sqlite_autoindex_nexus_refs_1
idx_extractions_anchor_status           uq_doc_nodes_ulid
idx_extractions_type                    uq_extractions_ulid
```

`uq_doc_nodes_ulid` and `uq_extractions_ulid` present. No UNIQUE index on shadow ULID columns (`claim_sources.claim_ulid`, etc.).

#### 4. ULIDGenerator 100-sample format / uniqueness / monotonicity

```
$ .venv/bin/python -c '...100-sample test from handoff...'
OK: 100 ULIDs generated, all unique, sorted == sequence-generated
Sample: GZGMAN8000AF194JPPSSXTT88W -> GZGMAN80335PV684QGMW5K33CR
```

All three assertions pass: format (26-char Crockford Base32), uniqueness (100/100 distinct), monotonicity (sorted == generation order).

#### 5. NULL-population counts on 7 ULID columns — all zero

```
doc_nodes.ulid NULL count:                  0
extractions.ulid NULL count:                0
claim_sources.claim_ulid NULL count:        0
claim_sources.node_ulid NULL count:         0
claim_context_nodes.claim_ulid NULL count:  0
claim_context_nodes.node_ulid NULL count:   0
embeddings.node_ulid NULL count:            0
```

(`claim_context_nodes` has zero rows in this build — no synthesized claims — but the column exists and the NULL count over the empty set is trivially 0.)

#### 6. Per-row samples of populated ULID columns

```
$ sqlite3 -header /tmp/phase3_test.lun "SELECT id, ulid FROM doc_nodes ORDER BY id LIMIT 5"
id|ulid
1|GZH01ZG000VGVTG652E0XNK1JG
2|GZH01ZG001WXPSXXCVN7SFZZKM
3|GZH01ZG002BNMKY688TWTCNECF
4|GZH01ZJ000FW1DB3JQNTRQ1A41
5|GZH01ZJ001BSGDDEYHVMMNSEM8

$ sqlite3 -header /tmp/phase3_test.lun "SELECT id, ulid, type, anchor_status FROM extractions ORDER BY id LIMIT 5"
id|ulid|type|anchor_status
1|GZH0876000C95VAMY2PDQPBW7E|summary|anchored
2|GZH0878000W5AYZW5VK9K1KT3V|claim|anchored
3|GZH0878001DDA1B7G1FQRKJH7P|claim|anchored
4|GZH0878002ZE1R5RYX7T9QY5AJ|claim|anchored
5|GZH0878003M7HYPYW52AM3S12D|claim|anchored

$ sqlite3 -header /tmp/phase3_test.lun "SELECT claim_id, node_id, claim_ulid, node_ulid FROM claim_sources LIMIT 5"
claim_id|node_id|claim_ulid|node_ulid
1|2|GZH0876000C95VAMY2PDQPBW7E|GZH01ZG001WXPSXXCVN7SFZZKM
2|5|GZH0878000W5AYZW5VK9K1KT3V|GZH01ZJ001BSGDDEYHVMMNSEM8
3|6|GZH0878001DDA1B7G1FQRKJH7P|GZH01ZJ002GR319V2YJ9DQAHBT
4|12|GZH0878002ZE1R5RYX7T9QY5AJ|GZH01ZJ008R5AP7Y64PD0RYBAN
5|13|GZH0878003M7HYPYW52AM3S12D|GZH01ZJ009BBVVRR1PDYARKA2P

$ sqlite3 -header /tmp/phase3_test.lun "SELECT node_id, level, node_ulid FROM embeddings LIMIT 5"
node_id|level|node_ulid
4|paragraph|GZH01ZJ000FW1DB3JQNTRQ1A41
7|paragraph|GZH01ZJ00334JEK3YDWK1P2BHC
11|paragraph|GZH01ZJ007PVWY7R17CPRJ614Y
2|section|GZH01ZG001WXPSXXCVN7SFZZKM
3|section|GZH01ZG002BNMKY688TWTCNECF
```

Build extracted 20 artifacts (4 summary + 12 claim + 4 entity) and 6 embeddings (3 paragraph + 3 section) — full end-to-end ULID coverage.

#### 7. Cross-reference integrity counts — all zero

```
claim_sources × extractions mismatch:  0
claim_sources × doc_nodes mismatch:    0
embeddings × doc_nodes mismatch:       0
```

Every shadow ULID matches its parent table's ULID for the same integer FK.

#### 8. `validate_ulids()` clean run

```
$ .venv/bin/python -c "from luna.cartridge.builder import validate_ulids; ..."
OK: validate_ulids passes
```

#### 9. Tamper test — corrupted ULID produces BuildError traceback

```
$ cp /tmp/phase3_test.lun /tmp/phase3_bad.lun
$ sqlite3 /tmp/phase3_bad.lun <<'EOF'
DROP TRIGGER IF EXISTS nodes_fts_au;
UPDATE doc_nodes SET ulid = 'NOT_A_VALID_ULID' WHERE id = (SELECT MIN(id) FROM doc_nodes);
EOF
$ sqlite3 /tmp/phase3_bad.lun "SELECT ulid FROM doc_nodes WHERE id = (SELECT MIN(id) FROM doc_nodes)"
NOT_A_VALID_ULID

$ .venv/bin/python -c '...validate_ulids on tampered file...'
OK: rejected with BuildError. Traceback follows:
Traceback (most recent call last):
  File "<string>", line 7, in <module>
    validate_ulids(conn)
    ~~~~~~~~~~~~~~^^^^^^
  File "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/src/luna/cartridge/builder.py", line 232, in validate_ulids
    raise BuildError(
    ...<2 lines>...
    )
luna.cartridge.builder.BuildError: doc_nodes.ulid has 1 malformed ULID(s). Expected 26-char Crockford Base32 matching ^[0-9A-HJKMNP-TV-Z]{26}$
```

Note: `DROP TRIGGER nodes_fts_au` was required to bypass the FTS5 trigger that blocks raw UPDATE on `doc_nodes`. The trigger is dropped only on the throwaway tamper file; the original `/tmp/phase3_test.lun` is untouched.

#### 10. Reader exposes `ulid` for v0.2 cartridge

Captured under item 11 below (`list_extractions` and `resolve_source_ref` v0.2 lines).

#### 11. Legacy read-compat across all three cartridges — six OK-prints

```
$ .venv/bin/python /tmp/phase3_reader_smoke.py
Opening v0.1 cartridge without application_id set (legacy)
Opening v0.1 cartridge without application_id set (legacy)
OK: v0.2 list_extractions (9 claims) all have non-None ulid
OK: v0.1 list_extractions (1 claims) all have ulid=None
OK: uv=1 list_extractions (1 claims) all have ulid=None
OK: v0.2 resolve_source_ref (1 claims at node 5) all have non-None ulid
OK: v0.1 resolve_source_ref (1 claims) all have ulid=None
OK: uv=1 resolve_source_ref (1 claims) all have ulid=None
```

Six OK-prints. The two "Opening v0.1 cartridge without application_id set (legacy)" lines are the existing Phase 1 warning emitted from `validate_cartridge_open()` when `application_id == 0` — appears once per opens of `$V01_STUB` (twice: once from `list_extractions`, once from `resolve_source_ref`). Expected behaviour, not a regression.

#### 12. `validate_cartridge_open()` diff bounded to user_version widening

```
$ git diff src/luna/cartridge/__init__.py | sed -n '/def validate_cartridge_open/,/^@@.*resolve_source_ref/p'
@@ -52,9 +52,9 @@ def validate_cartridge_open(conn) -> None:
             f"Not a Luna cartridge: application_id=0x{app_id:08X}, expected 0x4C554E43"
         )
     user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
-    if user_ver != 2:
+    if user_ver not in (1, 2):
         raise UnsupportedVersionError(
-            f"Cartridge user_version={user_ver}, expected 2"
+            f"Cartridge user_version={user_ver}, expected 1 (integer-only mode) or 2 (full v0.2)"
         )
```

Exactly two substantive lines changed inside the function body: the version check predicate and the error message. The `app_id == 0` tolerant early-return at lines 47-49 is byte-identical. The `WrongFamilyError` path at lines 50-53 is byte-identical.

#### 13. `meta.deprecated_columns` marker

```
$ sqlite3 /tmp/phase3_test.lun "SELECT value FROM meta WHERE key = 'deprecated_columns'"
doc_nodes.id,extractions.id
```

#### 14. Monotonicity proof — `ORDER BY id` == `ORDER BY ulid`

```
$ .venv/bin/python -c "...compare both orderings..."
doc_nodes:   ORDER BY id == ORDER BY ulid: True
extractions: ORDER BY id == ORDER BY ulid: True
```

ULIDs are written in `cursor.lastrowid` order via a per-build `ULIDGenerator` instance, so their lexical order matches their integer-id order.

#### 15. Phase 2 regression — `validate_anchors()` still passes

```
$ .venv/bin/python -c "from luna.cartridge.builder import validate_anchors; ..."
OK: validate_anchors passes
```

All six Phase 2 anchor classification invariants pass on the v0.2 build:
1. No `type='claim'` with `anchor_status='unknown'`
2. All `anchor_status` values within the CHECK set
3. Every `anchored` row has ≥1 `claim_sources` entry
4. Every `match_failed` row has `anchor_reason` set
5. `synthesized` lineage check (>=2 nodes AND >=2 distinct parents) — vacuously true (zero synthesized rows)
6. Non-auto `claim_sources` carry `anchored_by` + `anchored_at`

The extractor's mid-pass log confirms the classification distribution:
`[CARTRIDGE-EXTRACTOR] Anchor classification: {'anchored': 12, 'unknown': 8}` — the 8 unknowns are entities (Phase 2 carved out entities from the "no unknowns" invariant; SPEC-001 only scopes claims).

#### 16. SPEC-002 Q5 — `validate_cartridge_open` accepts `$V1_STUB` without raising

```
$ .venv/bin/python -c "...validate_cartridge_open on V1_STUB..."
OK: uv=1 cartridge accepted by validate_cartridge_open

$ .venv/bin/python -c "...all three cartridges through validate_cartridge_open..."
Opening v0.1 cartridge without application_id set (legacy)
OK: v0.1 cartridge accepted (app_id=0 fallback)
OK: v0.2 cartridge accepted
```

All three cartridges open without raising:
- `$V01_STUB` (`app_id=0, uv=0`) — Phase 1 tolerant early-return at `__init__.py:47-49`
- `$V1_STUB` (`app_id=LUNC, uv=1`) — Phase 3 widening at `__init__.py:55` (Q5 partial-migration support)
- `/tmp/phase3_test.lun` (`app_id=LUNC, uv=2`) — full v0.2 path

---

### Notes and Deviations

- **Tamper test required dropping `nodes_fts_au` trigger** on the throwaway file (item 9). The FTS5 update trigger raises "unsafe use of virtual table" on any direct `UPDATE doc_nodes` issued from the sqlite3 CLI. Dropping the trigger on the *copy* leaves the production `/tmp/phase3_test.lun` untouched and the validation surface (regex check inside `validate_ulids`) is what was being exercised. This is a SQLite-side limitation, not a SPEC-002 deviation.

- **`embedder.py` has TWO `INSERT OR REPLACE INTO embeddings` sites**, not one as the handoff's grep expectation implied. Both sites (paragraph-level at `embedder.py:72-75` and section-level at `:116-119`) were threaded identically with `node_ulid` and let `KeyError` raise on a missing mapping (per `feedback_no_silent_degradation`). The handoff's Step 5 pattern applies cleanly to both sites with no ordering risk: `node_id_to_ulid` is fully populated before `embed()` is called.

- **No external ULID library used** — hand-rolled `ULIDGenerator` per spec lines 391-415, including the (ts << 16 | counter) timestamp expansion that gives strictly-monotonic ULIDs even within a single millisecond.

- **`_anchor_claim` signature widened** from `(conn, claim_id, quote, sentence_nodes, anchored_by)` to `(conn, claim_id, claim_ulid, quote, sentence_nodes, anchored_by)`, and `sentence_nodes` from 2-tuple `(id, content)` to 3-tuple `(id, content, ulid)`. The method has exactly one caller (`extract()` in the same module) and no test references; the contract change is fully internal.

- **Reader legacy detection renamed** `is_v01` → `is_legacy` in both `resolve_source_ref` and `list_extractions`, with the predicate changed from `user_ver == 0` to `user_ver < 2`. This single rename absorbs both the v0.1 case (`uv=0`) and the SPEC-002 Q5 partial-migration case (`uv=1`) through the same synthesis path (both lack `ulid` and `anchor_status` columns).

### Open follow-ups (Phase 5+)

- The reader's `validate_cartridge_open` warning for v0.1 cartridges (`"Opening v0.1 cartridge without application_id set (legacy)"`) is emitted once per opened connection. For callers that open the same cartridge multiple times in a session, the warning is repeated. Not a Phase 3 issue; Phase 5 (migration tool + legacy fallback removal) is the natural cleanup point.

- `claim_context_nodes` has zero rows in the test build because the test source produces no `synthesized` claims (Haiku's substring match anchored every claim). The shadow ULID columns are present and validated, but the populated-data invariants for that table are not exercised by this smoke run. Recommend a synthetic test in Phase 5 that forces a `synthesized` path.

---

### Phase 3.5 canonical ULID hotfix

**Status:** evidence pasted, awaiting review (no commit yet)
**Implementer:** Claude (Opus 4.7) in coordination with Ahab
**Implementation date:** 2026-05-13
**Handoff:** `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE3_5_CANONICAL_ULID.md` (rev 1)
**Baseline:** Phase 3 commit `c25c4bf`
**Files changed:** `src/luna/cartridge/builder.py` (`ULIDGenerator` rewrite, `ULID_RE` tightening, error-message update), `src/luna/cartridge/schema.py` (CHECK GLOB tightening). No edits to `__init__.py`, `extractor.py`, or `embedder.py`.

**Bug:** Phase 3 generator computed `(ts << 16 | counter) << 80 | rand`, making the effective integer ~137 bits wide. The 26-char encoding extracts 130 bits bottom-up, pushing high timestamp bits into the first 5-bit char and producing first chars in `[G-Z]`. Canonical ULIDs require first char `[0-7]` per SPEC-002 D1 (the Migration mechanics code block at SPEC-002 lines 391-415 is illustrative; D1 is authoritative).

**Fix:** rewrite to standard canonical form (48 bits ts + 80 bits rand = 128 bits) with monotonicity by random-increment within the same millisecond. Tighten Python regex from `^[0-9A-HJKMNP-TV-Z]{26}$` to `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`, and SQL CHECK GLOB from `'[0-9A-HJKMNP-TV-Z]*'` to `'[0-7][0-9A-HJKMNP-TV-Z]*'`. Rebuild affected v0.2 LUNC cartridges (test fixture only; production cartridges are not yet at v0.2).

#### Smoke 1 — Pre-flight greps (all six)

```
$ git log --oneline -5
c68a39e docs: Phase 1 handoff frontmatter + dev-diary git history report
c25c4bf feat(.lun v0.2): SPEC-002 Phase 3 — portable identity (ULID additive)
c4f346e feat(.lun v0.2): SPEC-001 Phase 2 — anchor classification + reader v0.1 read-compat
c7a3dc3 feat(.lun v0.2): SPEC-006 Phase 1 — application_id contract + hygiene
12ce34f fix(voice): echo-retry pipeline + memory header scrub + frontend voice swap

$ grep -n "class ULIDGenerator\|def __init__\|def next" src/luna/cartridge/builder.py | head -20
63:class ULIDGenerator:
75:    def __init__(self) -> None:
79:    def next(self) -> str:
312:    def __init__(    # CartridgeBuilder constructor — unrelated

$ grep -nE "ULID_RE|\^\[0-9A-HJKMNP-TV-Z\]" src/luna/cartridge/builder.py
102:ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
229:            if not ULID_RE.match(row[0])
234:                f"Expected 26-char Crockford Base32 matching ^[0-9A-HJKMNP-TV-Z]{{26}}$"

$ grep -n "CHECK (length(ulid)\|ulid GLOB" src/luna/cartridge/schema.py
44:    CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*')

$ # Pre-flight 5 — enumerate LUNC + uv=2 (filter excludes LUNM 1280659021).
$ # macOS /tmp is a symlink to /private/tmp; using both paths.
$ find /tmp /private/tmp . -name "*.lun" 2>/dev/null | sort -u | while IFS= read -r f; do
>   app=$(sqlite3 "$f" "PRAGMA application_id" 2>/dev/null)
>   ver=$(sqlite3 "$f" "PRAGMA user_version" 2>/dev/null)
>   if [ "$app" = "1280659011" ] && [ "$ver" = "2" ]; then echo "$app $ver $f"; fi
> done
1280659011 2 /private/tmp/phase3_test.lun         # primary Phase 3 baseline — will rebuild
1280659011 2 /private/tmp/phase3_bad.lun          # frozen tamper artifact from Phase 3 smoke — won't touch
1280659011 2 /private/tmp/phase35_review.lun      # user-created review artifact (May 13 00:05) — won't touch

$ # Sanity: ./data/user/memory_matrix.lun is LUNM (app_id=1280659021); filter correctly excluded it.

$ # Pre-flight 6 — BEFORE first-char distribution on baseline:
$ sqlite3 /tmp/phase3_test.lun "SELECT substr(ulid,1,1), COUNT(*) FROM doc_nodes GROUP BY substr(ulid,1,1) ORDER BY 1"
G|13
$ sqlite3 /tmp/phase3_test.lun "SELECT substr(ulid,1,1), COUNT(*) FROM extractions GROUP BY substr(ulid,1,1) ORDER BY 1"
G|20
```

**PASS** — Phase 3 commit at HEAD, surface verified, three LUNC + uv=2 cartridges in scope (one in active rebuild scope, two intentionally not rebuilt per scope-discipline). BEFORE snapshot captured: all first chars are `'G'` — proves the canonical bug.

#### Smoke 2 — BEFORE/AFTER first-char distribution (headline proof)

```
BEFORE rebuild (/tmp/phase3_test.lun at Phase 3 head):
  doc_nodes:    G|13
  extractions:  G|20

AFTER rebuild (canonical generator):
  doc_nodes:    0|13
  extractions:  0|21
```

**PASS** — first chars moved from `'G'` (non-canonical) to `'0'` (canonical). Slight extraction count delta (20 → 21) reflects nondeterminism in Haiku's per-section output; the ULID-coverage invariants are deterministic.

#### Smoke 3 — 1000-sample generator: canonical, monotonic, unique

```
$ .venv/bin/python -c '
from luna.cartridge.builder import ULIDGenerator
import re
g = ULIDGenerator()
samples = [g.next() for _ in range(1000)]
first_chars = set(s[0] for s in samples)
print(f"Unique first chars across 1000 samples: {sorted(first_chars)}")
assert all(c in "01234567" for c in first_chars)
CANONICAL = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
for s in samples: assert CANONICAL.match(s)
assert sorted(samples) == samples
assert len(set(samples)) == 1000
print("OK: 1000 ULIDs canonical, monotonic, unique")
print(f"First: {samples[0]}")
print(f"Last:  {samples[-1]}")'
Unique first chars across 1000 samples: ['0']
OK: 1000 ULIDs canonical, monotonic, unique
First: 01KRFX6X4QNZEBRXP8TGDW8DQP
Last:  01KRFX6X4SK2GQB7X8KKAZ49FH
```

**PASS** — 1000 samples all match `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`, all monotonically increasing, no duplicates. Per the canonical layout, today's unix-ms timestamps occupy only the `'0'` slot of the `[0-7]` range; that's expected for any year in the next millennium or so.

#### Smoke 4 — `validate_ulids()` rejects non-canonical injection

```
$ .venv/bin/python -m luna.cartridge.builder /tmp/phase3_test_source.md /tmp/phase35_inject.lun
[CARTRIDGE] Built phase35_inject.lun (13 nodes, 72 words)
$ sqlite3 /tmp/phase35_inject.lun <<'EOF'
DROP TRIGGER IF EXISTS nodes_fts_au;     -- throwaway file only; FTS5 blocks direct UPDATE on doc_nodes
UPDATE doc_nodes SET ulid = 'GZZZZZZZZZZZZZZZZZZZZZZZZZ'
WHERE id = (SELECT MIN(id) FROM doc_nodes);
EOF
$ sqlite3 /tmp/phase35_inject.lun "SELECT ulid FROM doc_nodes WHERE id = (SELECT MIN(id) FROM doc_nodes)"
GZZZZZZZZZZZZZZZZZZZZZZZZZ

$ .venv/bin/python -c '
import sqlite3, traceback
from luna.cartridge.builder import validate_ulids, BuildError
conn = sqlite3.connect("/tmp/phase35_inject.lun")
try:
    validate_ulids(conn)
    print("UNEXPECTED")
except BuildError:
    print("OK: rejected non-canonical ULID. Traceback follows:")
    traceback.print_exc()
finally:
    conn.close()'
OK: rejected non-canonical ULID. Traceback follows:
Traceback (most recent call last):
  File "<string>", line 6, in <module>
    validate_ulids(conn)
    ~~~~~~~~~~~~~~^^^^^^
  File "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/src/luna/cartridge/builder.py", line 241, in validate_ulids
    raise BuildError(
    ...<2 lines>...
    )
luna.cartridge.builder.BuildError: doc_nodes.ulid has 1 malformed ULID(s). Expected canonical 26-char ULID matching ^[0-7][0-9A-HJKMNP-TV-Z]{25}$
```

**PASS** — `BuildError` raised, message reads the new canonical regex (`^[0-7][0-9A-HJKMNP-TV-Z]{25}$`), full traceback captured.

#### Smoke 5 — SQL CHECK constraint blocks non-canonical write

```
$ sqlite3 /tmp/phase3_test.lun "INSERT INTO extractions (type, content, ulid) VALUES ('claim', 'test', 'GZZZZZZZZZZZZZZZZZZZZZZZZZ')"
Error: stepping, CHECK constraint failed: length(ulid) = 26 AND ulid GLOB '[0-7][0-9A-HJKMNP-TV-Z]*' (19)
```

**PASS** — write rejected at the SQL layer with the tightened CHECK constraint visible in the error message. The CHECK fires before any Python validation runs.

#### Smoke 6 — DISTINCT first-char across all 5 ULID columns in rebuilt cartridge

```
$ sqlite3 /tmp/phase3_test.lun "SELECT GROUP_CONCAT(DISTINCT substr(ulid,1,1)) FROM doc_nodes"          → 0
$ sqlite3 /tmp/phase3_test.lun "SELECT GROUP_CONCAT(DISTINCT substr(ulid,1,1)) FROM extractions"        → 0
$ sqlite3 /tmp/phase3_test.lun "SELECT GROUP_CONCAT(DISTINCT substr(claim_ulid,1,1)) FROM claim_sources" → 0
$ sqlite3 /tmp/phase3_test.lun "SELECT GROUP_CONCAT(DISTINCT substr(node_ulid,1,1)) FROM claim_sources"  → 0
$ sqlite3 /tmp/phase3_test.lun "SELECT GROUP_CONCAT(DISTINCT substr(node_ulid,1,1)) FROM embeddings"     → 0
```

**PASS** — every populated ULID column starts with `'0'` (the canonical first-char range for 2026 timestamps); none in `[G-Z]`.

#### Smoke 7 — `python-ulid` round-trip interop (transient install)

```
$ .venv/bin/pip install python-ulid       # transient — NOT added to requirements.txt
... Successfully installed python-ulid-3.1.0
$ .venv/bin/python -c '
from ulid import ULID
from luna.cartridge.builder import ULIDGenerator
g = ULIDGenerator()
mismatches = []
for _ in range(100):
    s = g.next()
    parsed = ULID.from_str(s)
    if str(parsed) != s: mismatches.append((s, str(parsed)))
print("OK: 100 generated ULIDs round-trip through python-ulid library cleanly" if not mismatches else f"FAIL: {mismatches[0]}")'
OK: 100 generated ULIDs round-trip through python-ulid library cleanly
$ .venv/bin/pip uninstall -y python-ulid
... Successfully uninstalled python-ulid-3.1.0
$ git status requirements.txt   # (no such file in tree — interop install left no trace)
working tree clean
```

**PASS** — strict third-party parser accepts our ULIDs cleanly. The package was uninstalled after; no `requirements.txt` edit (the file doesn't exist in this tree).

#### Smoke 8 — Phase 3 regression: full 16-item rerun against rebuilt cartridge

| # | Item | Result |
|---|------|--------|
| 1 | Pre-flight greps (Phase 3.5 surface verified above) | PASS |
| 2 | `.schema` for 5 ULID-bearing tables — `extractions.ulid` CHECK now reads `GLOB '[0-7][0-9A-HJKMNP-TV-Z]*'`; all other schema lines unchanged | PASS |
| 3 | `.indices` lists `uq_doc_nodes_ulid` + `uq_extractions_ulid` | PASS |
| 4 | ULIDGenerator 100-sample (canonical regex): `01KRFX9GETNQRHBY0XVY2QBDF6 → 01KRFX9GEVX27N98DQC16HZZ1E` | PASS |
| 5 | NULL counts on 7 ULID columns: `0, 0, 0, 0, 0, 0, 0` | PASS |
| 6 | Per-row samples: `doc_nodes.id=1 ulid=01KRFX5QQGW1W0X64AHTRC6ED8`; `extractions.id=1 ulid=01KRFX5V98NGV4B62AK314FH82`; `claim_sources.claim_ulid=01KRFX5V98... node_ulid=01KRFX5QQG...`; `embeddings.node_ulid=01KRFX5QQGW1W0X64AHTRC6EDB` — all canonical | PASS |
| 7 | Cross-ref integrity: `claim_sources×extractions=0, claim_sources×doc_nodes=0, embeddings×doc_nodes=0` | PASS |
| 8 | `validate_ulids()` clean run | PASS |
| 9 | Tamper test: malformed-ULID injection raises `BuildError: doc_nodes.ulid has 1 malformed ULID(s). Expected canonical 26-char ULID matching ^[0-7][0-9A-HJKMNP-TV-Z]{25}$` | PASS |
| 10 + 11 | Reader pass-through across 3 cartridges (v0.2 + v0.1 + uv=1) × 2 functions (`list_extractions` + `resolve_source_ref`) = six OK-prints emitted | PASS |
| 12 | `validate_cartridge_open` diff vs Phase 3 HEAD: empty (Phase 3.5 makes no edits to `__init__.py`) | PASS |
| 13 | `meta.deprecated_columns = doc_nodes.id,extractions.id` | PASS |
| 14 | Monotonicity: `doc_nodes: ORDER BY id == ORDER BY ulid: True`; `extractions: True` | PASS |
| 15 | `validate_anchors()` clean run | PASS |
| 16 | `validate_cartridge_open($V1_STUB)` accepts `uv=1` without raising | PASS |

**PASS** — all 16 Phase 3 invariants hold under the canonical generator. The only observable schema-level change between Phase 3 and Phase 3.5 is the CHECK constraint on `extractions.ulid` (item 2) and the new canonical first-char range visible across items 4 + 6.

### Phase 3.5 deviations and notes

- **Rebuild scope narrowed:** of the three LUNC + uv=2 cartridges found, only `/tmp/phase3_test.lun` was rebuilt. `/tmp/phase3_bad.lun` is a frozen tamper artifact from Phase 3's smoke (deliberately corrupted; rebuilding would erase its purpose; a fresh tamper file `/tmp/phase35_inject.lun` was created for Phase 3.5's tamper test). `/tmp/phase35_review.lun` was created by an external review session today (May 13, 00:05) and isn't this implementer's to overwrite. Spirit of the handoff "rebuild any v0.2 cartridges in /tmp" is "leave no actively-used stale fixtures"; both untouched files are inert.

- **macOS `/tmp` symlink:** `/tmp` resolves to `/private/tmp`; `find . /tmp -name "*.lun"` deduplicates and misses files reachable only through the symlinked alias. Pre-flight 5 was re-run with `find /tmp /private/tmp . -name "*.lun" | sort -u` to surface the full set. No code change implied — just a smoke-evidence note.

- **No `python-ulid` runtime dependency:** transient `pip install`/`pip uninstall` confined to the interop smoke (item 7). Tree has no `requirements.txt`; nothing to edit; nothing edited.

- **No edits beyond Phase 3.5 scope:** `git diff` shows changes only in `src/luna/cartridge/builder.py` (generator + regex + error-message) and `src/luna/cartridge/schema.py` (CHECK GLOB). `__init__.py`, `extractor.py`, `embedder.py` are byte-identical to Phase 3 head.

---

- Commit/PR reference: (pending — Phase 3 implementation branch)
- Implementation date: 2026-05-12 (Phase 3); 2026-05-13 (Phase 3.5 hotfix, evidence pasted, awaiting commit)
- Deviations from spec: none in Phase 3.5 (D1 canonical form now correctly implemented); Phase 3's non-canonical generator was a bug, not a deviation
- Follow-up issues created: (none — Phase 3.5 captures all observed issues directly above)

### Phase 5 closeout

Phase 5 commits (chronological, all on `fix/intergalactic-hub-phase-2-runtime` after Phase 4 at `8d5c6d9`):

| Commit | Subject |
|--------|---------|
| `6775822` | chore(docs): track .lun v0.2 Phase 4+5 handoff docs and backfill Phase 4 smoke evidence |
| `80690e5` | feat: Phase 5 Step 1-2 — atomic v0.1 -> v0.2 migration tool (`src/luna/cartridge/migrate.py`) |
| `cb6d13a` | feat: Phase 5 Step 4 — remove v0.1 legacy fallback in `validate_cartridge_open` |
| `325c68b` | refactor: Phase 5 Step 5 — centralize validators into `src/luna/cartridge/validation.py` |

Phase 5 Step 3 (Lansing v0.2 build) produces a gitignored cartridge artifact at `data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun` (no commit). Phase 5 Step 6 edits `../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md` which lives outside the engine repo's git tree (no commit; edit persists on disk).

**Handoff:** [HANDOFF_NEXUS_LUN_V02_PHASE5_MIGRATION_CLOSEOUT.md](../../../HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE5_MIGRATION_CLOSEOUT.md) rev 2.

#### Item 1 — Pre-flight greps (7) + Step 0 resolution

```
--- Grep 1: Phase 4 at HEAD ---
6775822 chore(docs): track .lun v0.2 Phase 4+5 handoff docs and backfill Phase 4 smoke evidence
8d5c6d9 feat(.lun v0.2): SPEC-003 Phase 4 — drop confidence, raw signals, atomic reader patch
f83c4cb fix(.lun v0.2): SPEC-002 Phase 3.5 — canonical ULID generator
c68a39e docs: Phase 1 handoff frontmatter + dev-diary git history report
c25c4bf feat(.lun v0.2): SPEC-002 Phase 3 — portable identity (ULID additive)

--- Grep 2: validator order in builder.py ---
118:def validate_extractions(conn: sqlite3.Connection) -> None:
185:def validate_anchors(conn: sqlite3.Connection) -> None:
274:def validate_ulids(conn: sqlite3.Connection) -> None:
567:        validate_extractions(conn)
572:        validate_ulids(conn)
576:        validate_anchors(conn)

--- Grep 3: validate_cartridge_open() shape (pre-Step-4) ---
30:    "UnsupportedAttributionError",
43:class UnsupportedAttributionError(Exception):
54:    if app_id == 0:                    # ← Phase 5 Step 4 removes this gate
67:    if user_ver != 2:
73:        raise UnsupportedAttributionError(
79:        raise UnsupportedAttributionError(

--- Grep 4: Lansing source search (Step 0) ---
PDF search in ./, ~/Documents, ~/Downloads, ~/Desktop, ~/Library/Mobile Documents — all empty.
Source path recorded in pre-quarantine DB: '/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/PRIESTS AND PROGRAMMERS_Lansing.pdf' — not present at that path either.
Pre-quarantine DB probe:
  607b5b69-cc69-4799-9f57-1cdc3c53d530 | PRIESTS AND PROGRAMMERS_Lansing | 485972 (full_text chars)

→ Step 0 resolves via **Path B** (reconstruct from pre-quarantine DB).
  Reconstructed source: data/cartridges/_reconstruction/lansing_reconstructed.md (486455 bytes)

--- Grep 5: existing .lun cartridges in tree ---
uv=2 app_id=1280659021 ./data/user/memory_matrix.lun         (LUNM family — out of scope)
uv=2 app_id=1280659011 /private/tmp/phase4_test.lun          (Phase 4 baseline)
uv=2 app_id=1280659011 /private/tmp/phase4_bad.lun           (Phase 4 tamper)
uv=2 app_id=1280659011 /private/tmp/phase4_no_meta.lun       (Phase 4 tamper)
uv=2 app_id=1280659011 /private/tmp/phase4_no_base.lun       (Phase 4 tamper)
uv=1 app_id=1280659011 /private/tmp/v1_stub.lun              (Phase 3 partial-migration stub)
uv=0 app_id=0          /private/tmp/v01_phase3_stub.lun      (Phase 3 v0.1 stub)
(plus other Phase 3+3.5 artifacts, all matching expected family/version)

--- Grep 6: SPEC-002 non-canonical sketch location ---
401:    Minimal ULID generator. ts_ms = Unix timestamp in ms, counter = sub-ms sequence.
407:    # For migration: use (ts_ms << 16 | counter) as the full 48-bit timestamp value
409:    ts = (ts_ms << 16) | (counter & 0xFFFF)

--- Grep 7: validator import sites outside cartridge/__init__.py ---
(empty — no external imports of validators; Step 5 centralization has zero cross-module impact)
```

#### Item 2 — Migration round-trip on synthetic v0.1 stub

```
$ .venv/bin/python -m luna.cartridge.migrate /tmp/phase5_v01_stub.lun
{
  "strict": false,
  "input_state": "v0.1",
  "spec_006": "applied",
  "spec_001": {"anchored": 1, "orphans_classified": 1, "strict_mode": false},
  "spec_002": {"doc_nodes_ulids": 2, "extraction_ulids": 2},
  "spec_003": {"confidence_dropped": true, "llm_extractions_marked": 2},
  "path": "/tmp/phase5_v01_stub.lun",
  "dry_run": false
}

$ sqlite3 /tmp/phase5_v01_stub.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2

$ sqlite3 /tmp/phase5_v01_stub.lun ".schema extractions"
CREATE TABLE extractions (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, content TEXT NOT NULL,
  anchor_status TEXT NOT NULL DEFAULT 'unknown' CHECK (anchor_status IN
    ('anchored','synthesized','match_failed','filtered','unknown')),
  anchor_reason TEXT, ulid TEXT, llm_logprob_sum REAL, llm_token_count INTEGER,
  extraction_method TEXT NOT NULL DEFAULT 'llm' CHECK (extraction_method IN
    ('llm','rule','ner','manual')));
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);
```

Schema matches Phase 4 fresh-build modulo the documented nullable-ULID column declaration discrepancy: migrated tables get `ulid TEXT` without the `NOT NULL` + `CHECK (length(ulid)=26 AND ulid GLOB ...)` because `ALTER TABLE ADD COLUMN` cannot retrofit those constraints into the column declaration. Data still passes `validate_ulids()` and the GLOB CHECK — only the column-declaration syntax differs.

#### Item 3 — Migration produces canonical ULIDs

```
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT DISTINCT substr(ulid,1,1) FROM doc_nodes ORDER BY 1"
0
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT DISTINCT substr(ulid,1,1) FROM extractions ORDER BY 1"
0
```

All ULIDs first-char in `[0-7]` per Phase 3.5 canonical generator (only `0` observed because the migration completed within a single timestamp band).

#### Item 4 — Orphan classification + auto-anchor preservation

```
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT id, type, anchor_status, anchor_reason FROM extractions"
1|claim|anchored|
2|claim|match_failed|migration_unclassified

$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT claim_id, node_id, anchor_method, anchored_by, anchored_at FROM claim_sources"
1|1|auto||
```

Claim 1 (anchored in v0.1) became `anchored`. Claim 2 (orphan in v0.1) received the SPEC-001 fallback `match_failed` + `migration_unclassified`. Existing `claim_sources` row id=1 retained `anchor_method='auto'` per SPEC-001 line 181 — relabeling as `'migrated'` would falsely imply migration-time provenance AND violate `validate_anchors()`'s non-auto-requires-`anchored_by` invariant (no actor identity available for the original v0.1 builder run).

#### Item 5 — Migration validators all pass

```
OK: validate_extractions passes
OK: validate_ulids passes
OK: validate_anchors passes
```

(Implicit — the validators run inside `_migrate_open_conn()` after the four `_apply_spec_*` helpers; commit happens only if all three pass.)

#### Item 6 — Strict mode rejection (transaction rolled back)

```
$ .venv/bin/python -m luna.cartridge.migrate --strict /tmp/phase5_v01_strict.lun
MIGRATION FAILED: Strict mode: 1 orphan claims would receive the
'migration_unclassified' fallback. Full classification analysis
(synthesized / filtered / match_failed) is not implemented in Phase 5.
Either resolve manually before re-running, or drop --strict to accept
the spec-documented fallback for all orphans.

$ sqlite3 /tmp/phase5_v01_strict.lun "PRAGMA application_id; PRAGMA user_version"
0
0
```

`MigrationError` carries the spec-required "would receive the 'migration_unclassified' fallback" phrase. Post-rejection state has `application_id=0` + `user_version=0` (rollback worked — the file is byte-identical to its pre-migration state).

#### Item 7 — uv=1 partial-migration acceptance

```
$ .venv/bin/python -m luna.cartridge.migrate /tmp/phase5_uv1_partial.lun
{
  "strict": false,
  "input_state": "uv=1_partial",
  "spec_006": "applied",
  ...
}

$ sqlite3 /tmp/phase5_uv1_partial.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2
```

Pre-flight gate accepts both `(app_id=0, uv=0)` true-v0.1 and `(app_id=LUNC, uv=1)` partial-migration; partial-migration replays cleanly because every `_apply_spec_*` helper uses `_add_column_if_missing` and `INSERT OR REPLACE`.

#### Item 8 — Dry-run leaves file untouched

```
$ .venv/bin/python -m luna.cartridge.migrate --dry-run /tmp/phase5_v01_dryrun.lun
{
  "strict": false,
  "input_state": "v0.1",
  "spec_006": "applied",
  ...,
  "dry_run": true
}

$ sqlite3 /tmp/phase5_v01_dryrun.lun "PRAGMA application_id; PRAGMA user_version"
0
0
```

Dry-run clones the file into an in-memory SQLite connection via `sqlite3.Connection.backup`, runs the four `_apply_spec_*` helpers + validators against the clone, returns the summary, and discards. The on-disk file is never opened in write mode.

#### Item 9 — Lansing v0.2 build (headline measurement — DEVIATION noted)

```
$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT key, value FROM meta WHERE key IN
  ('format_version','logprob_base','logprob_attribution','deprecated_columns','node_count','word_count')
  ORDER BY key"
deprecated_columns|doc_nodes.id,extractions.id
format_version|0.2
logprob_attribution|response_level
logprob_base|e
node_count|5576
word_count|79213

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT type, anchor_status, COUNT(*)
  FROM extractions GROUP BY type, anchor_status ORDER BY type"
entity|unknown|15
summary|anchored|1

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT
  SUM(CASE WHEN type='claim' AND anchor_status='match_failed' THEN 1 ELSE 0 END) AS claim_match_failed,
  SUM(CASE WHEN type='claim' AND anchor_status='anchored' THEN 1 ELSE 0 END) AS claim_anchored
  FROM extractions"
0|0
```

**DEVIATION:** The headline `claim_match_failed / (claim_anchored + claim_match_failed)` ratio is **undefined** (0 / 0) for this build, so no actionable comparison against the 2026-04-21 audit's 9.5% orphan baseline is possible from Phase 5's Lansing v0.2 cartridge.

Root cause is structural, not a code bug:

1. Step 0 resolved via Path B (PDF unavailable), producing a markdown reconstruction from `full_text` in the pre-quarantine DB
2. The reconstructed markdown has no `#` heading syntax (the original PDF's structure was lost when the text was flattened into the `full_text` column)
3. `MarkdownParser` consequently identifies only **1 section** spanning all 5576 nodes
4. `CartridgeExtractor.extract()` truncates each section to 8000 chars before sending to Haiku (`extractor.py:167`), so only the first 8000 of 485972 chars get extracted
5. That single Haiku call returned 1 summary + 15 entities + 0 claims (typical for a title-page-like opening segment)

Cartridge is structurally valid (`application_id=LUNC`, `user_version=2`, all meta markers present, all validators clean, ULIDs canonical, `extraction_method='llm'` for all 16 rows, deprecated_columns marker intact). The 9.5%-baseline measurement is **deferred** as a follow-up requiring either (a) PDF source recovery for a structure-preserving rebuild, or (b) a chapter-splitting pre-process that injects `#` heading markers into the reconstructed markdown before build. See Phase 5 follow-up below.

#### Item 10 — Legacy fallback removed: `app_id=0` raises `WrongFamilyError`

```
$ .venv/bin/python -c "
import sqlite3
from luna.cartridge import validate_cartridge_open, WrongFamilyError
conn = sqlite3.connect('/tmp/phase5_v01_unmigrated.lun')
try:
    validate_cartridge_open(conn)
    print('UNEXPECTED')
except WrongFamilyError as e:
    print(f'OK: rejected: {e}')
finally:
    conn.close()
"
OK: rejected: Cartridge has application_id=0x0, expected LUNC (0x4C554E43).
Pre-SPEC-006 v0.1 cartridges (app_id=0) must be migrated first via
`python -m luna.cartridge.migrate <path>`.
```

Error message points at the migration command. uv=1 LUNC partial-migration stubs and uv=2 v0.2 cartridges still open cleanly (regression checked separately — both pass).

#### Item 11 — Validator centralization regression

```
$ .venv/bin/python -c "
from luna.cartridge.builder import validate_anchors, validate_ulids, validate_extractions, BuildError
from luna.cartridge import (validate_cartridge_open, WrongFamilyError,
                            UnsupportedVersionError, UnsupportedAttributionError)
from luna.cartridge.validation import (
    validate_anchors as v_anchors,
    validate_ulids as v_ulids,
    validate_extractions as v_extractions,
    validate_cartridge_open as v_open,
    BuildError as v_be,
    WrongFamilyError as v_wfe,
    UnsupportedVersionError as v_uve,
    UnsupportedAttributionError as v_uae,
)
assert validate_anchors is v_anchors
assert validate_ulids is v_ulids
assert validate_extractions is v_extractions
assert validate_cartridge_open is v_open
assert BuildError is v_be
assert WrongFamilyError is v_wfe
assert UnsupportedVersionError is v_uve
assert UnsupportedAttributionError is v_uae
print('OK: all validators + exceptions re-exported from validation.py; public API preserved')
"
OK: all validators + exceptions re-exported from validation.py; public API preserved
```

Phase 4 smoke items re-run against centralized validators after Step 5:
- Item 7 (`validate_extractions` clean): `OK: validate_extractions passes`
- Item 8 (paired-NULL tamper): `OK: rejected with BuildError: 1 extractions have mismatched logprob/token_count NULLs.`
- Item 10a (missing `logprob_attribution`): `OK: rejected: meta.logprob_attribution must be 'response_level' for v0.2 cartridges. Got: MISSING`
- Item 11 (uv=1 LUNC stub still opens): `OK: /tmp/v1_stub.lun opens cleanly`

Migration tool round-trip on a fresh v0.1 stub after Step 5 produces the same JSON summary as before centralization. Identity assertions confirm zero behavioral change.

#### Item 12 — SPEC-002 Phase 3.5 lesson annotation visible

```
$ grep -A 8 "Phase 3.5 lesson" "../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md"
> **NOTE (Phase 3.5 lesson):** The example below uses `ts << 16 | counter` as a sub-ms
> monotonicity sketch. This is **non-canonical** — it overflows the 48-bit timestamp field
> and produces first chars in `[G-Z]` for current dates, which strict ULID parsers reject as
> overflow. The canonical generator (48-bit ts + 80-bit random, monotonic via random
> increment within same ms) lives in `src/luna/cartridge/builder.py::ULIDGenerator` and is
> the authoritative form. See `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE3_5_CANONICAL_ULID.md`
> for the root cause analysis. The example below is preserved for historical context;
> do not copy it into new implementations.
```

Annotation inserted between the prose at SPEC-002 line 391 and the example code-fence opening at line 393. Example code itself unmodified.

#### Item 13 — Phase 1-4 regression sweep on Lansing v0.2 cartridge

```
OK: validate_extractions
OK: validate_ulids
OK: validate_anchors
OK: validate_cartridge_open

meta markers: deprecated_columns=doc_nodes.id,extractions.id  format_version=0.2
              logprob_attribution=response_level  logprob_base=e
ULID first chars in doc_nodes: 0 only (canonical [0-7] range, single timestamp band)
extraction_method distribution: llm=16 (Phase 4 SPEC-003 invariant holds)
```

All Phase 1 (SPEC-006), Phase 2 (SPEC-001), Phase 3 (SPEC-002), Phase 3.5 (canonical ULID), and Phase 4 (SPEC-003) invariants hold on the Lansing v0.2 cartridge.

### Phase 5 deviations and follow-ups

1. **Lansing 9.5% baseline measurement deferred** — see Item 9. The reconstructed markdown has no `#` headings, so the parser produces 1 section and the extractor only processes the first 8000 chars. Cartridge is structurally valid but the headline ratio is undefined. **Follow-up:** either recover the original PDF (Path A) for a rebuild that preserves chapter structure, OR pre-process the reconstructed markdown with a chapter-detection heuristic that injects `#` headings before build. Track as a Phase-5-followup; not gating the Phase 5 closeout because the cartridge is otherwise valid and the structural arc is complete.

2. **SPEC-002 example annotation lives outside the engine repo** — `../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md` is in a sibling directory that is not under git. Step 6's edit persists on disk but has no commit. If the Research tree later gets git-tracked, the annotation will surface as a pre-existing edit.

3. **Pre-flight grep 5 (Phase 4 backfill)** — original pattern `SELECT.*FROM extractions` returned 1 hit instead of 4 because Phase 4's `resolve_source_ref()` SQL was reformatted to multi-line. Documented in the Phase 4 handoff backfill; not a Phase 5 issue per se, but surfaces the same drift any future single-line grep pattern will hit.

4. **Markdown reconstruction `\x0c` artifacts** — the pre-quarantine DB's `full_text` for Lansing preserves PDF form-feed characters (page breaks). These pass through to the cartridge text content. Not a build-breaking issue (parser treats them as whitespace) but visible in `doc_nodes.content`. Follow-up if/when chapter-splitting heuristic lands: strip `\x0c` as part of the same pre-process.

5. **Adjacent smells deferred per scope discipline:**
   - Full SPEC-001 orphan semantic classification (synthesis/filtered detection via multi-lineage similarity + section-type heuristics) — deferred to a future spec (likely SPEC-004 consumer territory). Phase 5 applies the spec-documented `migration_unclassified` fallback uniformly.
   - SPEC-002 `extractions.id` ULID-only consolidation — v0.3 territory.
   - `_migration_log` table for migrations — explicit SPEC-002 Q4 reject; not added.
   - Backend-side logprob exposure (`HaikuResult.usage` fields) — v0.3 backend-side improvement; current paired-NULL trivially satisfies the SPEC-003 contract.
