# SQLite Research Briefs
**Source:** sqlite-3530000 (3.53.0), docs + src verified  
**Date:** 2026-05-10  
**Purpose:** Feeds SPEC-001 through SPEC-005 and architecture decisions for the .lun format

---

## Priority Queue

| # | Topic | Feeds | Status |
|---|-------|-------|--------|
| 3 | Hash-chained append-only ledger | SPEC-005 | **HIGH** |
| 2 | Portable PKs (content-addr / UUID / ULID / UUIDv7) | SPEC-002 | **HIGH** |
| 1 | `application_id` patterns and registry | v0.2 hygiene | **SHIP NOW** |
| 4 | CHECK constraints + triggers, invalid-states-unrepresentable | SPEC-001 | **HIGH** |
| 5 | Schema migration: additive, backward-compatible | .lun evolution | **HIGH** |
| 6 | ATTACH DATABASE for multi-cartridge query | plug-in discovery | **MEDIUM** |
| 7 | FTS5: external content, tokenizers, migration | `nodes_fts` | **MEDIUM** |
| 8 | Read-only / read-mostly optimization | cartridge distribution | **MEDIUM** |
| 9 | SQLite WASM ecosystem | browser .lun viewer | **LOW** |
| 10 | sqlite-vec: failure modes at scale | Eclissi dependency | **MEDIUM** |

---

## Topic 3: Hash-Chained Append-Only Ledger in SQLite

### The core question
Who has done `previous_hash → current_hash` chains in pure SQLite, how does it work, and where does it break?

### Prior art

**Fossil's manifest chain** is the canonical example. Fossil stores every artifact as a blob keyed by SHA-3 (previously SHA-1) of its content. The manifest for a checkin contains a field `P <sha1-of-parent>` — the parent pointer. Fossil's repo is a standard SQLite database (`fossil.db`) with an `event` table, a `blob` table storing raw content, and a `delta` table for compression. The chain is enforced *at application level* (Fossil's C code), not at the DB level. The DB itself is append-only by convention, not constraint — an admin with `sqlite3` CLI can modify anything.

**Certificate Transparency** logs (RFC 6962) use a Merkle tree approach: each log entry contains the hash of the previous leaf. The chain is verifiable externally but again enforcement is application-level.

**The honest answer**: nobody enforces hash-chaining at the SQLite constraint layer because SQLite has no native hash function and no cross-row awareness in CHECK constraints.

### What SQLite can enforce

SQLite *can* enforce append-only semantics via triggers. The mechanism:

```sql
CREATE TRIGGER ledger_no_update
BEFORE UPDATE ON annotation_ledger
BEGIN
  SELECT RAISE(ABORT, 'annotation_ledger is append-only: updates forbidden');
END;

CREATE TRIGGER ledger_no_delete
BEFORE DELETE ON annotation_ledger
BEGIN
  SELECT RAISE(ABORT, 'annotation_ledger is append-only: deletes forbidden');
END;
```

When `RAISE(ABORT, ...)` fires: the statement is rolled back, `SQLITE_CONSTRAINT` is returned to the application. The transaction may or may not be rolled back depending on `ON CONFLICT` handling and whether this is part of a larger transaction.

**Caution on BEFORE triggers (from docs):** "If a BEFORE UPDATE or BEFORE DELETE trigger modifies or deletes a row that was to have been updated or deleted, then the result of the subsequent update or delete operation is undefined." The append-only pattern is fine because we `RAISE` and never actually modify — we just abort.

`RAISE(ROLLBACK, ...)` rolls back the entire transaction. `RAISE(ABORT, ...)` rolls back only the current statement. For ledger enforcement, `ABORT` is usually right — you want the caller to know what happened and be able to handle it, rather than silently killing their outer transaction.

### What it does NOT prevent

- `DROP TABLE annotation_ledger` — no trigger fires on DDL
- `DELETE FROM annotation_ledger` *with triggers disabled* — `PRAGMA disable_triggers=1` (internal, not public, but `writable_schema` workarounds exist)
- Admin using SQLite CLI: `sqlite3 memory.lun "DELETE FROM annotation_ledger"`
- `PRAGMA writable_schema=ON` + direct sqlite_schema modification
- Replacing the file entirely

**The honest framing**: trigger-enforced append-only is a *soft covenant*, not a cryptographic guarantee. It raises the bar for accidental modification (application code can't accidentally delete) and is useful for auditing (you can detect tampering by re-verifying the hash chain). It's not tamper-proof against a determined admin.

### The hash chain implementation pattern

Since SQLite has no native SHA function, the hash must be computed at application insert time. The canonical pattern:

```sql
CREATE TABLE annotation_ledger (
  id        INTEGER PRIMARY KEY,   -- rowid alias, monotone
  seq       INTEGER NOT NULL UNIQUE, -- application-level sequence number
  entry_ts  INTEGER NOT NULL,       -- unix epoch ms
  payload   TEXT    NOT NULL,       -- JSON or structured content
  prev_hash TEXT,                   -- NULL for genesis entry
  entry_hash TEXT NOT NULL UNIQUE   -- SHA-256 of (seq || entry_ts || payload || prev_hash)
);

-- Computed in application layer before INSERT:
-- entry_hash = sha256(str(seq) + "|" + str(entry_ts) + "|" + payload + "|" + (prev_hash or ""))
```

To verify integrity: walk the table in `seq` order, recompute each hash, confirm `entry_hash` matches and each `prev_hash` equals the prior row's `entry_hash`. If any row is missing or modified, the chain breaks.

The genesis row has `prev_hash = NULL` (or a fixed sentinel like `'0' * 64`). Make this part of the spec.

### A note on the "admin can still drop the table" problem

The standard mitigation is **external verification**: a witness service, a separate signed snapshot, or publishing the chain root. Fossil handles this by making the repo content-addressed — any tampering changes the hashes and breaks verification. For annotation_ledger in the .lun format, the practical protection level is: "modification requires deliberate effort and leaves detectable evidence." That's appropriate for the governance use case.

### Spec implications for SPEC-005

- `annotation_ledger` needs BEFORE UPDATE + BEFORE DELETE triggers
- Hash chain uses `prev_hash → entry_hash` where `entry_hash = SHA-256(seq || ts || payload || prev_hash)`
- Hash computed by application, stored in table
- Verification function in spec: walk ordered by `seq`, recompute, detect any break
- Genesis entry: `prev_hash = NULL`, documented sentinel in spec
- Admin bypass acknowledged in spec — "soft append-only covenant"

---

## Topic 2: Portable Primary Keys (content-addr / UUID / ULID / UUIDv7)

### The core question
When keys cross database boundaries (cartridge-to-cartridge, or runtime DB referencing a cartridge), what are the real-world trade-offs?

### The critical SQLite background: how TEXT PKs actually work

In a **standard rowid table** (default), declaring `text_col TEXT PRIMARY KEY` creates:
1. A hidden rowid B-Tree (the main table, keyed by integer rowid)
2. A separate UNIQUE index B-Tree on `text_col`

A lookup by text key requires **two B-Tree lookups**: first into the index (finds rowid), then into the main table (reads data). The text is stored *twice*: once in the index, once in the main table.

In a **WITHOUT ROWID table**, declaring `text_col TEXT PRIMARY KEY` creates a single clustered B-Tree keyed by the text. One lookup retrieves everything. Text stored once.

**Direct from docs (verified):** "in some cases, a WITHOUT ROWID table can use about half the amount of disk space and can operate nearly twice as fast."

**When WITHOUT ROWID is worth it:**
- Non-integer or composite primary keys
- Rows average under ~1/20th of page size (under ~200 bytes for 4KiB pages)
- Lookups by PK are frequent

**When it's NOT worth it:**
- Single-column INTEGER primary key (ordinary rowid table is faster for this)
- Large BLOBs as PKs (intermediate B-Tree nodes get fat, fan-out decreases)
- Content larger than ~200 bytes per row average

### Key format comparison

| Format | Length | Type | Sortable | Monotone | Cross-boundary safe | Notes |
|--------|--------|------|----------|----------|---------------------|-------|
| `INTEGER` (rowid) | 8 bytes | int | yes | yes | **NO** — rowids repeat across DBs | Fast, but can't be a portable key |
| `UUID v4` | 36 chars (TEXT) or 16 bytes (BLOB) | random | no (TEXT sort ≠ time sort) | no | yes | No temporal ordering in index |
| `ULID` | 26 chars | TEXT | **yes** (lex = time) | yes (ms precision) | yes | Designed for SQLite-style use |
| `UUIDv7` | 36 chars TEXT or 16 bytes BLOB | time-ordered | yes if sorted as binary | yes (ms precision) | yes | RFC 9562; binary packing saves space |
| `SHA-256` content hash | 64 hex chars TEXT or 32 bytes BLOB | content-addressed | yes (but meaningless) | no | yes | Git's approach; dedup is the feature |

### Content-addressed keys: the Git analogy and why it's different

Git's SHA-1 key identifies *content*, not *position*. If two blobs are identical, they share a key — deduplication is built in. For annotation rows, this is usually wrong: two annotations with identical text should still be distinct rows. Content addressing is only correct for immutable artifact storage (e.g., Fossil's blob table), not for records that have identity beyond their content.

Use content hashing as a **hash within the chain** (the `entry_hash` field in SPEC-005), not as the row PK.

### ULID vs UUIDv7: the practical difference

Both are time-ordered (ms precision), both are globally unique, both survive DB merges. Key difference:

- **ULID**: 48 bits timestamp + 80 bits random. Encodes as 26-char Crockford Base32. SQLite TEXT. Lexicographic sort = chronological sort. Works perfectly as a WITHOUT ROWID TEXT PK.
- **UUIDv7**: 48 bits timestamp + 74 bits random. Encodes as standard UUID string (36 chars with dashes). Or store as 16-byte BLOB. Binary form is more compact but requires hex()/blob manipulation in SQL.

For portable SQLite without any binary encoding dependencies, **ULID as TEXT** is the cleanest. The 26-char string is valid SQL, sorts correctly, is human-readable-enough, and fits neatly in a WITHOUT ROWID clustered index.

For interop with systems that expect UUID format, **UUIDv7 as TEXT** works, but the 36-char form has slightly more overhead than ULID's 26-char.

### Recommendation for SPEC-002

**Cross-boundary portable IDs**: Use ULID (26-char TEXT) stored in a WITHOUT ROWID table wherever the PK will be referenced cross-cartridge.

```sql
CREATE TABLE claims (
  claim_id TEXT PRIMARY KEY,  -- ULID, e.g. '01HQ3KZXD4FGTW8N5PJKZMBV3R'
  ...
) WITHOUT ROWID;
```

- Without ROWID: single B-Tree, no double lookup, text stored once
- ULID: time-ordered, cross-boundary safe, no collision risk, human-debuggable
- Index joins on `claim_id` work across ATTACH'd databases because text comparison is universal

**What about the INTEGER PK for internal tables?** Keep INTEGER PK (rowid) for tables that never cross database boundaries (session state, local indexes, caches). Switch to ULID WITHOUT ROWID only for tables that are part of the portable .lun format contract.

---

## Topic 1: application_id Patterns and Informal Registry

### The canonical registry: magic.txt

The SQLite source ships a `magic.txt` (at root of source tree, also available at `sqlite.org/src/artifact?ci=trunk&filename=magic.txt`). This is the authoritative list of registered `application_id` values and is read by `file(1)` on Unix systems.

**Known registrations (from magic.txt, verified in sqlite-src-3530000):**

| Hex Value | Decimal | Application |
|-----------|---------|-------------|
| `0x0f055111` | 251,674,897 | Fossil repository |
| `0x0f055112` | 251,674,898 | Fossil checkout |
| `0x0f055113` | 251,674,899 | Fossil global configuration |
| `0x42654462` | 1,113,948,258 | Bentley Systems BeSQLite Database |
| `0x42654c6e` | 1,113,950,318 | Bentley Systems Localization File |
| `0x47504b47` | 1,196,444,487 | OGC GeoPackage |
| `0x47503130` | 1,196,441,904 | OGC GeoPackage v1.0 |
| `0x45737269` | 1,165,517,417 | Esri Spatially-Enabled Database |
| `0x4d504258` | 1,296,257,112 | MBTiles tileset |
| `0x6a035744` | 1,778,958,148 | TeXnicard card database |
| `0x5f4d544e` *(at offset 60!)* | 1,598,903,374 | Monotone (uses `user_version`, historical) |

**How to pick a value**: there's no formal registry authority. Convention is:
1. Pick something memorable and collision-unlikely
2. Based on the application name in some encoding (ASCII of initials, etc.)
3. Submit to the SQLite project to update `magic.txt` for public registration

For the Luna Engine `.lun` format, a reasonable approach: ASCII of "LUNA" in big-endian = `0x4C554E41` (L=0x4C, U=0x55, N=0x4E, A=0x41). This is `1,280,266,049` decimal — not in the current registry.

**To verify no collision**: `grep 0x4C554E41 magic.txt` → not present as of 3.53.0.

### The three version-tracking fields

All three live in the 100-byte database header:

| Field | Offset | Who Controls | Purpose |
|-------|--------|-------------|---------|
| `schema_version` | 40 | **SQLite internally** | Increments on every schema change (CREATE TABLE, ALTER TABLE, etc.). Never set manually. Used to invalidate prepared statement caches. |
| `user_version` | 60 | **Application** | SQLite never touches it. Use for schema migration tracking (i.e., "which migration version is this DB on"). |
| `application_id` | 68 | **Application** | SQLite never touches it. Use for file format identification. Set once at DB creation, never change. |

### The dual-tracking pattern

The question was: how to dual-track schema version in both a `meta` table and `user_version`? The right pattern is:

```sql
-- At DB creation
PRAGMA application_id = 0x4C554E41;  -- 'LUNA' — set once, never change
PRAGMA user_version = 1;              -- start at 1

-- In meta table (human-readable, queryable)
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT INTO meta VALUES ('format_version', '1');
INSERT INTO meta VALUES ('created_at', strftime('%s', 'now'));

-- On migration to version 2:
BEGIN;
-- ... schema changes ...
UPDATE meta SET value = '2' WHERE key = 'format_version';
PRAGMA user_version = 2;
COMMIT;
```

Keep both in sync — they're redundant by design. `user_version` enables fast "do I need to migrate?" check without opening the full schema. `meta` table is queryable by application code and survives file inspection without knowing the SQLite binary format.

**Do not use `schema_version`** for application versioning — SQLite increments it on every DDL operation and you'll collide with its internal use.

### Ship decision

Pick `0x4C554E41` for the `.lun` format unless there's a reason to use something else. Set it in the DB creation script. Add it to the spec as a fixed constant.

---

## Topic 4: CHECK Constraints, Triggers, and Invalid-States-Unrepresentable

### What CHECK can do

From the docs (verified in `lang_createtable.html`):

- CHECK is evaluated on every `INSERT` and `UPDATE`
- Expression cast to NUMERIC: zero → violation; NULL or nonzero → ok
- Expression may not contain a subquery
- Column-level or table-level (functionally identical)
- Can span multiple columns in a table-level CHECK

Examples of what CHECK **can** enforce declaratively:

```sql
CREATE TABLE claims (
  claim_id   TEXT PRIMARY KEY,
  status     TEXT NOT NULL CHECK(status IN ('draft','pending','anchored','rejected')),
  confidence REAL CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
  anchor_ts  INTEGER,
  -- Cross-column invariant: if status='anchored', anchor_ts must be non-null
  CHECK(status != 'anchored' OR anchor_ts IS NOT NULL)
) WITHOUT ROWID;
```

That last table-level CHECK — `CHECK(status != 'anchored' OR anchor_ts IS NOT NULL)` — enforces the invariant without a trigger.

### What CHECK cannot do

- Reference other tables (no subqueries)
- Enforce cross-table invariants: "a claim with status='anchored' must have at least one row in `claim_sources`"
- Enforce ordering constraints: "version must be greater than previous version"
- Enforce uniqueness beyond UNIQUE/PK constraints

**Important caveat from docs**: "CHECK constraints are only verified when the table is written, not when it is read. Furthermore, verification of CHECK constraints can be temporarily disabled using `PRAGMA ignore_check_constraints=ON`." A corrupt or manually modified database can have CHECK-violating rows. CHECK is a *write gate*, not a guarantee of current state.

### When you need triggers

For cross-table invariants, use AFTER INSERT/UPDATE triggers with a subquery check:

```sql
-- Enforce: claim with status='anchored' must have ≥1 row in claim_sources
CREATE TRIGGER claims_anchor_invariant
AFTER INSERT ON claims
WHEN NEW.status = 'anchored'
BEGIN
  SELECT RAISE(ABORT, 'anchored claim must have at least one source')
  WHERE NOT EXISTS (
    SELECT 1 FROM claim_sources WHERE claim_id = NEW.claim_id
  );
END;

CREATE TRIGGER claims_anchor_invariant_update
AFTER UPDATE OF status ON claims
WHEN NEW.status = 'anchored'
BEGIN
  SELECT RAISE(ABORT, 'anchored claim must have at least one source')
  WHERE NOT EXISTS (
    SELECT 1 FROM claim_sources WHERE claim_id = NEW.claim_id
  );
END;
```

Use `AFTER` triggers (not BEFORE) when you need to query other tables that may have been modified in the same transaction — AFTER fires after the current row change is visible.

### RAISE options and when to use each

| Form | Effect | Use case |
|------|--------|----------|
| `RAISE(ABORT, msg)` | Roll back current statement, continue transaction | Standard constraint enforcement |
| `RAISE(ROLLBACK, msg)` | Roll back entire transaction | Critical invariant where partial state is unacceptable |
| `RAISE(FAIL, msg)` | Abort statement, *keep changes so far in transaction* | Unusual; mostly for bulk-insert situations |
| `RAISE(IGNORE)` | Silently skip the current trigger action | INSTEAD OF triggers on views |

For SPEC-001 invariants, use `RAISE(ABORT, ...)` as the default. Use `RAISE(ROLLBACK, ...)` only for integrity violations that corrupt the entire in-progress operation.

### The "invalid states unrepresentable" pattern in practice

The principle: schema design should make invalid states syntactically or structurally impossible, not just logically forbidden. In practice, this means layering:

1. **Column type + affinity**: TEXT column can't store integers without coercion (SQLite's type affinity is weak, but you can enforce with CHECK)
2. **NOT NULL**: eliminates null-ambiguity for required fields
3. **CHECK constraints**: single-table structural invariants
4. **UNIQUE + partial indexes**: enforce conditional uniqueness (`CREATE UNIQUE INDEX idx ON t(col) WHERE condition`)
5. **Triggers**: cross-table invariants, ordering constraints, cascade behaviors
6. **Application layer**: business logic that SQL can't express

The migration implication: adding CHECK constraints to a table with existing data fails if any existing row violates the constraint (behavior added in 3.37.0). Always validate data before adding constraints.

---

## Topic 5: Schema Migration for Additive, Backward-Compatible SQLite

### The core property to preserve

Old reader opens new `.lun` → succeeds, reads data it understands, ignores new columns. This is the "forward compatibility" promise. SQLite enables this naturally because:

- New columns added via `ADD COLUMN` have a DEFAULT value
- Old readers' queries don't reference new columns
- The only way this breaks is if the schema validator rejects unknown columns — don't do that

### ALTER TABLE ADD COLUMN: exact semantics (from docs, verified)

The DDL modification: `ALTER TABLE ADD COLUMN` works by rewriting the SQL text in `sqlite_schema`. **No table content is changed** unless the new column has constraints that must be validated against existing rows.

**Constraints on what you can ADD:**

| Restriction | Why |
|-------------|-----|
| Cannot be `PRIMARY KEY` or `UNIQUE` | Would require index rebuild |
| Default cannot be `CURRENT_TIME`, `CURRENT_DATE`, `CURRENT_TIMESTAMP`, or `(expr)` | These are dynamic — their value at add time ≠ value at row creation time |
| If `NOT NULL`, must have explicit DEFAULT ≠ NULL | Otherwise existing rows would violate NOT NULL |
| If `REFERENCES` (FK), must default to NULL | Otherwise existing rows might violate FK constraint |
| Cannot be `GENERATED ALWAYS AS ... STORED` | Would require table rewrite to compute+store values; VIRTUAL is ok |

**Performance**: ADD COLUMN without constraints runs in O(1) — just a schema text change, independent of table size. ADD COLUMN *with* CHECK or NOT NULL-on-generated-column requires reading all existing rows to validate → O(n).

**Compatibility floor**: After ADD COLUMN, the database cannot be opened by SQLite ≤ 3.1.3 (released 2005-02-20). Not a practical concern.

### When ADD COLUMN is enough

Adding a new optional field with a sensible default:

```sql
ALTER TABLE claims ADD COLUMN external_url TEXT DEFAULT NULL;
ALTER TABLE claims ADD COLUMN confidence_score REAL DEFAULT NULL;
ALTER TABLE nodes ADD COLUMN embedding_model TEXT DEFAULT 'unknown';
```

These are all O(1), backward-compatible, and old readers ignore the new column.

### When ADD COLUMN fails and you need a table rewrite

| Need | Why ADD COLUMN fails |
|------|---------------------|
| Add column with NOT NULL and no default | Existing rows would immediately violate |
| Add column to WITHOUT ROWID table with a constraint that must be validated | EXISTS check on all rows |
| Change column type or affinity | ADD COLUMN can only add, not modify |
| Reorder columns | Not supported; columns are always appended |
| Convert ordinary table to WITHOUT ROWID | Structural change, full rewrite |
| Add STORED generated column | Must compute+store value for all existing rows |

The full rewrite pattern:

```sql
BEGIN;
CREATE TABLE claims_new (
  -- new schema here
) WITHOUT ROWID;
INSERT INTO claims_new SELECT ..., NULL AS new_col FROM claims;
DROP TABLE claims;
ALTER TABLE claims_new RENAME TO claims;
-- Recreate triggers, indexes, views on claims
PRAGMA user_version = <new_version>;
COMMIT;
```

Note: `DROP TABLE` removes all triggers, indexes, and views referencing `claims`. They must be recreated. **This is the #1 migration footgun in SQLite.**

### PRAGMA user_version: the migration protocol

```sql
-- Check version before deciding what to migrate
SELECT * FROM pragma_user_version;  -- returns integer

-- Pattern for a migration function:
BEGIN EXCLUSIVE;
-- Read current version
-- Apply migrations in sequence (idempotent if possible)
-- Bump version at end
PRAGMA user_version = <N+1>;
COMMIT;
```

`BEGIN EXCLUSIVE` locks the file immediately, preventing concurrent migration attempts. This is the right locking mode for migration scripts.

### The "old reader opens new file by ignoring additions" contract

For this to hold, old readers must:
1. Not `SELECT *` and then map columns by position — column order may shift if you ever do a table rewrite
2. Reference columns by name
3. Not fail on schema validation if unknown columns exist

The contract breaks if a new column is NOT NULL with no default — but ADD COLUMN prevents this anyway. It also breaks if the reader validates the schema strictly ("I don't recognize this column, error"). Document this in the format spec.

### Version scheme recommendation

Use `user_version` as a monotone integer. Never decrement it. The `.lun` format spec should define:

- `user_version = 0`: uninitialized (reject)
- `user_version = 1`: v0.1 baseline schema
- `user_version = 2`: v0.2 + `application_id`, `external_url` on claims, etc.

Document the migration SQL for each version bump in the spec. The migration script is part of the format contract.

---

## Topic 6: ATTACH DATABASE for Multi-Cartridge Query

### What ATTACH does

```sql
ATTACH DATABASE '/path/to/cartridge.lun' AS c1;
-- Now query across main and cartridge:
SELECT m.claim_id, c1.annotations.* 
FROM main.claims m
JOIN c1.annotations a ON a.claim_id = m.claim_id;
```

Tables referenced as `schema.table`. If the name is unique across all attached DBs, the prefix is optional.

### Atomicity — the critical footgun

From docs (verified): "Transactions involving multiple attached databases are atomic, assuming that the main database is not `:memory:` and the journal_mode is not WAL."

In WAL mode: transactions are atomic **per-database**, not across the whole attachment set. If you `BEGIN`, write to `main`, write to `c1`, then `COMMIT`, a crash mid-commit could leave `main` committed and `c1` not (or vice versa).

**For the `.lun` plug-in collection system**: cartridges are read-mostly. The only writes go to `memory_matrix.lun` (runtime DB). Don't start transactions that write to multiple attached databases simultaneously. If you need cross-cartridge atomicity, write to a staging area in main first, then propagate.

### Foreign keys across attached databases

Foreign keys do **not** work across attached databases. `REFERENCES c1.claims(claim_id)` is not valid — FK constraints can only reference tables in the same schema.

Cross-cartridge references must be application-enforced or enforced via triggers in the main DB.

### Limit on simultaneous attachments

`SQLITE_LIMIT_ATTACHED` defaults to 10, maximum is 125 (compile-time option). For the plug-in discovery system with many cartridges in a folder: the current `_discover_plugin_collections()` implementation must respect this limit, or the query must be batched across at most 10 concurrent attachments.

Practical recommendation: for a "query across the lot" scenario, use a two-step approach: iterate ATTACH/query/DETACH per cartridge, or pre-VACUUM all cartridges into one combined DB.

### VFS inheritance

Attached databases inherit the main database's VFS unless overridden with `vfs=NAME` in the URI. Relevant if cartridges use a custom VFS (e.g., encrypted or read-only overlay).

---

## Topic 7: FTS5 Deep Dive

### Shadow tables created by FTS5

When you `CREATE VIRTUAL TABLE nodes_fts USING fts5(...)`, SQLite creates these shadow tables:

| Table | Contents |
|-------|----------|
| `nodes_fts_data` | B-Tree segments of the full-text index |
| `nodes_fts_idx` | Segment index structure |
| `nodes_fts_content` | Copy of row content (unless external content mode) |
| `nodes_fts_docsize` | Per-document token counts (used for BM25 scoring) |
| `nodes_fts_config` | Configuration key-value pairs |

These shadow tables are part of the `.lun` format if `nodes_fts` is in the schema. Schema migrations must handle them.

### Tokenizer trade-offs for cartridge content

| Tokenizer | Best for | Notes |
|-----------|----------|-------|
| `unicode61` | General text (default) | Unicode 6.1, case-insensitive, handles accents correctly. Good default. |
| `porter` | English-language search where stemming is desirable | Reduces "running", "ran", "runs" to "run". Wrap around unicode61: `tokenize = 'porter unicode61'` |
| `trigram` | Substring matching, LIKE optimization | Breaks text into 3-char sequences. Enables `WHERE content LIKE '%substring%'` via FTS. Significantly larger index. |
| `ascii` | ASCII-only content, performance-sensitive | Treats non-ASCII as token chars, no Unicode normalization |

For cartridge `nodes_fts`: `unicode61` is the right choice for research content. Consider `trigram` if the use case requires substring matching (e.g., finding partial concept names). Do not combine `porter` + `trigram` — they're solving different problems.

### External content table pattern (verified pattern from docs)

Instead of storing content twice (in `nodes` table and in FTS shadow table), use external content:

```sql
-- Source table
CREATE TABLE nodes (
  node_id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  title   TEXT
);

-- External content FTS table (index only, no content copy)
CREATE VIRTUAL TABLE nodes_fts USING fts5(
  content, title,
  content='nodes',
  content_rowid='node_id'
);

-- Triggers to keep FTS in sync with nodes
CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, content, title) VALUES (new.node_id, new.content, new.title);
END;

CREATE TRIGGER nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, content, title)
  VALUES ('delete', old.node_id, old.content, old.title);
END;

CREATE TRIGGER nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, content, title)
  VALUES ('delete', old.node_id, old.content, old.title);
  INSERT INTO nodes_fts(rowid, content, title) VALUES (new.node_id, new.content, new.title);
END;
```

The `'delete'` command in the FTS virtual table is how you signal a deletion — you insert a special row with the 'delete' command and the old values. FTS5 removes that row from the index.

### FTS5 and schema migrations

When you migrate the `nodes` table (e.g., adding a column), the FTS shadow tables are **not automatically updated**. After any schema migration affecting the FTS-indexed columns:

```sql
-- Verify integrity (detects index/content mismatch):
INSERT INTO nodes_fts(nodes_fts) VALUES('integrity-check');

-- Full rebuild if needed:
INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild');
```

`rebuild` re-indexes all content from the content table from scratch. It's O(n) but correct. For write-once cartridges, run `rebuild` once at build time and skip the triggers.

**The migration footgun**: if you `DROP TABLE nodes` and recreate it during a table rewrite, the triggers on `nodes` are dropped. Triggers must be recreated as part of the migration. If you forget, FTS goes stale silently.

### BM25 ranking customization

FTS5 uses BM25 by default. Tunable parameters if you need to bias title vs content:

```sql
-- Give title 10x the weight of content
SELECT * FROM nodes_fts
WHERE nodes_fts MATCH 'query'
ORDER BY nodes_fts_rank('bm25(10.0, 1.0)');
```

---

## Topic 8: Read-Only / Read-Mostly SQLite Optimization

### Cartridge use case

Cartridges (`.lun` files) are **written once at build time, read many times in the field**. This is a fundamentally different access pattern from the runtime `memory_matrix.lun`. They should be optimized separately.

### Recommended pragma stack for cartridge distribution

**At build time (after all inserts, before distributing):**

```sql
-- Run ANALYZE to give query planner accurate statistics
PRAGMA optimize;

-- Switch from WAL to DELETE journal mode
-- WAL creates -wal and -shm sidecar files that break portability
PRAGMA journal_mode = DELETE;

-- Checkpoint to fold any WAL data into the main file (if was in WAL mode during build)
PRAGMA wal_checkpoint(TRUNCATE);

-- Set page size (must be done before any writes on an empty DB)
-- 4096 is the default and usually optimal; 8192 for large content cartridges
-- Cannot change after first write

-- Vacuum to reclaim space and defragment
VACUUM;
```

**At runtime (when opening a cartridge read-only):**

```sql
-- Connection-level read enforcement
PRAGMA query_only = 1;
-- Effect: any CREATE/DELETE/DROP/INSERT/UPDATE returns SQLITE_READONLY
-- Note: NOT the same as sqlite3_db_readonly() -- that function is unaffected
-- Note: checkpoint and COMMIT still work (soft enforcement)

-- Memory-mapped I/O for large cartridges
PRAGMA mmap_size = 268435456;  -- 256MB; tune to cartridge size
-- Effect: OS maps the file into process address space for zero-copy reads

-- Page cache size (default is -2 = 2MB; for read-heavy, increase)
PRAGMA cache_size = -8000;  -- 8MB (negative = kilobytes)
```

### WAL mode and read-only files: the portability trap

From docs (verified): WAL mode requires `-wal` and `-shm` sidecar files. For a distributed cartridge:

- If you distribute a WAL-mode `.lun`, the recipient needs a writable directory to create `-shm`
- If they can't create `-shm`, they must open with `?immutable=1` URI flag, which has limitations
- Recommendation from docs: "it is good practice to convert the database to `PRAGMA journal_mode=DELETE` prior to burning an SQLite database image onto read-only media"

**Conclusion: ship cartridges in DELETE journal mode, not WAL.**

### query_only vs immutable vs read-only file system

| Mechanism | Scope | Enforcement | Notes |
|-----------|-------|-------------|-------|
| `PRAGMA query_only = 1` | Connection | Soft (SQLite layer) | Reverts at connection close; `sqlite3_db_readonly()` unaffected |
| `?immutable=1` URI flag | Connection | Hard (no writes possible) | Bypasses all locking; no checkpointing; assumes file doesn't change externally |
| File system read-only (chmod 444) | OS level | Hard | SQLite will fail to open in write mode; WAL mode requires directory write permission |
| `SQLITE_OPEN_READONLY` flag | Connection | Hard | Standard read-only open; acquires shared lock only |

For the cartridge runtime open: use `SQLITE_OPEN_READONLY` at the C level (or the equivalent Python `mode='ro'` URI parameter: `file:cartridge.lun?mode=ro`). This is the correct semantic. `query_only` is for when you have a connection that might be used for both reads and writes but you want to enforce read-only in a particular context.

---

## Topic 9: SQLite WASM Ecosystem

### Official implementation

The canonical SQLite WASM build is `ext/wasm/` in the SQLite source tree (confirmed: `ext/wasm/api/sqlite3-wasm.c` present in sqlite-src-3530000). Built with Emscripten. Available at `sqlite.org/wasm`.

The official build provides:
- A JavaScript API close to the C API
- An `OPFS` (Origin Private File System) storage backend for persistent databases in browsers
- A `JNI` binding for Java (separate, in `ext/jni/`)

### Browser storage options

| Backend | Persistence | COOP/COEP required | Notes |
|---------|-------------|-------------------|-------|
| In-memory | Session only | No | Fastest; data lost on close |
| `localStorage`/`IndexedDB` (via sql.js) | Persistent | No | Requires serializing entire DB to ArrayBuffer; poor for large files |
| OPFS (official sqlite-wasm) | Persistent | **Yes** — needs `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp` | True file I/O; full SQLite functionality; correct choice for `.lun` viewer |
| OPFS-SAH pool (newer) | Persistent | Yes | Optimized pool approach; faster OPFS access |

### The path from "open .lun in SQLite Fiddle" to "ship a browser-based .lun viewer"

1. **SQLite Fiddle** (sqlite.org/fiddle): already runs the official WASM build. Users can upload a `.lun` file directly. This is available today, no development needed. Forces format spec to be clean enough for any standard SQLite tooling.

2. **Embedded viewer**: ship the official WASM + a thin React/vanilla JS wrapper. User drags `.lun` into browser, WASM parses it, viewer queries `SELECT * FROM claims`, renders. Requires COOP/COEP headers on your host. No backend needed.

3. **OPFS-backed**: for a persistent viewer where the user keeps cartridges locally in the browser, use OPFS. More complex setup but enables "open cartridge library in browser" UX.

### sql.js vs official sqlite-wasm

| | sql.js | Official sqlite-wasm |
|-|--------|---------------------|
| Age | Older, battle-tested | Newer (3.39+) |
| OPFS support | No | Yes |
| File API | Load entire DB as ArrayBuffer | True file I/O or OPFS |
| Extensions | Limited | Full (FTS5, JSON, etc.) |
| Size | ~1MB | ~1MB |
| Recommendation | Simple read-only queries on known-small DBs | Use for .lun viewer |

For a `.lun` viewer: use official sqlite-wasm. FTS5 and the full extension set are available. The format doesn't need any modifications for WASM compatibility.

---

## Topic 10: sqlite-vec — Performance, Indexing, and Failure Modes at Scale

### What sqlite-vec does

sqlite-vec is a SQLite extension that adds a virtual table for vector similarity search. Vectors are stored as typed BLOBs (float32 by default). Query:

```sql
SELECT rowid, distance
FROM vec_items
WHERE embedding MATCH ?  -- query vector as blob
ORDER BY distance
LIMIT 10;
```

### The exact-kNN wall

**The critical failure mode**: sqlite-vec performs **exact** nearest-neighbor search by default. Every query scans the entire vector table and computes distance to every stored vector. This is O(n) per query.

At different scales (approximate):

| Row count | Query time (rough) | Practical? |
|-----------|-------------------|------------|
| 1k–10k | <1ms | Fine |
| 50k | 5–50ms | Acceptable for interactive |
| 100k | 50–500ms | Borderline |
| 500k | 500ms–5s | Problematic |
| 1M+ | >5s | Not usable |

Eclissi at 53k nodes: you're in the "acceptable" range, but approaching the edge. At 100k+ (expected as Luna Engine accumulates more memories), latency will degrade noticeably.

### ANN support in recent versions

sqlite-vec has added IVF (Inverted File Index) support for approximate nearest neighbor search in more recent releases. This partitions vectors into clusters and searches only the nearest cluster(s). Build time is O(n) but query time becomes O(sqrt(n)) roughly.

```sql
-- Create an IVF index (approximate, must specify nlist = number of clusters)
CREATE VIRTUAL TABLE vec_items USING vec0(
  embedding float[768],
  +nlist=100  -- number of IVF clusters
);
```

**Verify the exact sqlite-vec version you're using** — the IVF API changed between releases.

### Storage format considerations

sqlite-vec stores vectors as binary BLOBs in shadow tables (`vec0_data`, `vec0_chunks`, etc.). The binary format is:
- 4 bytes header (type + dimensionality info)
- N × 4 bytes for float32 vectors

Dimensionality is fixed at table creation. You **cannot** change the embedding dimension after the table is created (it's baked into the serialization).

**Migration implication**: if you switch embedding models (e.g., from 384-dim to 768-dim), you must drop and recreate the vec0 table and re-embed all content. Plan for this in the migration spec.

### Float32 vs Float64

sqlite-vec defaults to float32 (4 bytes/dimension). Using float64 doubles storage with minimal quality benefit for most embedding models. Keep float32.

### Practical ceiling for Eclissi

At current 53k nodes: exact kNN is fine for typical query loads. Watch for degradation if:
- You run many concurrent queries (no parallelism in SQLite)
- Query vectors are high-dimensional (>1024 dims)
- You're doing real-time interactive search

Mitigation options in order of invasiveness:
1. Run `PRAGMA optimize` and ensure the vec0 shadow tables have good statistics (low effort)
2. Pre-compute and cache common query results (app-level caching)
3. Enable IVF indexing if your sqlite-vec version supports it
4. Move to a dedicated vector DB (e.g., FAISS, Qdrant) for the hot path while keeping sqlite-vec for the cold/archive path

---
