# SPEC-011: LUNM format-invariant DDL ratification

**Status:** implemented (2026-07-24; Luna Engine PR #159 merge `629679b5`)
**Severity:** high
**Author:** Ahab (with Claude)
**Created:** 2026-07-24
**Last updated:** 2026-07-24 (Engine FI column conformance landed; moved to implemented/)
**Affects format version:** LUNM v0.1 (no `user_version` bump anticipated — additive ratification of already-shipped columns; see § Migration path)

---

## Problem statement

[SPEC-008](../implemented/SPEC-008_lunm-family-foundation.md) named eight **format-invariant** tables that every LUNM file MUST contain, but intentionally deferred their column-level DDL. [SPEC-009](../implemented/SPEC-009_lunm-schema-ownership.md) recorded ownership (`luna.substrate` / `schema.sql`) and classification without ratifying CREATE statements. Tools and future Engine work therefore still treat FI shape as “whatever is in `schema.sql` today,” with no reviewable contract a spec author or third-party auditor can cite. SPEC-011 ratifies that DDL for the eight tables only.

## Observed evidence

All evidence taken 2026-07-24 against Luna Engine `main` merge `c5c451fa` (post SPEC-010 PR #158) and the live matrix at `data/user/memory_matrix.lun` (`application_id = 1280659021`, `user_version = 2`).

- **SPEC-010 soak (precondition).** `scripts/soak_spec010_migration_integrity.py` opened a **copy** of the live matrix: `[MIGRATION-INTEGRITY] ran=23 noop=2 degraded=0` — PASS. No known failing format-invariant migration at draft time.
- **Column parity.** For each of the eight FI tables, live `PRAGMA table_info` column *names* match the `CREATE TABLE` in `src/luna/substrate/schema.sql` exactly (0 only-live / 0 only-schema). Full tables: [`04_Audits/AUDIT_2026-07-24_lunm-fi-pragma-table-info.md`](../../04_Audits/AUDIT_2026-07-24_lunm-fi-pragma-table-info.md).
- **Manifest rows.** SPEC-009 manifest lists all eight as `owner = "luna.substrate"`, `classification = "format-invariant"`, `ddl = "schema.sql"`.
- **`schema.sql` anchors** (Engine `c5c451fa`):

| Table | Lines | Live cols |
| --- | --- | --- |
| `memory_nodes` | 9–37 | 24 |
| `conversation_turns` | 41–64 | 14 |
| `graph_edges` | 67–80 | 9 |
| `sessions` | 93–100 | 6 |
| `profile_config` | 104–112 | 6 |
| `nexus_nodes` | 497–503 | 5 |
| `nexus_edges` | 505–512 | 4 |
| `nexus_registry` | 514–534 | 16 |

- **`conversation_turns.session_id` has no FOREIGN KEY** to `sessions` in `schema.sql` (SPEC-008 Q5 already recorded this as convention-only). Live matrix matches.

## Root cause analysis

SPEC-008 correctly refused to freeze ~700 lines of mixed-owner DDL. SPEC-009 then made ownership enumerable. The remaining gap is deliberate but now actionable: the eight FI tables are stable enough (parity with live production; soak clean) that ratifying their CREATE shapes closes the “named but shapeless” hole without attempting the other 81 tables.

## Proposed solution

### 4.1 Scope

This spec ratifies **only** the eight format-invariant tables of SPEC-008 § 4.3. Owner: `luna.substrate`. Authoritative DDL source for v0.1: Engine `schema.sql` at the commit named in Implementation notes (draft baseline: `c5c451fa`).

**Out of scope:** all other `luna.substrate` engine-extension tables; `ih_*` / `intergalactic_hub.storage`; vestigial `consciousness_snapshots`; FTS5/vec0 shadow tables (owned via SPEC-009 parent rule); path-loaded `migrations/004_*`.

### 4.2 Normative column contract

A LUNM v0.1 file MUST present the columns listed below for each FI table (names and nullability as in the audit). Types MUST be SQLite-compatible with the `schema.sql` declarations; storage-class coercion follows SQLite rules.

**MUST columns for SPEC-008 identity / discrimination consumers**

| Table | Columns that are load-bearing for shipped contracts |
| --- | --- |
| `profile_config` | `key`, `value`, `value_type`, `updated_at` — hosts `lunm.*` keys (SPEC-008 Q1/Q2) |
| `nexus_registry` | `collection_key`, `lun_path`, `ingestion_pattern`, `mounted`, `family`, `user_version`, `source` — Nexus dynamic mounting + §4.4 identity consumers |
| `nexus_nodes` / `nexus_edges` | PKs and FKs as in `schema.sql` — promotion graph |
| `memory_nodes` | `id`, `node_type`, `content`, `created_at`, `updated_at` — substrate primary |
| `graph_edges` | `from_id`, `to_id`, `relationship` (+ FKs to `memory_nodes`) |
| `conversation_turns` | `id`, `session_id`, `role`, `content`, `created_at`, `turn_type` |
| `sessions` | `session_id`, `started_at` |

Remaining columns in the CREATE blocks are **SHOULD** for Engine feature compatibility but are still part of the ratified FI shape for this owner (changing or removing them is a contract question under SPEC-008 Q4).

### 4.3 Normative DDL (by reference + appendix)

The normative CREATE TABLE text for each FI table is the `CREATE TABLE IF NOT EXISTS …` block in Engine `schema.sql` at the pinned commit, reproduced in **Appendix A**. Indexes declared immediately with those tables in `schema.sql` (e.g. `idx_profile_config_updated_at`, nexus indexes) are part of the ratified surface as SHOULD (absence on an otherwise-valid matrix is not a family discriminator failure; SPEC-009 presence rules still apply to the tables themselves).

### 4.4 `lunm.schema_fingerprint` (shape hand-back)

Per SPEC-008 Q2 / SPEC-009: fingerprint is **not** a hash of `schema.sql`. For the FI owner slice, SPEC-011 defines the contribution as:

> SHA-256 hex of the UTF-8 bytes of `\n`-joined `name\tsql` lines from `sqlite_master`, for the eight FI **tables** plus their Appendix A **non-autoindexes**, sorted by `name`. (Q3.)

The full `lunm.schema_fingerprint` value remains a SPEC-008 amendment at Engine implement time; SPEC-011 only fixes the FI owner’s input set.

### Schema changes

None at draft. Ratification describes already-shipped DDL. If acceptance discovers a live/schema mismatch requiring Engine change, that change ships under SPEC-010 discipline and may trigger SPEC-008 Q4 bump rules.

### Behavioral changes (Engine, after accept)

1. Add a conformance test: fresh matrix and/or live fixture — FI `PRAGMA table_info` names equal ratified set.
2. Optionally stamp / document `lunm.schema_fingerprint` FI contribution (SPEC-008 follow-up).
3. Do **not** add a FOREIGN KEY from `conversation_turns.session_id` → `sessions` (Q1: convention-only for v0.1).

### Migration path

Forward-compatible if live matrices already match (verified 2026-07-24). No `user_version` bump for ratification alone.

## Validation rules

```python
# Pseudocode — Engine test after accept
FI = {...}  # eight names
for name in FI:
    live_cols = {row.name for row in pragma_table_info(name)}
    assert live_cols == RATIFIED_COLUMNS[name]
```

SPEC-009 §4.4 remains the authority for table *presence*. SPEC-011 is the authority for FI *columns*.

## Governance implications

- **Ledger / annotation events:** N/A (LUNM).
- **Cross-cartridge traversal:** Strengthens SPEC-008 §4.4 consumers that read `profile_config` / nexus tables by freezing their shapes.
- **Memory Matrix integration:** First SPEC-011+ slice; later specs ratify other owners the same way.

## Alternatives considered

- **(a) Ratify all 52 `luna.substrate` tables now.** Rejected — EE surface is large and still migrating; FI-first matches SPEC-008’s cut.
- **(b) Start with `ih_*`.** Rejected for first slice — conditional classification; Hub-gated; not format-invariant.
- **(c) Ratify by English prose only (no DDL appendix).** Rejected — auditors need citeable CREATE text.

## Resolved questions

Each Q below was resolved ahead of the `active → accepted` promotion. Question bodies are preserved; each `**Resolution (2026-07-24):**` records what was picked.

1. **Should `conversation_turns.session_id` gain a FOREIGN KEY to `sessions`?** SPEC-008 left this convention-only after finding ad-hoc writers. Adding an FK is a contract-affecting schema change (likely SPEC-008 Q4 bump). **Recommendation:** leave convention-only in v0.1 ratification; open a follow-up Engine audit of writers before any FK.

   **Resolution (2026-07-24):** Leave convention-only for v0.1 ratification — no FK in Appendix A. Track a follow-up Engine writers audit (HistoryManager, MemoryMatrix, Guardian raw INSERT in `server.py`, tests) before any future FK; inherited from SPEC-008 Q5. `PRAGMA foreign_keys=ON` on `MemoryDatabase` means a real FK would enforce immediately.

2. **Are additive EE-driven columns on FI tables (e.g. future `memory_nodes` columns) allowed without a SPEC-011 amendment?** SPEC-008 Q4 says additive columns do not bump `user_version`. **Recommendation:** additive columns on FI tables REQUIRE a SPEC-011 amendment (or a dated appendix revision) even when they do not bump `user_version`, so the ratified column set does not silently drift. Engine MAY ship the column behind SPEC-010 before the amendment lands only if classified carefully — prefer amendment-first.

   **Resolution (2026-07-24):** Additive columns on FI tables REQUIRE a SPEC-011 amendment (or dated Appendix A revision) even when they do not bump `user_version`. **Amendment-first:** the amendment must land before or in the same change set as the Engine column; no silent ship-then-amend. SPEC-008 Q4 bump triggers are unchanged.

3. **Exact serialization for the FI fingerprint contribution** (canonical JSON of `{table: sql}` vs SQLite `sqlite_master` order)? **Recommendation:** sorted table name → `sql` text from `sqlite_master` where `type='table'`, UTF-8, `\n`-joined `name\tsql` lines; hash algorithm SHA-256 hex. Confirm at acceptance against Engine helper.

   **Resolution (2026-07-24):** SHA-256 hex of UTF-8 bytes of `\n`-joined `name\tsql` lines from `sqlite_master`, for the eight FI **tables** plus Appendix A **non-autoindexes**, sorted by `name`. No Engine fingerprint helper exists yet — this locks the format before one is written. Aligns with §4.4 (indexes included). Full `lunm.schema_fingerprint` stamping remains a SPEC-008 follow-up.

4. **Pin policy:** must Implementation notes always name an Engine commit SHA for `schema.sql`, or is “main at date” enough? **Recommendation:** always pin SHA (mirror SPEC-009/010 implementation notes).

   **Resolution (2026-07-24):** Always pin an Engine commit SHA for `schema.sql` in Implementation notes. Never “main at date.” Matches SPEC-009/010 house style.

## Dependencies

**Upstream (must be implemented):**

- [SPEC-008](../implemented/SPEC-008_lunm-family-foundation.md) — names the eight tables
- [SPEC-009](../implemented/SPEC-009_lunm-schema-ownership.md) — ownership + classification
- [SPEC-010](../implemented/SPEC-010_lunm-migration-discipline.md) — how DDL changes ship; soak clean 2026-07-24

**Downstream:**

- Engine conformance test + optional fingerprint stamping after accept
- Later SPEC-011+ siblings for other owners (`luna.substrate` EE, `intergalactic_hub.storage`, …)

## Implementation notes

- Commit/PR reference: Luna Engine PR [#159](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/159) merge `629679b5` (`spec-011-fi-column-conformance`). Artifact: `tests/unit/test_spec011_fi_columns.py` (fresh-matrix FI `PRAGMA table_info` names ≡ Appendix A). Authoritative DDL pin remains `schema.sql` at ancestor `c5c451fa` (FI CREATE blocks unchanged through `629679b5`).
- Implementation date: 2026-07-24
- Deviations from spec: none. Fingerprint stamping not implemented (SPEC-008 follow-up; Q3 locks serialization only).
- Follow-up issues created: Engine writers audit before any `conversation_turns.session_id` → `sessions` FK (Q1); SPEC-008 amendment for full `lunm.schema_fingerprint` stamp.

---

## Appendix A — Normative CREATE blocks (Engine `c5c451fa`)

Source: `_LunaEngine_BetaProject_V2.0_Root/src/luna/substrate/schema.sql`. Verbatim at draft time; line anchors above.

### A.1 `memory_nodes` (lines 9–37)

```sql
CREATE TABLE IF NOT EXISTS memory_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    source TEXT,
    confidence REAL DEFAULT 1.0,
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    reinforcement_count INTEGER DEFAULT 0,
    lock_in REAL DEFAULT 0.15,
    lock_in_state TEXT DEFAULT 'drifting',
    last_accessed TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,
    scope TEXT NOT NULL DEFAULT 'global',
    classification TEXT DEFAULT 'public',
    custodian TEXT,
    access_roles TEXT,
    consent_status TEXT DEFAULT 'granted',
    consent_date TEXT,
    review_status TEXT DEFAULT 'current',
    date_shared TEXT,
    namespace TEXT NOT NULL DEFAULT 'active'
);
```

### A.2 `conversation_turns` (lines 41–64)

```sql
CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,
    turn_type TEXT NOT NULL DEFAULT 'NORMAL_USER_TURN',
    tier TEXT DEFAULT 'active',
    compressed TEXT,
    compressed_at REAL,
    archived_at REAL,
    context_refs TEXT,
    thread_id TEXT
);
```

### A.3 `graph_edges` (lines 67–80)

```sql
CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    strength REAL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT,
    scope TEXT NOT NULL DEFAULT 'global',
    origin TEXT NOT NULL DEFAULT 'user',
    FOREIGN KEY (from_id) REFERENCES memory_nodes(id),
    FOREIGN KEY (to_id) REFERENCES memory_nodes(id),
    UNIQUE(from_id, to_id, relationship)
);
```

### A.4 `sessions` (lines 93–100)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL,
    app_context TEXT,
    turns_count INTEGER DEFAULT 0,
    metadata TEXT
);
```

### A.5 `profile_config` (lines 104–112)

```sql
CREATE TABLE IF NOT EXISTS profile_config (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    value_type   TEXT NOT NULL DEFAULT 'string'
                     CHECK (value_type IN ('string','int','float','bool','json')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by   TEXT,
    description  TEXT
);
```

### A.6 `nexus_nodes` / `nexus_edges` / `nexus_registry` (lines 497–534)

```sql
CREATE TABLE IF NOT EXISTS nexus_nodes (
    nexus_node_id TEXT PRIMARY KEY,
    collection_key TEXT NOT NULL,
    satellite_node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nexus_edges (
    src_node_id TEXT NOT NULL,
    dst_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    FOREIGN KEY (src_node_id) REFERENCES nexus_nodes(nexus_node_id),
    FOREIGN KEY (dst_node_id) REFERENCES nexus_nodes(nexus_node_id)
);

CREATE TABLE IF NOT EXISTS nexus_registry (
    collection_key TEXT PRIMARY KEY,
    lun_path TEXT NOT NULL,
    ingestion_pattern TEXT NOT NULL,
    lock_in REAL DEFAULT 0.15,
    access_count INTEGER DEFAULT 0,
    annotation_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    mounted INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT,
    validation_status TEXT,
    validation_reason TEXT,
    family TEXT,
    user_version INTEGER,
    source TEXT NOT NULL DEFAULT 'yaml'
);
```

### A.7 Non-autoindexes (SHOULD; fingerprint contribution)

Declared in `schema.sql` with the FI tables at `c5c451fa`. Absence on an otherwise-valid matrix is not a family discriminator failure (§4.3). Included in the FI fingerprint contribution (Q3 / §4.4).

| Index | On |
| --- | --- |
| `idx_nodes_type` | `memory_nodes(node_type)` |
| `idx_nodes_created` | `memory_nodes(created_at)` |
| `idx_nodes_importance` | `memory_nodes(importance DESC)` |
| `idx_nodes_accessed` | `memory_nodes(last_accessed DESC)` |
| `idx_nodes_lock_in` | `memory_nodes(lock_in DESC)` |
| `idx_nodes_lock_in_state` | `memory_nodes(lock_in_state)` |
| `idx_nodes_classification` | `memory_nodes(classification)` |
| `idx_nodes_custodian` | `memory_nodes(custodian)` |
| `idx_turns_session` | `conversation_turns(session_id)` |
| `idx_turns_created` | `conversation_turns(created_at)` |
| `idx_turns_tier_timestamp` | `conversation_turns(tier, created_at DESC)` |
| `idx_turns_session_tier` | `conversation_turns(session_id, tier)` |
| `idx_turns_turn_type` | `conversation_turns(turn_type)` |
| `idx_turns_thread_id` | `conversation_turns(thread_id)` |
| `idx_sessions_started` | `sessions(started_at DESC)` |
| `idx_edges_from` | `graph_edges(from_id)` |
| `idx_edges_to` | `graph_edges(to_id)` |
| `idx_edges_relationship` | `graph_edges(relationship)` |
| `idx_profile_config_updated_at` | `profile_config(updated_at DESC)` |
| `idx_nexus_nodes_collection` | `nexus_nodes(collection_key)` |
| `idx_nexus_edges_src` | `nexus_edges(src_node_id)` |
| `idx_nexus_edges_dst` | `nexus_edges(dst_node_id)` |
