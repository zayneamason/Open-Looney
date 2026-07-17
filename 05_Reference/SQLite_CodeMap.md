# SQLite 3.53.0 — Annotated Code Map

> Source tree: `sqlite-src-3530000/`  
> Scope: files directly relevant to the 10 `.lun` format research questions.  
> All line numbers verified against 3.53.0 source.

---

## How to Read This Map

Each entry: `path/file.c` → what it owns → which research topics it's relevant to → annotated symbols.

Topic shorthand: **T1** = application_id, **T2** = PKs, **T3** = ledger/triggers, **T4** = CHECK/constraints, **T5** = migration, **T6** = ATTACH, **T7** = FTS5, **T8** = read-only, **T9** = WASM, **T10** = sqlite-vec.

---

## Core Source Files (`src/`)

### `src/pragma.c`
**Owns:** All PRAGMA statement execution. The single dispatch point for every `PRAGMA foo = bar`.

**Relevant to:** T1, T8

**Key symbols:**

| Symbol | Line | Notes |
|--------|------|-------|
| `sqlite3Pragma()` | 425 | Top-level dispatch — every PRAGMA routes through here |
| `PragTyp_HEADER_VALUE` case | 2324 | Handles `user_version`, `schema_version`, `application_id` — emits `OP_SetCookie` / `OP_ReadCookie` VDBE opcodes |
| `OP_SetCookie` / `OP_ReadCookie` | 2329–2362 | Direct header byte write — `iCookie` param selects the header offset (schema=40, user=60, app=68) |
| Defensive mode guard | 2342–2346 | `PRAGMA schema_version=VALUE` silently becomes `OP_Noop` in defensive mode — app_id and user_version not blocked |
| `pragmaVtabModule` | 3060 | PRAGMAs as virtual table — enables `SELECT * FROM pragma_table_info(...)` |
| `pragmaLocate()` | 311 | Binary search over sorted pragma name table |

**Annotation for T1:** The three "cookies" (schema=40, user=60, app=68) share the same read/write path (`PragTyp_HEADER_VALUE`). The only difference is which 4-byte offset in the header gets written. `schema_version` gets special protection in defensive mode; `user_version` and `application_id` do not. This confirms the documentation claim: SQLite never touches `user_version` or `application_id` — they're pure application territory.

---

### `src/trigger.c`
**Owns:** Trigger parsing, compilation, and code generation. The machinery behind append-only enforcement.

**Relevant to:** T3, T4

**Key symbols:**

| Symbol | Line | Notes |
|--------|------|-------|
| `sqlite3BeginTrigger()` | 104 | Parses `CREATE TRIGGER` — validates timing (BEFORE/AFTER), event, target table |
| `sqlite3FinishTrigger()` | 323 | Finalizes trigger, writes to `sqlite_schema` |
| `sqlite3CodeRowTrigger()` | 1454 | Entry point for trigger codegen at query execution time — called from INSERT/UPDATE/DELETE paths |
| `sqlite3CodeRowTriggerDirect()` | 1382 | Executes trigger steps inline — handles `RAISE()` via `ignoreJump` parameter |
| `ignoreJump` | 1388, 1463 | VDBE jump target for `RAISE(IGNORE)` — skips current row, does not abort |
| `sqlite3DeleteTrigger()` | 640 | Drops trigger from schema — relevant to migration: triggers must be explicitly recreated after table rewrites |
| `sqlite3UnlinkAndDeleteTrigger()` | 747 | Removes trigger from in-memory schema cache |

**Annotation for T3 (append-only):** `sqlite3CodeRowTrigger()` is invoked from the VDBE DELETE and UPDATE paths. A `BEFORE DELETE ... RAISE(ABORT, 'immutable')` causes the current statement to be rolled back before the row is touched — the VDBE never executes the actual delete. `RAISE(ROLLBACK, msg)` goes further and rolls back the entire transaction. For `.lun` ledger tables, `RAISE(ABORT)` is the right choice: it rejects the statement while leaving any surrounding transaction intact.

**Annotation for T5 (migration):** `sqlite3DeleteTrigger()` / `sqlite3UnlinkAndDeleteTrigger()` confirm what the docs say: triggers are attached to the original table name. A full-table rewrite (`CREATE new → INSERT SELECT → DROP old → RENAME new → old`) drops all triggers on `old`. The migration script must explicitly recreate them.

---

### `src/alter.c`
**Owns:** `ALTER TABLE` — rename, add column, drop column, add/drop constraints.

**Relevant to:** T5

**Key symbols:**

| Symbol | Line | Notes |
|--------|------|-------|
| `sqlite3AlterFinishAddColumn()` | 313 | Executes `ALTER TABLE ... ADD COLUMN` — contains the O(1) vs O(n) fork |
| `sqlite3AlterBeginAddColumn()` | 483 | Parses the new column definition, validates against ADD COLUMN restrictions |
| `sqlite3AlterRenameTable()` | 124 | `ALTER TABLE ... RENAME TO` — the last step in a full table rewrite migration |
| `sqlite3AlterRenameColumn()` | 599 | `ALTER TABLE ... RENAME COLUMN` — schema text rewrite, not data movement |
| `sqlite3AlterDropColumn()` | 2250 | `ALTER TABLE ... DROP COLUMN` (added 3.35.0) — triggers full table rewrite |
| `sqlite3AlterAddConstraint()` | 2983 | `ALTER TABLE ... ADD CONSTRAINT` (added 3.47.0) |
| `sqlite3AlterFunctions()` | 3042 | Registers internal rename helpers used during schema migration |

**Annotation for T5:** `sqlite3AlterFinishAddColumn()` is where the O(1)/O(n) split lives. If the new column has a CHECK constraint or a NOT NULL constraint (without DEFAULT), the function triggers a full schema validation scan — it tests every existing row against the constraint and returns `SQLITE_CONSTRAINT` on the first violation. The O(1) path (schema text only) is only taken when the column is fully unconstrained or has a literal DEFAULT.

---

### `src/attach.c`
**Owns:** `ATTACH DATABASE` / `DETACH DATABASE` and cross-DB schema fixup.

**Relevant to:** T6

**Key symbols:**

| Symbol | Line | Notes |
|--------|------|-------|
| `sqlite3Attach()` | 440 | Compiles `ATTACH DATABASE 'path' AS name` into VDBE |
| `sqlite3Detach()` | 420 | Compiles `DETACH DATABASE name` |
| `SQLITE_LIMIT_ATTACHED` check | 137 | `db->nDb >= db->aLimit[SQLITE_LIMIT_ATTACHED] + 2` — the +2 accounts for main + temp; limit defaults to 10 |
| `sqlite3FixSrcList()` | 562 | Walks query SrcList and reassigns DB references — called after ATTACH to fix up cross-DB queries |
| `sqlite3FixTriggerStep()` | 591 | Equivalent for trigger steps — confirms FK cross-DB limitation is structural |

**Annotation for T6:** The `+2` in the limit check (`nDb >= limit + 2`) means the effective default is 10 attached databases beyond `main` and `temp`. The `sqlite3FixSrcList()` / `sqlite3FixTriggerStep()` calls reveal why cross-DB foreign keys don't work: the FK enforcement code operates on schema-qualified table refs, but FK references in `CREATE TABLE` are never schema-qualified — they resolve to the current DB only. This is a parser-level constraint, not an enforcement gap.

---

### `src/wal.h` (+ `src/wal.c`)
**Owns:** WAL (Write-Ahead Log) mode interface — the second journal mode behind DELETE.

**Relevant to:** T8

**Key symbols from `wal.h`:**

| Symbol | Line | Notes |
|--------|------|-------|
| `sqlite3WalOpen()` | 59 | Creates WAL session — also creates the `-wal` and `-shm` sidecar files |
| `sqlite3WalClose()` | 60 | Closes WAL; `sync_flags` controls fsync behavior |
| `sqlite3WalBeginReadTransaction()` | 72 | Readers take a read lock; WAL frame visibility is determined here |
| `sqlite3WalCheckpoint()` | 101 | Moves WAL frames back to main DB; relevant to pre-distribution packaging |
| `sqlite3WalSnapshotGet()` | 133 | Snapshot isolation (read-only consistent view) |

**Annotation for T8:** `sqlite3WalOpen()` is why WAL mode is problematic for distributable `.lun` cartridges — the `-wal` and `-shm` files are created on first open and must travel with the database. `sqlite3WalCheckpoint()` with mode `SQLITE_CHECKPOINT_TRUNCATE` is the correct pre-packaging step: it merges all WAL frames back into the main file and truncates the sidecar to zero. After that, `PRAGMA journal_mode=DELETE` switches the mode header, making the file self-contained.

---

### `src/sqliteInt.h`
**Owns:** Master internal header — all core structs, flags, and compile-time limits.

**Relevant to:** T2, T4, T6

**Key symbols:**

| Symbol | Line | Notes |
|--------|------|-------|
| `TF_WithoutRowid` | 2489 | `0x00000080` flag on `Table.tabFlags` — set when `WITHOUT ROWID` is declared |
| `HasRowid(X)` macro | 2544 | `(((X)->tabFlags & TF_WithoutRowid)==0)` — used throughout query planner |
| `SQLITE_LIMIT_ATTACHED` | defined in `sqlite.h.in:4414` | `7` — the index into `db->aLimit[]`; default value is 10 |

**Annotation for T2:** `TF_WithoutRowid` and `HasRowid()` appear throughout the query planner and B-tree layer — every code path that touches table storage checks this flag. This confirms WITHOUT ROWID is not a query hint but a structural schema property baked in at table creation. It cannot be added later without a full table rewrite.

---

## Extension Files (`ext/`)

### `ext/misc/sha1.c`
**Owns:** `sha1(X)` and `sha1_query(Y)` SQL functions — loadable extension.

**Relevant to:** T3

**What it provides:**
- `sha1(X)` — SHA-1 hash of any value; returns 40-char hex TEXT
- `sha1_query(Y)` — evaluates SQL in Y, returns SHA-1 hash of the result set

**Annotation for T3:** This is the hash function you'd load for a ledger chain using SHA-1. The extension is not compiled in by default — it's a loadable `.so`/`.dylib`. For `.lun` format portability, this is a dependency concern: the hash must be computed at application layer (Python `hashlib`, etc.) and stored as TEXT, not computed inside SQLite. The `sha1_query` variant is useful for auditing: `sha1_query('SELECT * FROM ledger ORDER BY id')` gives a fingerprint of the entire ledger.

---

### `ext/misc/shathree.c`
**Owns:** `sha3(X, SIZE)`, `sha3_agg(Y, SIZE)`, `sha3_query(Z, SIZE)` SQL functions.

**Relevant to:** T3

**What it provides:**
- `sha3(X, SIZE)` — SHA3/Keccak hash; SIZE ∈ {224, 256, 384, 512}, default 256
- `sha3_agg(Y, SIZE)` — aggregate SHA3 over multiple rows (order matters; pair with ORDER BY)
- `sha3_query(Z, SIZE)` — hash of a query's result set; useful for ledger verification

**Annotation for T3:** SHA3 (not SHA-256) is the more modern choice. `sha3_agg()` with `ORDER BY rowid` gives a rolling hash over an append sequence — this is directly applicable to ledger verification. However: same portability caveat as SHA-1. For the `.lun` append-only ledger in SPEC-005, compute `prev_hash` at application layer before the INSERT, not inside a trigger. The trigger enforces the chain structure; the application computes the hash value.

---

### `ext/misc/uuid.c`
**Owns:** `uuid()`, `uuid_str(X)`, `uuid_blob(X)` SQL functions.

**Relevant to:** T2

**What it provides:**
- `uuid()` — generates a random UUID v4 as a 36-char TEXT (hyphenated)
- `uuid_str(X)` — converts 16-byte BLOB to text UUID
- `uuid_blob(X)` — converts text UUID to 16-byte BLOB

**Annotation for T2:** UUID v4 is random and not time-ordered — poor choice for WITHOUT ROWID tables because random insertion causes B-tree page splits on every write (worst case: page split per insert). ULID or UUIDv7 (both time-ordered) are better. This extension does not provide ULID or UUIDv7; those must be generated at the application layer.

---

### `ext/fts5/fts5.h` + `ext/fts5/fts5Int.h`
**Owns:** FTS5 public API and internal data structures for full-text search.

**Relevant to:** T7

**Key symbols from `fts5.h`:**

| Symbol | Lines | Notes |
|--------|-------|-------|
| `Fts5Tokenizer` typedef | 631 | Handle type for tokenizer instances |
| `fts5_tokenizer_v2` struct | 633–653 | Current tokenizer API (v2); replaces deprecated `fts5_tokenizer` |
| `xCreate` in `fts5_tokenizer_v2` | 636 | Factory: instantiates tokenizer from `azArg[]` config args |
| `xTokenize` in `fts5_tokenizer_v2` | 638 | Core tokenizer callback — called once per document during indexing and query parsing |
| Legacy `fts5_tokenizer` struct | 659–663 | Deprecated; `xCreate` / `xDelete` / `xTokenize` with older signature |

**Annotation for T7:** The v2 tokenizer API is the current interface — new custom tokenizers must use `fts5_tokenizer_v2`. The built-in tokenizers (unicode61, porter, trigram, ascii) are all registered at startup and selected by name in the `CREATE VIRTUAL TABLE` statement. For `nodes_fts` in the Luna Engine: `unicode61` is the right default; `trigram` only if substring search is needed (significantly larger index). Porter stemming can be composed on top of unicode61: `tokenize = 'porter unicode61'`.

---

### `ext/misc/cksumvfs.c`
**Owns:** Checksum VFS — wraps the default VFS to add 8-byte checksum to every page.

**Relevant to:** T3, T8

**What it provides:** Each database page gets an 8-byte checksum suffix. Detects silent corruption on read. Can be enabled with `PRAGMA checksum_verification=ON`.

**Annotation for T3/T8:** This is a defense-in-depth option for `.lun` cartridges, not a replacement for the hash-chained ledger. The ledger proves content integrity at row level; cksumvfs proves storage integrity at page level. For read-only distributed cartridges, both are relevant. Cksumvfs adds ~8 bytes per 4KiB page (0.2% overhead).

---

### `ext/misc/appendvfs.c`
**Owns:** AppendVFS — stores a SQLite database appended to the end of another file.

**Relevant to:** T1, T8

**What it provides:** Opens a database that lives in the tail of any file (after an arbitrary prefix). Access via URI: `file:image.png?vfs=apndvfs`.

**Annotation for T1/T8:** Interesting for `.lun` format: a self-describing `.lun` file could embed a small manifest or human-readable header before the SQLite magic bytes, then use AppendVFS to access the database portion. The `application_id` in the SQLite header still identifies the DB type; the prefix is opaque to SQLite. Niche use case, but documented here because it affects how `file(1)` magic detection works — the standard magic string at offset 0 won't match if there's a prefix.

---

### `ext/wasm/api/sqlite3-wasm.c`
**Owns:** The C-side of the official SQLite WASM build — entry point for the Emscripten compilation.

**Relevant to:** T9

**What it does:** Exposes SQLite's C API to the JavaScript layer via Emscripten's `EM_JS` / `EMSCRIPTEN_KEEPALIVE` annotations. The JS wrapper (`sqlite3.js`) then wraps these into the ergonomic `sqlite3.oo1.DB` API.

**Annotation for T9:** This file is not meant to be read as a library — it's a compilation target. The relevant surface for `.lun` browser viewer is the JS API, not the C source. Key point: OPFS (Origin Private File System) access requires `sqlite3-opfs-async-proxy.js` to be co-hosted because OPFS requires a dedicated worker. The sync wrapper (`OPFSCoopSyncVFS`) handles this, but it requires `SharedArrayBuffer` + COOP/COEP headers.

---

### `ext/rbu/sqlite3rbu.c`
**Owns:** RBU (Resumable Bulk Update) — applies large incremental updates without locking out readers.

**Relevant to:** T5, T8

**What it provides:** `sqlite3rbu_open()`, `sqlite3rbu_step()`, `sqlite3rbu_close()` — applies a batch of changes from an RBU database to a target database, resumably, without write-locking the target for the full duration.

**Annotation for T5:** RBU is not the right tool for `.lun` schema migrations (it handles data updates, not schema changes). It's relevant if Luna needs to apply large retroactive embeddings or bulk memory updates to Eclissi without blocking reads. Worth knowing exists.

---

## Header Files of Interest

### `src/sqlite.h.in` (generates `sqlite3.h`)
The public API header. The `.in` suffix means it's a template — version strings are substituted at build time.

**Relevant to:** All topics. Key defines:

| Define | Value | Notes |
|--------|-------|-------|
| `SQLITE_LIMIT_ATTACHED` | 7 (index) | Selects slot 7 in `aLimit[]`; runtime default value = 10 |
| `SQLITE_LIMIT_VARIABLE_NUMBER` | 9 | Max bind variables; default 999 |

### `src/sqliteLimit.h`
Compile-time maximums. Not the same as `SQLITE_LIMIT_*` runtime limits.

| Define | Default | Notes |
|--------|---------|-------|
| `SQLITE_MAX_ATTACHED` | 10 | Absolute ceiling for ATTACH count — cannot exceed at runtime |
| `SQLITE_MAX_COLUMN` | 2000 | Max columns per table |
| `SQLITE_MAX_LENGTH` | 1000000000 | Max string/blob length (1GB) |

### `src/wal.h`
WAL interface — see `src/wal.c` for implementation. All WAL state is opaque behind the `Wal*` handle. Only `pager.c` calls into this layer.

### `ext/fts5/fts5.h`
Public FTS5 API — tokenizer registration, auxiliary function registration, phrase query callbacks. Relevant when building a custom tokenizer for Luna's `nodes_fts`.

---

## Documentation Files (`sqlite-doc-3530000/`)

Annotated by relevance — these are the primary verification sources used in `SQLite_Research.md`.

| File | Topics | Why It Matters |
|------|--------|----------------|
| `pragma.html` | T1, T8 | `application_id`, `user_version`, `schema_version` semantics; `query_only`, `mmap_size` |
| `withoutrowid.html` | T2 | WITHOUT ROWID B-tree mechanics, performance guidance, row size caveat |
| `lang_altertable.html` | T5 | ADD COLUMN restrictions; O(1) vs O(n) table; DROP COLUMN semantics |
| `lang_createtrigger.html` | T3, T4 | RAISE() options, BEFORE vs AFTER semantics, undefined behavior note |
| `lang_createtable.html` | T4 | CHECK constraint semantics, NULL handling, subquery restriction |
| `lang_attach.html` | T6 | ATTACH atomicity footgun in WAL mode, FK limitation, limit |
| `fts5.html` | T7 | External content table pattern, tokenizer list, shadow tables, migration commands |
| `wal.html` | T8 | WAL sidecar files, read-only WAL, portability recommendation |
| `appfileformat.html` | T1, T2, T3 | Application file format best practices from SQLite authors |
| `magic.txt` | T1 | Canonical `application_id` registry — all registered values as of 3.53.0 |

---

## Quick Reference: Which Files to Read for Each Topic

| Topic | Primary Source Files | Primary Doc Files |
|-------|---------------------|-------------------|
| T1: application_id | `src/pragma.c:2324` | `pragma.html`, `magic.txt` |
| T2: Portable PKs | `src/sqliteInt.h:2489`, `ext/misc/uuid.c` | `withoutrowid.html` |
| T3: Append-only ledger | `src/trigger.c:104,1382`, `ext/misc/sha1.c`, `ext/misc/shathree.c` | `lang_createtrigger.html` |
| T4: CHECK + triggers | `src/trigger.c`, `src/sqliteInt.h` | `lang_createtable.html`, `lang_createtrigger.html` |
| T5: Migration | `src/alter.c:313,483`, `src/trigger.c:640,747` | `lang_altertable.html` |
| T6: ATTACH | `src/attach.c:137,440` | `lang_attach.html` |
| T7: FTS5 | `ext/fts5/fts5.h:631`, `ext/fts5/fts5Int.h` | `fts5.html` |
| T8: Read-only | `src/wal.h:59,101`, `src/pragma.c`, `ext/misc/cksumvfs.c` | `wal.html`, `pragma.html` |
| T9: WASM | `ext/wasm/api/sqlite3-wasm.c` | online only (not in local bundle) |
| T10: sqlite-vec | not in this source tree (separate repo) | not in local bundle |

---

## What's NOT in This Source Tree

- **sqlite-vec** — separate project (`asg017/sqlite-vec`); not bundled with SQLite core. IVF/ANN support lives in that repo's `sqlite-vec.c`.
- **WASM documentation** — online at `sqlite.org/wasm`; only the C compilation target (`sqlite3-wasm.c`) is local.
- **JSON5 / JSONB** — new in 3.45.0, in `src/json.c` (not examined here; not relevant to .lun format research).
- **sqlite3_recover extension** (`ext/recover/`) — relevant if `.lun` files need corruption recovery tooling; not examined.
