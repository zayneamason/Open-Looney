---
doc_type: breakdown
status: active
created: 2026-07-24
updated: 2026-07-24
description: LUNM runtime matrix — identity, format-invariant vs. engine-extension tables, what's stored vs. not, threads and entities in detail
tags:
  - lunm
  - runtime-matrix
  - breakdown
---

# LUNM: the runtime matrix

The reference breakdown for this wiki's `sections/` — every future subsystem
page follows this shape: **definition → classification → what's stored vs.
not → per-item detail → citations**.

Two kinds of claim appear below, and they are not interchangeable. **(a)**
facts already on record in a spec, cited by document and section — durable,
re-check the spec if you doubt them. **(b)** facts that exist only by querying
the live matrix — dated, reproducible, and liable to have moved by the time
you read this. Section §6 states exactly how to reproduce every §5 number.

## 1. Definition

LUNM is Luna's runtime substrate: memory nodes, graph edges, conversation
turns, and the Nexus cross-cartridge pointer-graph, in one SQLite file mutated
in place for the life of a profile. It is the sibling family to the cartridge
(`LUNC`) — same `.lun` extension, same SQLite container, disjoint schema and
disjoint lifecycle. *(a: [SPEC-006](../../../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md);
[SPEC-008](../../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md) §4.1–4.2)*

Unlike a cartridge, a LUNM file:

- is created once at profile init and never rebuilt or shipped
- is mutated for the life of the profile under `journal_mode=WAL`
- has cooperating first-party writers (the engine plus a set of Intergalactic
  Hub daemons), not a single builder, and no third-party writer is permitted

*(a: SPEC-008 §4.2, all five lifecycle invariants)*

## 2. Identity

| Property | Value | Source |
|---|---|---|
| `PRAGMA application_id` | `0x4C554E4D` (`1280659021`, ASCII `'LUNM'`) | (a) SPEC-006 |
| `PRAGMA user_version` | `2` (LUNM v0.1 baseline) | (a) SPEC-008 §4.1 |
| `lunm.format_version` | `0.1` | (b) live header, §6.1 |
| `lunm.matrix_ulid` | `01KY8KK2A4VVQ8VB2NS0NQP5CD` | (b) live header, §6.1 — profile-specific, will differ per install |
| `lunm.engine_version` | `2.0.0` | (b) live header, §6.1 |

**The `lunm.created_at` wart.** The header key reads
`2026-07-23T23:04:54.852971+00:00`, but the oldest `memory_nodes` row in the
same file is dated `2026-07-07T13:19:28.345722` — 16 days earlier. The key
records when `_seed_lunm_header()` first ran on this file, not when the
matrix was actually created; on any matrix that existed before that seeding
routine shipped, the two dates diverge. The name implies matrix-birth
provenance it cannot provide. *(b: §6.1; the routine itself is (a):
SPEC-008 §Behavioral changes item 3)*

## 3. Table classification

A LUNM file at format version v0.1 holds 89 tables on a normal install, of
which exactly 8 are format-invariant — assumable by any tool that reads or
writes a LUNM file. The other 81 are engine extensions, conditional, or
vestigial (see [`../TAXONOMY.md`](../TAXONOMY.md) for the four-way
definition). `schema.sql` itself declares only 47 — the other 42 arrive from
more than twenty other files across the engine. *(a: SPEC-009 §Observed
evidence; live counts re-confirmed (b), §6.1)*

| Table | Role | Classification |
|---|---|---|
| `memory_nodes` | Primary node table — typed content, lock-in score, classification; FTS5-indexed | format-invariant |
| `graph_edges` | Directed relationships between `memory_nodes` rows | format-invariant |
| `conversation_turns` | Turn-by-turn history, scoped to `sessions` by convention only — no FOREIGN KEY | format-invariant |
| `sessions` | Conversation session records; `metadata` is a correctness surface for episodic recall | format-invariant |
| `nexus_nodes` | Nexus pointer-graph: cross-cartridge node identities promoted into the matrix | format-invariant |
| `nexus_edges` | Directed relationships between `nexus_nodes` rows | format-invariant |
| `nexus_registry` | Per-collection cartridge registration and dynamic-mount state | format-invariant |
| `profile_config` | Typed key/value config; hosts the `lunm.*` identity keys | format-invariant |

*(a: SPEC-008 §4.3, the exhaustive list)*

## 4. What's stored vs. not

**Not stored, by design:**

- **`meta`** — deliberately absent. The cartridge-side `meta` convention is
  write-once-at-build; a substrate mutated for a profile's lifetime cannot
  make that provenance claim honestly. `profile_config` fills the gap
  instead. *(a: SPEC-008 §4.2 point 2)*
- **`annotation_ledger`** — LUNC-only. The matrix has no append-only ledger
  or head-pointer chain.
- **`nexus_refs`** — the v0.3 format spec describes it, but the engine
  deliberately writes only the master `nexus_nodes` pointer on the sealed-LUNC
  branch. Reconciling the two descriptions is a named SPEC-009 follow-up, not
  yet resolved.

**Present but empty on this install** *(b, §6.1 — point-in-time, may not hold
on every install)*:

- `consciousness_snapshots` — 0 rows, zero readers or writers repo-wide;
  classified `vestigial`.
- `nexus_edges` — 0 rows; the pointer graph has nodes but no edges yet on
  this profile.

## 5. Threads and entities, in detail

### Threads — 5 tables, all `engine-extension`

| Table | Role |
|---|---|
| `threads` | Thread record: `id`, `topic`, `status` (active/parked/closed/merged), `project_slug`, timestamps, `resume_count`, `metadata_json`, `parent_thread_id` (fork lineage) |
| `thread_events` | Event-sourced log keyed by `event_id`/`thread_id`/`parent_event_id` — this is where thread content actually accumulates |
| `thread_tasks` | Thread ↔ task linkage |
| `thread_topics` | Thread ↔ topic linkage |
| `topology_cluster_threads` | Cluster membership |

**A live defect: duplicate DDL that has already drifted.** `threads` is
declared twice inside the same owning module (`luna.substrate`) — once in
`schema.sql`, once again inside a `_migrate_thread_parent_column`-adjacent
helper in `database.py`, both as `CREATE TABLE IF NOT EXISTS`. Current source
at Engine `629679b5`:

- `substrate/schema.sql:727-744` — 11 columns, including `parent_thread_id`
  in the base `CREATE TABLE`, and a status comment reading
  `-- active, parked, closed, merged`.
- `substrate/database.py:545-556` — 10 columns, **missing**
  `parent_thread_id` entirely.

Because both use `IF NOT EXISTS`, whichever one ran first on a given file
wins, and the other becomes permanently cosmetic on that file. The live
matrix proves this already happened: its actual `CREATE TABLE` (read from
`sqlite_master`, ground truth, not either source copy) has 10 base columns
plus a *trailing* `, parent_thread_id TEXT)` outside the original paren list —
the signature of a later `ALTER TABLE ADD COLUMN`, not the `schema.sql`
version. Its status comment reads `-- active, parked, closed` — matching
*neither* current source file (one has no comment, one says
`..., merged`). The live table was created by an older `schema.sql`, frozen
by `IF NOT EXISTS`, then patched by a migration helper; every later edit to
`schema.sql`'s column list is invisible on this file unless a matching
migration helper exists for it too. *(b: §6.2 for the live DDL; the two
source citations are (a)-style file:line but re-verified fresh at Engine
`629679b5`, §6.2)*

### Entities — 5 LUNM-family tables, plus two same-named tables in other families

| Table | Role |
|---|---|
| `entities` | Slug primary key (`user_001`, `marzipan`); `entity_type` (person/persona/place/project/topic); `aliases` and `core_facts` JSON; `full_profile` Markdown; `origin`; `namespace` (active/archived) |
| `entity_mentions` | Occurrence records |
| `entity_relationships` | Typed relations between entities |
| `entity_versions` | Version history against `entities.current_version` |
| `entity_relationship_evidence` | Provenance for relations — `memory_node_id`, `source_id`, `provenance`, `confidence` |

Owner resolved as `luna.substrate` / `schema.sql` for the whole family. *(a:
SPEC-009 §4.1, "LUNM entity family (resolved 2026-07-23)")*

**Three tables share the name `entities` across different families** — only
the first is LUNM:

1. `substrate/schema.sql` — **LUNM matrix.** The table above.
2. `substrate/aibrarian_schema.py` — **cartridge/collection DB.** Different
   columns (`doc_id`, `entity_value`). Out of LUNM scope.
3. `intergalactic_hub/storage/db.py` (`ih_entities`) — a fourth namespace
   living in the *same* matrix file: integer autoincrement PK,
   `(kind, canonical_name)` unique pair. Classified `conditional` — present
   only when the Hub subsystem loads. 0 rows on this install.

*(a: SPEC-009 §Observed evidence, the name-collision finding)*

## 6. Evidence appendix — fresh, dated, reproducible

Run against `_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun`
on **2026-07-24**. These are point-in-time facts about one profile's live
file; re-run the commands below against your own matrix to check whether the
numbers moved.

### 6.1 Identity, header, and row counts

```
$ sqlite3 memory_matrix.lun "select * from pragma_application_id;"
1280659021
$ sqlite3 memory_matrix.lun "select * from pragma_user_version;"
2
$ sqlite3 memory_matrix.lun "select count(*) from sqlite_master where type='table';"
89
$ sqlite3 memory_matrix.lun "select key||' = '||value from profile_config order by key;"
lunm.created_at = 2026-07-23T23:04:54.852971+00:00
lunm.engine_version = 2.0.0
lunm.format_version = 0.1
lunm.matrix_ulid = 01KY8KK2A4VVQ8VB2NS0NQP5CD
$ sqlite3 memory_matrix.lun "select min(created_at) from memory_nodes;"
2026-07-07T13:19:28.345722
```

Row counts, format-invariant tables: `memory_nodes` 626, `graph_edges` 2538,
`conversation_turns` 472, `sessions` 71, `nexus_nodes` 3, `nexus_edges` 0,
`nexus_registry` 2, `profile_config` 4.

Row counts, threads family: `threads` 74, `thread_events` 622,
`thread_tasks` 0, `thread_topics` 0, `topology_cluster_threads` 0.

Row counts, entities family: `entities` 37, `entity_mentions` 226,
`entity_relationships` 28, `entity_versions` 81,
`entity_relationship_evidence` 38, `ih_entities` 0.

Absence check — `meta`, `annotation_ledger`, `nexus_refs`, `access_bridge`,
`permission_log`: `select name from sqlite_master where name in (...)` returns
zero rows; all five confirmed absent. `consciousness_snapshots`: 0 rows.

### 6.2 `threads` DDL drift

Live table, from `sqlite_master` (ground truth for what the file actually
contains, independent of either source copy):

```sql
CREATE TABLE threads (
    id             TEXT PRIMARY KEY,
    topic          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',  -- active, parked, closed
    project_slug   TEXT,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    parked_at      TEXT,
    resumed_at     TEXT,
    closed_at      TEXT,
    resume_count   INTEGER NOT NULL DEFAULT 0,
    metadata_json  TEXT
, parent_thread_id TEXT)
```

Engine source, re-verified at HEAD `629679b5` (line numbers had already moved
since an earlier read the same day — the file is under active development):

- `src/luna/substrate/schema.sql:727-744` — 11 columns, `parent_thread_id` in
  the base `CREATE TABLE`, comment `-- active, parked, closed, merged`.
- `src/luna/substrate/database.py:545-556` — 10 columns, no
  `parent_thread_id`, no comment.

Neither source copy matches the live DDL exactly, which is the point: the
live file is the union of whichever `CREATE TABLE IF NOT EXISTS` ran first
and whatever `ALTER TABLE` migrations ran afterward — not a mirror of either
declaration as currently written.
