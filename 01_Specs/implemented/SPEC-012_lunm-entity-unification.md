# SPEC-012: LUNM entity unification

**Status:** implemented
**Severity:** critical
**Author:** Ahab (with Codex)
**Created:** 2026-07-24
**Last updated:** 2026-07-24
**Affects format version:** LUNM v0.1 (no `user_version` bump — entity family is engine-extension; additive columns/tables do not change LUNM `user_version` per SPEC-008 Q4 / this spec §4.3)

---

## Problem statement

Luna's entity layer currently behaves as several adjacent identity systems rather than one substrate contract. Threads can carry display strings, graph `ENTITY` nodes can carry graph-local node ids, and the LUNM `entities` family carries the durable profile/fact/relationship state. When those ids diverge, merges, retypes, prompt assembly, Observatory views, and relationship recall cannot be made coherent by local fixes. SPEC-012 defines the architecture that makes LUNM entity identity converge without creating a sidecar cartridge or promoting entity tables into LUNM format invariants.

## Observed evidence

- [SPEC-009](../implemented/SPEC-009_lunm-schema-ownership.md) resolves the LUNM entity family owner as `luna.substrate` / `schema.sql`, covering `entities`, `entity_mentions`, `entity_relationships`, `entity_versions`, and `entity_relationship_evidence`.
- SPEC-009 classifies entity tables as `engine-extension`, not `format-invariant`. Readers MUST NOT treat those tables as required for LUNM family recognition.
- [SPEC-008](../implemented/SPEC-008_lunm-family-foundation.md) defines LUNM as Luna's runtime matrix family, discriminated by `PRAGMA application_id = 0x4C554E4D`, mutated in place, and versioned by contract-affecting changes only.
- [SPEC-010](../implemented/SPEC-010_lunm-migration-discipline.md) governs in-place LUNM migration behaviour. Entity migrations inherit the engine-extension failure and logging rules.
- The Looney-WIKI LUNM breakdown records the current live evidence shape: LUNM `entities` is distinct from cartridge/collection `aibrarian_schema.entities` and from Intergalactic Hub `ih_entities`.
- Acceptance audit (2026-07-24) against Luna Engine `schema.sql` and live `memory_matrix.lun` (read-only): `entities.id` TEXT PK; live counts **37** entities / **326** ENTITY memory nodes / **0** id overlap; name match N→1 (avg 1.59, max 3).

## Root cause analysis

The defect is not that LUNM lacks an entity table. The defect is that identity creation and projection are not forced through one durable key. A graph node id can become the effective identity in one path, a display string can become the thread identity in another, and the entity-family primary key can become the profile identity in a third. Because no layer is required to project from the same key, downstream policy inherits ambiguity:

- type defaults can mint a person when evidence only says "unknown mention";
- mention linking can rank strings and alias hits instead of entity evidence;
- prompt assembly can inject polluted facts or relationships every turn;
- Observatory can render entities, relationships, and thread chips through incompatible DTO shapes;
- maintenance scripts can rewrite live entity state without the same matrix safety ritual used for other LUNM work.

## Proposed solution

### 4.1 Architecture rule: one canonical LUNM entity key

The canonical entity identity is **`entities.id`** (TEXT slug primary key in Engine `schema.sql`). SPEC-012 does not invent a new identity column.

Every other entity-bearing layer is a projection:

| Layer | Post-SPEC-012 role |
| --- | --- |
| LUNM `entities` row | Source of truth for typed identity, aliases, profile state, facts, and relationship endpoints |
| `memory_nodes` where `node_type = 'ENTITY'` | Graph projection with `memory_nodes.id == entities.id` |
| `graph_edges` involving entities | Graph projection over canonical entity ids, never a second identity namespace |
| Thread roster | `entity_ids` plus a denormalized display-name cache for chips |
| Observatory entity UI/API | DTOs hydrated from canonical entity ids |
| LUNC cartridge entity extractions | Portable observations/surfaces that may seed or evidence an entity, never Luna's authoritative identity |

New code MUST read and write entity identity through a single Entity Lifecycle boundary. Direct creation of graph-local `ENTITY` ids, thread-only string entities, or relationship endpoints that bypass the canonical key is prohibited after the canonical-id flag graduates.

### 4.2 LUNM / LUNC boundary

This spec evolves the LUNM runtime matrix entity subsystem in place inside `memory_matrix.lun`. It does not define a new `.lun` family and does not create an entity sidecar cartridge.

LUNC cartridge entities remain import/export material: document-level extractions, surfaces, aliases, claim anchors, and possible seed evidence. Promoting a LUNC observation into Luna's live entity layer is an Engine operation that resolves or creates a canonical LUNM entity row. Exporting entity packs later is allowed only as a projection or snapshot; such packs cannot become a second source of truth for live Luna identity.

### 4.3 Schema changes

The allowed shape is additive only. **No `entities.graph_node_id` bridge column** — live N→1 name matches and zero id overlap make rewrite+quarantine the correct path; a single bridge column cannot represent N candidates.

No LUNM `user_version` bump: the entity family is engine-extension; additive columns/tables do not change `user_version` (SPEC-008 Q4).

`unknown` MUST become a valid entity type in the Engine model. Today's `entities.entity_type` has no CHECK constraint, so the DB accepts `unknown` without ALTER.

Accepted quarantine DDL (Engine `schema.sql` is sole DDL authority; `database.py` applies via schema-owned text, not a pasted second CREATE):

```sql
CREATE TABLE IF NOT EXISTS entity_identity_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
        -- 'ambiguous_name_match' | 'id_collision' | 'orphan_graph_node'
        -- | 'unmatched_entity_row' | 'manual'
    survivor_entity_id TEXT,
    loser_node_id TEXT,
    loser_entity_id TEXT,
    display_name TEXT NOT NULL,
    evidence_json TEXT,
    status TEXT NOT NULL DEFAULT 'open',
        -- 'open' | 'resolved_merge' | 'resolved_drop' | 'resolved_keep'
    coalesce_run_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_eiq_status ON entity_identity_quarantine(status);
CREATE INDEX IF NOT EXISTS idx_eiq_survivor ON entity_identity_quarantine(survivor_entity_id);
CREATE INDEX IF NOT EXISTS idx_eiq_loser_node ON entity_identity_quarantine(loser_node_id);
CREATE INDEX IF NOT EXISTS idx_eiq_run ON entity_identity_quarantine(coalesce_run_id);
```

**Retention:** `open` kept until operator resolves. Purge `resolved_*` older than 90 days. No FKs (survive deletes for audit).

**Coalesce identity rules:**

| Match | Action |
| --- | --- |
| 0 ENTITY stubs for an `entities` row | Insert projection node `id = entities.id` |
| Exactly 1 name/alias match | Rewire `graph_edges` to canonical id; archive/delete stub |
| N-way (ambiguous) | **Quarantine-only** — do not auto-pick a survivor. Rank candidates (access/degree/age) in `evidence_json` for review only. Merge only when an allowlist explicitly names the survivor |
| Orphan ENTITY stubs | Quarantine `orphan_graph_node`; do not auto-mint people |

### 4.4 Behavioral changes

Implementation MUST proceed in this order:

1. **Canonical identity.** Route all entity create/resolve/merge/retype paths through `entities.id`. Coalesce graph `ENTITY` nodes to that same key under a feature flag (no bridge column).
2. **Unknown default.** Replace person-default behaviour with `unknown` as the only safe default. Durable `person` type requires positive evidence.
3. **Prompt diet.** Split always-on identity context from turn-scoped relationship context. Always-on context is limited to Luna self-voice and bounded canonical user facts. Relationship facts enter the prompt only when the current turn resolved relevant entity ids or an explicit flag enables key-relationship injection.
4. **Mentions and salience.** Replace raw mention-count ranking with resolver-backed salience. Span-aware mentions may land later but must use the same canonical entity key.
5. **Observatory DTO.** Serve one entity API contract for Observatory and thread chips: entity references and relationships are keyed by canonical entity id.
6. **Maintenance safety.** Entity backfills, coalesces, and cleanup scripts must refuse unsafe live writes unless the backend is down or an explicit operator override is provided.
7. **Later structured facts.** If JSON `core_facts` and blob snapshots become unsafe for provenance or rollback, add a first-class `entity_facts` table in a later spec or accepted amendment.

### 4.5 DTO contract

Engine implementation MUST expose one owned entity DTO shape for Observatory entity surfaces:

```ts
type EntityRefDTO = {
  id: string;
  name: string;
  type: "unknown" | "person" | "persona" | "place" | "project" | "topic";
  aliases?: string[];
};

type EntityRelationshipDTO = {
  id: number | string;
  from: EntityRefDTO;
  to: EntityRefDTO;
  relationship: string;
  strength: number;
  evidence_count?: number;
  confidence?: number;
};

type ThreadRosterDTO = {
  entity_ids: string[];
  entities: EntityRefDTO[];
};
```

**DTO owners:** `src/luna/entities/dto.py` (contract); `src/luna/services/observatory/routes.py` + `src/luna_mcp/observatory/tools.py` (HTTP/payload); `frontend/src/observatory/views/EntitiesView.jsx` + `ThreadsView.jsx` (+ `api.js` / `store.js`). Graph visualization APIs may keep `from_id` / `to_id` for memory graph edges. Entity relationship APIs MUST NOT dual-vocab after the DTO flag graduates.

### Migration path

Migration is in-place and Engine-managed under SPEC-010. No destructive live-data rewrite is permitted as an automatic boot migration.

Required operator flow for destructive or coalescing entity migration:

1. stop Luna backend;
2. copy `memory_matrix.lun` and any `-wal` / `-shm` siblings;
3. run dry-run coalesce and produce survivor, duplicate, dangling-edge, thread-roster, and quarantine counts;
4. apply only a bounded allowlist (required for any N-way survivor);
5. verify with SQLite opened in read-only mode;
6. restart backend and run a live smoke;
7. rollback by restoring the backup and removing any feature flag that enabled the new path.

Forward compatibility is additive: old Engine readers may ignore new columns/tables but must not be expected to preserve unified projections if they continue writing legacy ids. The canonical-id feature flag must remain reversible until live coalesce has been proven.

**Schema authority:** Engine `schema.sql` owns entity-family DDL. `database.py` migrates by applying schema-owned CREATE text (e.g. `schema_apply.apply_create_if_missing`); it MUST NOT paste an independent CREATE body (threads-style dual-declaration drift). SPEC-009 Rule 2 authority for the new table is Engine `src/luna/substrate/lunm_table_manifest.toml`; dated `04_Audits/` TOML files are mirrors only.

## Validation rules

Acceptance audit recorded:

```python
canonical_key = "id"  # entities.id TEXT PRIMARY KEY
assert no_graph_node_id_bridge_column()
assert quarantine_ddl_frozen()  # see §4.3
```

Engine implementation MUST include unit tests for canonical coalescing, unknown-default typing, prompt diet, DTO hydration, and maintenance live-lock refusal. Live-matrix migration tests must run against a copy, never the active profile file. N-way matches MUST NOT auto-select a survivor without an allowlist entry.

## Governance implications

- **Ledger / annotation events:** N/A for LUNM. LUNM remains a continuously-mutated runtime matrix, not a hash-ledgered cartridge.
- **Multi-axis imprint weights:** LUNC reading concern only. Cartridge extraction confidence can be evidence for a LUNM entity but does not define canonical identity.
- **Actor roles:** No LUNM format-level role change. Entity provenance may later include writer/source roles as Engine metadata.
- **Cross-cartridge traversal:** A LUNC cartridge may supply entity observations; promotion into the matrix must verify the target is LUNM per SPEC-008 and resolve through the canonical entity key.
- **Memory Matrix integration:** Entity tables remain engine-extension tables under SPEC-009. SPEC-012 does not add to the eight SPEC-008 format-invariant tables.

## Alternatives considered

- **Sidecar entity cartridge.** Rejected. It recreates the split-identity defect across files and makes the live runtime identity layer depend on cartridge synchronization.
- **Promote entity tables to format-invariant.** Rejected. SPEC-008 intentionally limits LUNM invariants to eight core tables; entity identity is critical Engine behaviour but not required for family recognition.
- **Canonical key = graph `ENTITY` node id.** Rejected. Graph nodes are projections over memory topology; making them authoritative preserves the current UUID-vs-profile split.
- **Keep thread string rosters as source of truth.** Rejected. Display strings are caches, not identity.
- **Graduate NER typing alone.** Rejected. NER can be one evidence source, but unknown-default policy is the durable safety rule.
- **Temporary `entities.graph_node_id` bridge.** Rejected at acceptance — N→1 collisions make a single bridge column insufficient; quarantine + allowlisted coalesce instead.

## Resolved questions

Each Q below was resolved ahead of the `active → accepted` promotion. Question bodies are preserved; each `**Resolution (2026-07-24):**` records what was picked.

1. **What is the exact canonical key in the live Engine DDL?** Verify whether the LUNM `entities` row key is `id`, `name`, slug, or another column. The accepted spec must name the exact key and update §4.1 / DTO text if needed.

   **Resolution (2026-07-24):** Canonical key is `entities.id` (TEXT PRIMARY KEY, slug). Confirmed in Engine `schema.sql` and live `PRAGMA table_info(entities)`.

2. **Is a temporary `entities.graph_node_id` bridge required?** If the live graph can be coalesced without a bridge, omit the column and use a quarantine-only migration.

   **Resolution (2026-07-24):** **No bridge column.** Live audit: 37 entities, 326 ENTITY nodes, 0 id overlap; name match is N→1. Coalesce rewires unambiguous 1:1 matches; N-way is quarantine-only unless allowlist names survivor.

3. **What exact DDL does `entity_identity_quarantine` need?** Acceptance must define columns, indexes, and retention policy if the table is kept.

   **Resolution (2026-07-24):** DDL frozen in §4.3. Retention: purge `resolved_*` older than 90 days; no FKs.

4. **What is the first feature flag set and default?** Recommended: `LUNA_ENTITY_CANONICAL_ID=0`, `LUNA_ENTITY_UNKNOWN_DEFAULT=0→1`, `LUNA_IDENTITY_PROMPT_DIET=0→1`, `LUNA_ENTITY_SALIENCE_RANK=0`, `LUNA_OBS_ENTITY_DTO=0`.

   **Resolution (2026-07-24):** Adopt the recommended set and defaults exactly.

5. **Which Engine routes/components own the final Observatory DTO?** Verify current route names and frontend consumers before acceptance.

   **Resolution (2026-07-24):** Contract `src/luna/entities/dto.py`; HTTP `observatory/routes.py` + MCP `tool_observatory_entities`; UI `EntitiesView.jsx` / `ThreadsView.jsx` (+ api/store). Graph viz retains memory-edge vocabulary.

6. **What live-matrix probe corpus proves unknown-default typing?** Acceptance must name the tool/model/acronym/person cases that prevent harmful `person` mints.

   **Resolution (2026-07-24):** Probe corpus classes — must stay `unknown` (no person mint): models/tools (Claude Haiku, Whisper STT, GPT-4, MLX, FTS5, OpenAI, SQLite), acronyms/junk (API, STT, Slide 2, The Problem, years, calendar), concepts (generative AI, robotics), ambiguous bare names without context (Tesla, Maya, Edison). May become `person` with evidence: Ahab, Mrs. Chen (+ sentence evidence); full-name person context (Nikola Tesla). Places/projects typed only when evidence supports `place`/`project`, else `unknown`.

## Dependencies

**Upstream (implemented):**

- [SPEC-008](../implemented/SPEC-008_lunm-family-foundation.md) — LUNM family identity, invariants, and bump triggers.
- [SPEC-009](../implemented/SPEC-009_lunm-schema-ownership.md) — LUNM entity-family owner/classification.
- [SPEC-010](../implemented/SPEC-010_lunm-migration-discipline.md) — migration failure discipline and `user_version` mechanics.
- [SPEC-011](../implemented/SPEC-011_lunm-format-invariant-ddl.md) — ratifies only the eight format-invariant tables and leaves entity tables outside the FI set.

**Downstream:**

- Engine WP0+ implementation (canonical identity, unknown default, prompt diet, salience, Observatory DTO, maintenance live-lock).
- Follow-up accepted amendments or sibling specs for span-aware mentions and first-class `entity_facts` if those exceed this spec's accepted-scope DDL.

## Implementation notes

- Commit/PR reference: Engine `a43baae2` (SPEC-012 entity flags default-ON; `LUNA_ENTITY_TYPING` graduated with tool/model person-override rejection)
- Implementation date: 2026-07-24
- Deviations from spec: Quarantine N-way/orphan stubs remain open pending human allowlist review (`Logs/probes/spec012_allowlist_DRAFT.json`). NER person overrides reject tool/model tokens so unknown-default mint stays clean.
- Follow-up issues created: human-reviewed coalesce `--allowlist` apply; span mentions / `entity_facts` if needed later

