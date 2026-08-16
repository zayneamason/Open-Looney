# LUNM Inspector Prototype - Spec

**Status:** implemented MVP (v0.1.0, 2026-07-27)
**Owner:** Ahab
**Validates:** LUNM runtime matrix family (`application_id = 0x4C554E4D`, `PRAGMA user_version >= 2`)
**References:** SPEC-008 LUNM foundation, SPEC-011 format-invariant DDL, SPEC-012 entity boundary

---

## Purpose

The LUNM Inspector is a standalone, read-only desktop prototype for opening a
Luna runtime matrix (`memory_matrix.lun`) and inspecting the stable
format-invariant surface. It is deliberately separate from
`06_Prototypes/ReaderPrototype/`, which remains a LUNC cartridge reader and must
continue rejecting LUNM files.

LUNM is mutated in place for the life of a profile. This prototype therefore has
no write, repair, migration, export, or live Engine attachment path.

## Current app version

**v0.1.0** implements:

- LUNM-only open contract:
  - SQLite header check.
  - `PRAGMA application_id = 0x4C554E4D`.
  - `PRAGMA user_version >= 2`.
  - `SQLITE_OPEN_READ_ONLY` plus `PRAGMA query_only = 1`.
- Overview surface:
  - path, application id, user version, `lunm.format_version`,
    `lunm.matrix_ulid`, `lunm.created_at`, `lunm.engine_version`.
  - row counts and presence for the eight format-invariant tables.
- Health surface:
  - missing FI table/column errors.
  - missing `lunm.format_version` warning.
  - missing or malformed `lunm.matrix_ulid` warning.
  - `user_version = 2` / `lunm.format_version != 0.1` drift warning.
- Read-only inspection tabs:
  - Memory: `memory_nodes`.
  - Graph: `graph_edges` joined to source/target memory-node content.
  - Conversations: `sessions` with computed `actual_turns`, clickable session
    rows, and readable chronological `conversation_turns` by `session_id`.
  - Nexus: `nexus_registry`, `nexus_nodes`, `nexus_edges`.
  - Config: `profile_config`, with `lunm.*` keys visually highlighted.
- Non-picker open paths:
  - drag/drop a `.lun` file onto the window.
  - paste an absolute matrix path in the header.

## Format-invariant surface

The inspector treats exactly these SPEC-008/SPEC-011 tables as LUNM
format-invariant:

| Table | MVP use |
| --- | --- |
| `memory_nodes` | primary node listing and filters |
| `graph_edges` | relationship listing with source/target lookup |
| `conversation_turns` | turn listing, optionally by session |
| `sessions` | session listing |
| `nexus_nodes` | promoted pointer-graph nodes |
| `nexus_edges` | pointer-graph relationships |
| `nexus_registry` | mounted/discovered cartridge registrations |
| `profile_config` | LUNM header keys and profile config |

Entity tables from SPEC-012 remain engine-extension. They are not required for
family recognition and are not part of the first-screen MVP.

## Tauri command surface

| Command | Returns |
| --- | --- |
| `open_lunm_matrix(path)` | `MatrixHandle` |
| `close_lunm_matrix(handle)` | `()` |
| `get_lunm_overview(handle)` | `LunmOverview` |
| `get_lunm_health(handle)` | `LunmHealthReport` |
| `list_memory_nodes(handle, filters, limit, offset)` | `Vec<TableRow>` |
| `list_graph_edges(handle, node_id, limit, offset)` | `Vec<TableRow>` |
| `list_sessions(handle, limit, offset)` | `Vec<TableRow>` |
| `list_conversation_turns(handle, session_id, limit, offset)` | `Vec<TableRow>` |
| `list_nexus_registry(handle, limit, offset)` | `Vec<TableRow>` |
| `list_nexus_nodes(handle, limit, offset)` | `Vec<TableRow>` |
| `list_nexus_edges(handle, limit, offset)` | `Vec<TableRow>` |
| `list_profile_config(handle, prefix, limit, offset)` | `Vec<ProfileConfigRow>` |

All commands are read-only. There is no command that writes SQL or invokes
Engine migration logic.

## Acceptance criteria

1. Opening a non-SQLite file returns `NotSqlite`.
2. Opening a LUNC cartridge returns `WrongFamily(LUNC)`.
3. Opening an unknown SQLite family returns `WrongFamily(unknown)`.
4. Opening a LUNM file with `user_version = 2` succeeds.
5. Opening a LUNM file with `user_version < 2` returns `UnsupportedUserVersion`.
6. The opened connection rejects attempted writes via `query_only`.
7. Health reports missing FI tables and columns without needing a mutation path.
8. Health warns, rather than rejecting open, when `lunm.matrix_ulid` is missing.
9. The Memory, Graph, Conversations, Nexus, and Config tabs list rows from a
   small valid fixture.
10. `cargo test` and `npm run build` pass.

## Out of scope

- Editing, deleting, repairing, migrating, or exporting matrix data.
- Attaching to the live Luna Engine runtime.
- Treating engine-extension tables as required for family recognition.
- Reusing LUNC Reader surfaces such as cartridge `meta`, extractions, figure
  display, trust composition, shelf, semantic search, or annotation ledger.
