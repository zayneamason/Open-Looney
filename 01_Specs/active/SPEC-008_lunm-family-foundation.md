# SPEC-008: LUNM runtime matrix family — foundational contract

**Status:** active
**Severity:** medium
**Author:** Ahab (with Claude)
**Created:** 2026-05-24
**Last updated:** 2026-05-24
**Affects format version:** LUNM v0.1 (independent from LUNC version trajectory)

---

## Problem statement

[SPEC-006](../implemented/SPEC-006_v02-hygiene-bundle.md) fixed the family discriminator for `.lun` files (`application_id` = `'LUNC'` for cartridges, `'LUNM'` for the runtime matrix) but left the LUNM side deliberately under-specified — pragmas only, no `meta` table, no human-readable identity marker, no enumerated table contract. The [v0.3 format spec](../../03_Format_Spec/LUN-FORMAT_v0.3.md) explicitly defers (line 4): *"The runtime matrix family (`'LUNM'`) is a sibling format with a different schema; it will get its own spec when its schema stabilizes."* And SPEC-006 (line 254) names the follow-on: *"A future spec for the runtime matrix human-readable header (whether via `profile_config` or a new header table)."*

That deferral has held since 2026-05-10, gated on three preconditions ([`08_Journal/2026-05-10.md`](../../08_Journal/2026-05-10.md) lines 69–71): Nexus promotion bug fixed, `ih_*` tables settled, `conversation_turns` finalized. The Nexus precondition was satisfied on 2026-05-24 by Luna Engine commit `432e2e9` (`feat(nexus): dynamic cartridge mounting + v0.3 read support`), which extended `nexus_registry` with seven new columns (`mounted`, `discovered_at`, `validation_status`, `validation_reason`, `family`, `user_version`, `source`) and made the matrix a *named substrate* — the first downstream consumer to depend on a stable LUNM identity surface. The other two preconditions are partially-met: `conversation_turns` is in production use with one in-place column migration (`turn_type` per `_migrate_turn_type_column()`); the `ih_*` table family was forward-referenced in the 2026-05-10 architectural sketch but never built (see § Resolved questions Q5).

This spec ratifies LUNM's foundational contract — identity, lifecycle invariants, and a core table inventory by name — without locking DDL. It carries forward the header-mechanism decision from SPEC-006 as an explicit Open Question. It explicitly defers full schema ratification (→ SPEC-009) and migration discipline (→ SPEC-010).

## Observed evidence

- **SPEC-006 § Proposed solution → Schema changes** ([`SPEC-006_v02-hygiene-bundle.md`](../implemented/SPEC-006_v02-hygiene-bundle.md) lines 70–76): *"Runtime matrix family (`memory_matrix.lun`) — pragmas only: ... NO meta inserts. The live memory_matrix.lun has no meta table. Human-readable matrix marker deferred to a future spec (either via `profile_config` or a dedicated matrix header table)."*
- **SPEC-006 § Dependencies → Downstream deferred** (line 254): *"A future spec for the runtime matrix human-readable header (whether via `profile_config` or a new header table)."* This is the load-bearing deferral SPEC-008 closes (or formally re-defers via Q1).
- **LUN-FORMAT_v0.3 § Scope** (line 4): *"The runtime matrix family (`'LUNM'`) is a sibling format with a different schema; it will get its own spec when its schema stabilizes."* SPEC-008 is that spec.
- **Journal 2026-05-10** (lines 69–71): *"Matrix format spec. Doesn't exist yet. Decision: defer until the matrix schema stabilizes (Nexus promotion bug fixed, `ih_*` tables settled, `conversation_turns` finalized). No urgency."* The Nexus precondition was satisfied by engine commit `432e2e9` (2026-05-24); the other two are addressed in § Resolved questions Q5 and § 4.3.
- **Live matrix pragma patch** at `_LunaEngine_BetaProject_V2.0_Root/src/luna/substrate/database.py:141–161`. The patch idempotently sets `PRAGMA application_id = 0x4C554E4D` and `PRAGMA user_version = 2` on first open after migration, and raises `WrongFamilyError` if the file's `application_id` is any other non-zero value. This is the only matrix-side enforcement of family identity in the engine today.
- **Live matrix table inventory** at `_LunaEngine_BetaProject_V2.0_Root/src/luna/substrate/schema.sql` (~36 tables, ~870 lines of DDL) plus migration helpers in `database.py` (`_migrate_*` family, 12 functions). The live surface is dramatically larger than the 2026-05-10 architectural sketch's six tables; see § 4.3 for the format-level core cut.
- **Nexus dynamic mounting consumer** at `database.py:441–501` (`_migrate_nexus_registry_dynamic_columns()`) and `nexus_registry.py:207–253` (`upsert_discovered()`). This is the first engine code path that treats LUNM as a *named substrate* (with `family` and `user_version` columns on `nexus_registry`); it needs SPEC-008's discrimination contract to be stable.
- **Reader symmetric boundary** at [`06_Prototypes/ReaderPrototype/SPEC.md`](../../06_Prototypes/ReaderPrototype/SPEC.md) line 142: *"Runtime-matrix family. `application_id = 'LUNM'` files are rejected with a `WrongFamilyError`-equivalent message."* SPEC-008 § 4.2 makes this rejection load-bearing (engine-only readers), not advisory.

## Root cause analysis

Three distinguishable causes for LUNM's under-specification:

1. **`application_id` discriminates *file kind* but not *file identity*.** SPEC-006 told a reader "this is a matrix" but gave it no in-file way to inspect *which matrix*. There is no profile ULID, no creation timestamp, no LUNM-format-version stamp. For a single-tenant matrix this is tolerable (the file path encodes the profile); the moment a cartridge promotes a node via `nexus_refs` to "the matrix," the cartridge has no way to verify it is the *same* matrix as the one that produced the original promotion.

2. **The matrix has no `meta` table.** The cartridge-side `meta` convention (key/value, human-readable, append-only at build) is non-portable to a substrate that is mutated in place over a profile's lifetime. SPEC-006 wisely declined to mirror it; SPEC-008 inherits the choice and the gap. Whatever fills the gap (`profile_config`, a new header table, both) is the load-bearing decision in this spec.

3. **The table inventory grew organically.** The 2026-05-10 architectural sketch named six tables. The live `schema.sql` has ~36, with another ~10 added by migration helpers. No spec audit has separated *format invariants* (tables that any LUNM file must have to be a LUNM file) from *engine extensions* (application features the engine layers on top of the substrate). SPEC-008 makes that cut at the foundation level so SPEC-009 has a footing to ratify full DDL against.

## Proposed solution

SPEC-008 ratifies four things and only those four: family identity, lifecycle invariants, the core table inventory by name, and the cross-family discrimination contract. It does not add DDL. It does not require any code change in the engine beyond an optional assertion (§ Behavioral changes). The header-mechanism decision (Q1) is carried as an explicit Open Question; the spec body recommends `profile_config` but does not bind the choice at `active` status.

### 4.1 Family identity

A LUNM file is identified by:

| Property | Value | Source |
| --- | --- | --- |
| `PRAGMA application_id` | `0x4C554E4D` (decimal `1280659021`, ASCII `'LUNM'`) | SPEC-006 § Schema changes |
| `PRAGMA user_version` | `≥ 2` (LUNM v0.1 baseline = `2`, the value SPEC-006 set) | SPEC-006 § Schema changes |

**Independent version trajectory.** LUNM's `user_version` evolves on its own cadence, unconstrained by LUNC's. LUNM v0.1 (`user_version = 2`) is the current production baseline — this is the value SPEC-006 wrote on 2026-05-10 and has been stable through every engine update since. The number "2" reflects SPEC-006's choice to align with the LUNC v0.2 milestone, not a coupling promise. From SPEC-008 forward, LUNM bumps `user_version` only when LUNM's contract changes (which versioning rule applies — symmetric with LUNC, or stricter — is Q4 below). The LUNC version trajectory is irrelevant to LUNM and vice versa. This codifies [`08_Journal/2026-05-10.md`](../../08_Journal/2026-05-10.md) line 57's stance.

**Family-version label.** LUNM uses a `vMAJOR.MINOR` label ("LUNM v0.1") that maps onto integer `user_version` values. The mapping is:

| Family-version label | `user_version` | Status |
| --- | --- | --- |
| LUNM v0.1 | 2 | Current (this spec, 2026-05-24) |

Future spec-affecting changes bump the label per Q4's resolution and the `user_version` integer in lockstep.

### 4.2 Lifecycle invariants

A LUNM file:

1. **Is created at profile init.** A new profile's `MemoryDatabase.connect()` opens an empty SQLite file at the profile's matrix path, runs `_load_schema()`, applies all `_migrate_*` helpers, sets the pragmas (idempotent — see SPEC-006), and returns. The engine is the only authorized creator.
2. **Is mutated in place for the life of the profile.** Every conversation turn, memory node, Nexus promotion, and `profile_config` write goes into the same file. The matrix is *the* persistent state of the profile.
3. **Is never rebuilt.** Unlike the LUNC builder, which creates a cartridge from source documents and finalizes it (`VACUUM`, switch to `journal_mode=DELETE`), the matrix has no finalize stack. It runs `journal_mode=WAL` (per `database.py:130`) for its entire life. Re-creating a matrix means losing the profile.
4. **Is never shipped.** Cartridges are portable; matrices are stationary. A matrix file leaving its host is either a backup operation (acceptable, but the receiver is the same profile on a different machine) or a privacy bug. SPEC-008 takes no position on backup protocols; it states only that *consumer-style* portability (drop a `.lun` into another profile's substrate) is not a LUNM use case.
5. **Has a single-tenant writer.** One process holds the writer connection at a time. Concurrent readers within the same engine process are fine (WAL mode supports them); concurrent writers across processes are out of scope (`PRAGMA busy_timeout=15000` at `database.py:133` is a defensive limit, not a contract). Portable readers (the reader prototype, third-party tools) MUST refuse LUNM files outright per the boundary at [`06_Prototypes/ReaderPrototype/SPEC.md`](../../06_Prototypes/ReaderPrototype/SPEC.md) line 142.

### 4.3 Core table inventory

A LUNM file at format version v0.1 MUST contain the following tables. They are the *format invariants* — any tool claiming to read or write a LUNM file may assume their presence and shape (insofar as v0.1 specifies shape, which for SPEC-008 is "exists; full DDL deferred to SPEC-009").

| Table | One-line role | Source |
| --- | --- | --- |
| `memory_nodes` | The substrate's primary node table — typed content, lock-in score, classification. FTS5-indexed via virtual table `memory_nodes_fts`. | `schema.sql:9` |
| `graph_edges` | Directed relationships between `memory_nodes` rows. | `schema.sql:62` |
| `conversation_turns` | Turn-by-turn conversation history, scoped to `sessions`. Has one in-place migrated column (`turn_type`, per `_migrate_turn_type_column()`). | `schema.sql:41` |
| `nexus_nodes` | The Nexus pointer-graph: cross-cartridge node identities promoted into the matrix. | `schema.sql:407` |
| `nexus_edges` | Directed relationships between `nexus_nodes` rows. | `schema.sql:415` |
| `nexus_registry` | Per-collection cartridge registration: paths, ingestion patterns, lock-in, and (post-`432e2e9`) dynamic-mounting state. | `schema.sql:424` |
| `profile_config` | Per-profile typed key/value config. Recommended (but not yet bound — see Q1) as the home for LUNM's human-readable identity marker. | Created by `_migrate_profile_config_table()` at `database.py:412–439` |

**Engine-extension tables are explicitly OUT of scope** for SPEC-008. These tables exist in the live `schema.sql` but are application features the engine layers over the substrate; they are not format invariants and are not load-bearing on the LUNM identity contract:

`consciousness_snapshots`, `sessions`, `compression_queue`, `extraction_queue`, `history_embeddings`, `entities`, `entity_relationships`, `entity_mentions`, `entity_versions`, `tuning_sessions`, `tuning_iterations`, `collection_lock_in`, `collection_annotations`, `quests`, `quest_targets`, `quest_journal`, `protocols`, `roles`, `tasks`, `task_dependencies`, `task_runs`, `threads`, `thread_tasks`, `task_entities`, `task_subjects`, `task_keywords`, `task_files`, `task_memory_links`, `topology_clusters`, `topology_cluster_threads`, `game_states`.

Whether any of the above should be promoted into the SPEC-008 core (the most likely candidates are `sessions` and `consciousness_snapshots`) is Q5 below.

### 4.4 Discrimination contract for cross-family code

Any code path that opens a `.lun` file MUST verify `application_id` before any other read. Specifically:

- **Engine matrix open** (`MemoryDatabase.connect()`): already enforces this at `database.py:141–161`; this spec ratifies the existing behavior.
- **Engine cartridge open** (`AiBrarian.list_lun_dir()` and friends): already enforces this — `nexus_registry.family` records `'lunc_v02'` / `'lunc_v03'` / `NULL` per `schema.sql:441`. LUNM cartridges are not admissible as Nexus collections; the family check guards this.
- **Portable readers**: per [`06_Prototypes/ReaderPrototype/SPEC.md`](../../06_Prototypes/ReaderPrototype/SPEC.md) line 142, LUNM files MUST be rejected with the equivalent of `WrongFamilyError`. This is non-negotiable for SPEC-008 — the matrix is not a portable artifact and a portable reader has no defensible behavior on one. Whether the rejection must be normative (MUST) or advisory (SHOULD) is Q3 below; this spec recommends MUST.
- **Cross-cartridge promoters** (any code walking `nexus_refs.nexus_node_id` from a cartridge into a matrix): MUST verify the target matrix's `application_id` matches `0x4C554E4D`. Once Q1 resolves, the promoter MUST also verify a matrix-identity key (likely `profile_config.lunm_profile_ulid`) matches the engine's current profile. This prevents accidental cross-profile promotion. SPEC-008 names the requirement; the exact mechanism waits on Q1.

## Behavioral changes

SPEC-008 requires no engine code change beyond an optional assertion. The existing pragma patch at `database.py:141–161` already enforces `application_id`; it raises `WrongFamilyError` on mismatch. No new code path is mandated.

**Optional assertion (recommended, not required):** After `_load_schema()` completes (`database.py:182`), the engine MAY round-trip-verify that `application_id` is still `0x4C554E4D` and `user_version >= 2`. This guards against a buggy migration silently dropping the pragmas. SPEC-008 does not require it; it is named here as a low-cost defensive measure.

**No builder change.** Unlike LUNC, LUNM has no offline builder. The "build" is `MemoryDatabase.connect()` on a new file, and that path is already SPEC-006-compliant.

**No reader change.** Per § 4.4, the reader prototype's existing rejection at `SPEC.md:142` satisfies SPEC-008.

## Migration path

Forward-compatible. SPEC-008 only ratifies what exists; it adds no required fields, no new tables, no schema changes. Every LUNM file in production at any time on or after the SPEC-006 implementation date (2026-05-12) is already SPEC-008-compliant.

The header decision (Q1) will introduce a migration when it resolves — likely a `profile_config` insert via the existing `_migrate_profile_config_table()` path. That migration is out of scope for this spec; it lands with Q1's resolution.

## Validation rules

At engine matrix open (already implemented; SPEC-008 ratifies):

```python
# Pseudocode for MemoryDatabase.connect() post-condition.
assert pragma("application_id") == 0x4C554E4D, "Not a LUNM file"
assert pragma("user_version")   >= 2,          "LUNM v0.1 minimum is user_version=2"
```

No build-time validation is required (there is no build). No fsck-style tool exists for LUNM at v0.1; defining one is out of scope (→ SPEC-010 or later).

## Governance implications

- **Ledger / annotation events:** N/A. LUNM has no `annotation_ledger`. The SPEC-005 ledger is a LUNC feature; whether LUNM should ever gain an analog is a separate future spec (and almost certainly answered "no" — the matrix is mutated continuously and a hash-chained event log would conflict with that).
- **Multi-axis imprint weights (SPEC-004):** N/A. Imprint weights and trust composition are a LUNC reading concern.
- **Actor roles (owner, ambassador, elder, oracle):** N/A at the format level. LUNM has no actor-role table; if the engine tracks the current profile's role, that is application state, not format invariant.
- **Cross-cartridge traversal:** Load-bearing. The discrimination contract in § 4.4 is the first place SPEC-008 has teeth. When a cartridge promotes via `nexus_refs.nexus_node_id` to a matrix node, the promoter MUST verify both family (`application_id`) and (post-Q1) matrix identity. Without this, two profiles' matrices could be silently conflated through a shared cartridge that travels between them.
- **Memory Matrix integration:** This spec *is* the Memory Matrix integration contract at the foundation level. Subsequent specs (SPEC-009 DDL, SPEC-010 migration discipline, any future SPEC for matrix-side governance) layer on top.

## Alternatives considered

Three alternatives were evaluated and rejected at the scope-decision stage (2026-05-24):

- **(a) Narrow scope: resolve only the SPEC-006 header decision.** ~200 lines. Rejected: leaves "what is LUNM" undefined; every future LUNM spec would have to re-litigate scope, and the Nexus dynamic mounting consumer (`432e2e9`) needs a stable identity surface *now*, not after a future SPEC-009 ratifies one.
- **(b) Full schema spec: ratify every table's DDL.** ~700+ lines. Rejected: the live `schema.sql` has ~36 tables, the 2026-05-10 sketch named six (and `ih_events` doesn't exist, see Q5), and several tables have in-place column migrations that make their "true" DDL ambiguous. Locking full DDL now is premature; a fresh audit (→ SPEC-009) will need to disentangle base DDL from migration-added columns before any ratification is defensible.
- **(c) Family-architecture spec: define the LUNC/LUNM-family meta-contract with no LUNM-specific content.** Rejected: too abstract; no concrete consumer needs "what would a LUNxxx family look like" framing yet, and the SPEC-006 deferred header decision is concrete and overdue.

The chosen scope (hybrid foundation: identity + lifecycle + table inventory by name + discrimination contract, with the header mechanism carried as an Open Question) is the smallest defensible cut that closes the SPEC-006 deferral while leaving SPEC-009 a clean surface to ratify against.

## Open questions

Each Q below blocks `active → accepted`. Per the [spec lifecycle principle](../../00_README/README.md), question bodies are preserved through resolution; each `**Resolution (YYYY-MM-DD):**` line records what was picked when the spec moves to `accepted/`.

1. **Header mechanism: `profile_config` or a dedicated `lunm_header` table?** SPEC-006 (line 254) deferred this decision to "a future spec." `profile_config` already exists in the live engine (created by `_migrate_profile_config_table()` at `database.py:412–439`), already has a typed accessor (`ProfileConfig` at `profile_config.py:45–217` with str/int/float/bool/json coercion + audit fields), and is non-empty in every production matrix. A dedicated `lunm_header` table would be symmetric with LUNC's `meta` but costs a CREATE TABLE + migration for a table that would carry ~3 keys. **Recommendation:** `profile_config`, zero new DDL. The dedicated-table alternative reads as symmetric-for-symmetry's-sake; the cost of mixing format-identity keys with application-config keys in one table is small (a namespace convention — `lunm.*` for format-identity keys — handles it) and the cost of a second key/value table is paid forever.

2. **Required header keys (conditional on Q1 = `profile_config`).** Candidates: `lunm.format_version` (string, `"0.1"`), `lunm.profile_ulid` (string, ULID per SPEC-002 canonical form), `lunm.created_at` (string, ISO-8601 UTC). Any others? Candidate adds: `lunm.engine_version` (provenance: which engine wrote the file first), `lunm.schema_fingerprint` (a hash of `schema.sql` at creation time, for drift detection). **Recommendation:** the three baseline keys; add `lunm.engine_version` for provenance; defer `lunm.schema_fingerprint` to SPEC-009 (it is meaningful only once SPEC-009 ratifies the schema it would fingerprint).

3. **`nexus_refs` cross-family verification: MUST or SHOULD?** § 4.4 currently states MUST. The alternative is SHOULD — leaving room for code paths that promote into a *known-local* matrix (e.g. test harnesses) to skip the check. **Recommendation:** MUST in production code paths; document an explicit `--unsafe-skip-family-check` carve-out in any CLI tool that needs the SHOULD relaxation. The default is strict.

4. **LUNM `user_version` bump policy.** When does LUNM go v0.1 → v0.2? Two candidates:
   - **(a) Symmetric with LUNC** — any schema-affecting change bumps the version. Live engine adds a column to `memory_nodes` → LUNM v0.2.
   - **(b) Stricter than LUNC** — only contract-affecting changes bump the version. Adding a column that the spec doesn't name is an engine concern, not a LUNM-format concern, and doesn't bump.
   The cartridge is shipped and read by third parties; LUNC's policy is reasonably (a) by necessity. The matrix is engine-internal; (b) might be defensible. **Recommendation:** (b), stricter than LUNC. Rationale: bumping `user_version` on every column add forces a coordinated release across engine + spec for changes that are already isolated to one machine. The matrix doesn't ship; it doesn't need shipped-artifact versioning discipline.

5. **Engine-extension table boundary.** SPEC-008 § 4.3 names 7 core tables and excludes ~30 engine-extension tables. Is this cut right? Specifically:
   - `sessions` — currently OUT. It is referenced by `conversation_turns` (FK or convention?). Arguably load-bearing.
   - `consciousness_snapshots` — currently OUT. It is a primary observability surface for the engine. If it is also a primary correctness surface, it might belong IN.
   - `ih_events` — named in [`08_Journal/2026-05-10.md`](../../08_Journal/2026-05-10.md) line 12 as an architectural-sketch core table. **It does not exist in the live `schema.sql`.** Either the journal forward-referenced an unbuilt table, or it was renamed (`task_runs`? `tuning_iterations`?), or it was abandoned. The journal is from 2026-05-10; the Intergalactic Hub work has clearly moved (commit `432e2e9` is on `fix/intergalactic-hub-phase-2-runtime`). **Recommendation:** confirm with the engine implementer (a) whether `ih_events` is a real-but-not-yet-built table, (b) whether it has been renamed, or (c) whether the IH lives entirely in `task_runs` / `tasks` now. Resolve before promoting SPEC-008 to `accepted/`.

## Dependencies

**Upstream (must be implemented):**

- **SPEC-006** (implemented 2026-05-21) — provides the `application_id` / `user_version` contract that SPEC-008 ratifies for the LUNM side. SPEC-006 line 254's deferred-future-spec hook is the primary reference SPEC-008 closes.

**Downstream (future specs that will build on SPEC-008):**

- **SPEC-009 (future)** — full LUNM table DDL ratification. Will require a fresh audit of the live `schema.sql` + migration helpers to disentangle base DDL from in-place column adds. Out of scope for SPEC-008.
- **SPEC-010 (future)** — LUNM migration discipline: idempotency rules, ALTER patterns, when in-place changes are legal vs. when they require a `user_version` bump. Resolves Q4 in detail. Out of scope for SPEC-008.

No spec must change as a prerequisite for SPEC-008 to be accepted. SPEC-006's line 254 deferral becomes "closed by SPEC-008" once SPEC-008 lands in `implemented/`. LUN-FORMAT_v0.3.md line 4's "will get its own spec when its schema stabilizes" updates to "see SPEC-008" at the same time.

## Implementation notes

(Filled in when status moves to `implemented`.)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
