---
doc_type: reference
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - wiki
  - glossary
  - reference
---

# Glossary

Canonical definitions for the `.lun` cartridge and runtime-matrix corpus. This
is the one place these terms are defined; `00_README/README.md` points here
rather than carrying a second copy — two glossaries is the drift class this
wiki system exists to prevent.

## Cartridge family (LUNC)

- **Cartridge** — a `.lun` file in the cartridge family; a portable knowledge
  unit.
- **Family** — a set of related `.lun` files sharing a schema and
  `application_id`. Currently: cartridge (`'LUNC'`) and runtime matrix
  (`'LUNM'`).
- **application_id** — 4-byte SQLite header field that identifies the file
  format family. Set as a required contract per
  [SPEC-006](../../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md).
- **Node** — an entry in `doc_nodes`; document, section, paragraph, or
  sentence.
- **Extraction** — an LLM-generated summary, claim, or entity.
- **Anchor** — a row in `claim_sources` linking a claim to a source node.
- **Orphan claim** — a claim with no anchor row.
- **Imprint** — the process of a claim entering the Memory Matrix with weight.
- **The Wall** — the boundary between a cartridge's local data and the shared
  Memory Matrix.
- **Annotation** — an event recorded against a cartridge by an actor
  (ambassador, elder, oracle).
- **Ledger** — the append-only log of annotation events within a cartridge.
- **Contract** — a declared rule about who can do what with a cartridge.
- **Spectral signature** — a compact representation of a cartridge's
  knowledge used for cross-community matching.

## Runtime matrix family (LUNM)

- **Runtime matrix (LUNM)** — Luna's live, mutated-in-place substrate: memory
  nodes, graph edges, conversation turns, the Nexus pointer-graph.
  `PRAGMA application_id = 0x4C554E4D` (`'LUNM'`). Unlike a cartridge it is
  never rebuilt, never shipped, and has cooperating first-party writers rather
  than a single builder. See
  [SPEC-008](../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md)
  §4.1–4.2 and
  [`sections/lunm-runtime-matrix.md`](sections/lunm-runtime-matrix.md).
- **Format-invariant table** — one of the eight tables a LUNM file must
  contain to be a LUNM file: `memory_nodes`, `graph_edges`,
  `conversation_turns`, `sessions`, `nexus_nodes`, `nexus_edges`,
  `nexus_registry`, `profile_config`. See
  [SPEC-008](../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md)
  §4.3 and the classification vocabulary in [`TAXONOMY.md`](TAXONOMY.md).
- **`profile_config` header key** — a `lunm.`-prefixed key in the
  `profile_config` table carrying LUNM's identity surface:
  `lunm.format_version`, `lunm.matrix_ulid`, `lunm.created_at`,
  `lunm.engine_version`. Seeded by `_seed_lunm_header()`; the prefix is
  reserved so the config HTTP routes cannot overwrite it. See
  [SPEC-008](../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md)
  §Behavioral changes.
- **Nexus pointer-graph** — `nexus_nodes` and `nexus_edges`: cross-cartridge
  node identities promoted into the matrix. Distinct from the matrix's own
  content tables (`memory_nodes`, `graph_edges`).
- **Thread** — a row in the LUNM `threads` table (topic, status, lineage via
  `parent_thread_id`); an engine-extension table, not a format invariant.
  Thread content accumulates in the sibling `thread_events` table. See
  [`sections/lunm-runtime-matrix.md`](sections/lunm-runtime-matrix.md).
- **LUNM entity** — a row in the LUNM `entities` table (slug primary key,
  `entity_type`, `aliases`, `core_facts`, `full_profile`). Distinct from the
  cartridge-side `aibrarian_schema.entities` (different columns, different
  family) and from `ih_entities` (Intergalactic Hub, conditional — present
  only when that subsystem is loaded). See
  [`sections/lunm-runtime-matrix.md`](sections/lunm-runtime-matrix.md).
