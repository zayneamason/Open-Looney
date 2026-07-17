# Code for .lun Development

Research project for evolving the `.lun` cartridge format from a v1 read artifact
into a governed, living knowledge substrate.

**Started:** 2026-04-21
**Owner:** Ahab (Zayne Mason)
**Parent project:** Luna Engine

---

## Purpose

`.lun` cartridges are Luna's portable knowledge units — SQLite databases
containing a document tree, LLM extractions, embeddings, and an FTS5 index.
This research project tracks the format's evolution, specifications, audits,
and integration contracts as `.lun` grows from a read-only artifact into a
governed, ledger-backed, multi-community substrate.

This directory is for **specification and research only**. Implementation
lives in the main Luna codebase. Specs here are the source of truth for
what implementation should match.

---

## Scope: cartridge family

`.lun` is a file-format **family**, not a single format. Two members
currently exist in active use:

| Family | application_id | What it carries |
|--------|---------------|-----------------|
| Cartridge | `0x4C554E43` ('LUNC') | Portable knowledge: doc trees, extractions, claim anchors, embeddings, FTS5 index |
| Runtime matrix | `0x4C554E4D` ('LUNM') | Luna's runtime substrate: memory nodes, graph edges, conversation turns, Nexus pointer-graph |

Both are SQLite databases sharing the `.lun` extension. The `application_id`
pragma is the primary family discriminator — a `file(1)`-style sniffer can
identify which family a file belongs to without opening the schema.

**This research project focuses on the cartridge family.** The runtime
matrix gets its own spec only when its schema stabilizes (Nexus promotion
fixed, `ih_*` tables settled). See `08_Journal/2026-05-10.md` for the
application_id decision and reasoning, and `01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md`
for the contract that establishes both values.

---

## Folder structure

```
00_README/           This file, project conventions, glossary
01_Specs/            Format and behavior specifications
  active/              In-progress specs under discussion
  accepted/            Specs ready for implementation
  implemented/         Specs with shipped implementations
  rejected/            Specs considered and declined (with reasoning)
02_Handoffs/         Session handoff documents for continuity
03_Format_Spec/      Canonical .lun format specification (versioned)
04_Audits/           Audit reports from real cartridges
05_Reference/        External references (SQLite docs, related formats)
06_Prototypes/       Experimental code, throwaway spikes
07_Sample_Cartridges/ Test .lun files for validation
08_Journal/          Dated notes, thinking-out-loud, decisions log
09_Sample_Sources/   Source materials (PDFs, text) used to build sample cartridges
```

---

## Naming conventions

### Specs
`SPEC-NNN_short-slug.md` — e.g. `SPEC-001_orphan-claims.md`

Numbers are assigned sequentially at creation. Once assigned, never reused
even if the spec is rejected.

### Handoffs
`HANDOFF_YYYY-MM-DD_slug.md` — e.g. `HANDOFF_2026-04-21_initial-scoping.md`

### Audits
`AUDIT_YYYY-MM-DD_cartridge-name.md` — e.g. `AUDIT_2026-04-21_priests-and-programmers.md`

### Journal
`YYYY-MM-DD.md` — daily notes. Not every day needs an entry.

### Format spec versions
`LUN-FORMAT_v0.1.md`, `LUN-FORMAT_v0.2.md`, etc.

---

## Spec lifecycle

```
draft → active → accepted → implemented
              ↘
                rejected
```

1. **Draft** — rough thinking, lives in journal or conversation
2. **Active** — formal spec written, under discussion, in `01_Specs/active/`
3. **Accepted** — agreed to implement, moved to `01_Specs/accepted/`
4. **Implemented** — shipped in Luna codebase, moved to `01_Specs/implemented/`
   with a reference to the implementing commit or PR
5. **Rejected** — considered and declined, moved to `01_Specs/rejected/`
   with reasoning recorded at the top of the file

Specs do not disappear. Even rejected specs stay in the tree as a record
of considered alternatives.

---

## Spec template

Every spec should include:

- **Status** (active/accepted/implemented/rejected)
- **Severity / priority**
- **Problem statement**
- **Root cause analysis** (if applicable)
- **Proposed solution**
- **Schema changes** (if format-affecting)
- **Migration path** (for existing cartridges)
- **Validation rules**
- **Governance implications**
- **Open questions**

See `01_Specs/TEMPLATE.md`.

---

## Current format version

**v0.2** — shipping format as of 2026-05-12, established by the four
bundled specs in `01_Specs/implemented/`:

- **SPEC-006** — `application_id` contract (`LUNC` for cartridge, `LUNM` for
  matrix), dual version tracking (`PRAGMA user_version=2` mirroring
  `meta.format_version='0.2'`), `cartridge_kind`, title validation,
  `source_filename` (basename, not absolute path), finalize pragma stack
  (`optimize` → `wal_checkpoint(TRUNCATE)` → `journal_mode=DELETE` →
  `VACUUM`).
- **SPEC-001** — `extractions.anchor_status` + `anchor_reason`,
  `claim_sources` provenance columns (`anchor_method`, `anchored_by`,
  `anchored_at`, `event_id`), `claim_context_nodes` table,
  `validate_anchors()`.
- **SPEC-002** — ULID columns on `doc_nodes` and `extractions` alongside
  integer rowids (additive; integer rowids removed in v0.3). FTS5 untouched.
- **SPEC-003** — `extractions.confidence` dropped; raw signals
  (`llm_logprob_sum`, `llm_token_count`, `extraction_method`) added, with
  `meta.logprob_base='e'` and `meta.logprob_attribution='response_level'`.

Atomic v0.1→v0.2 migration tool lives at `src/luna/cartridge/migrate.py` in
the Luna engine repo. A canonical `03_Format_Spec/LUN-FORMAT_v0.2.md` is
TBD (next planned spec-repo task); in the meantime the four implemented
specs are the source of truth.

**v0.1** — original shipping format (2026-04-10 → 2026-05-12), built for
`PRIESTS_AND_PROGRAMMERS_Lansing.lun`. Six first-class tables: `meta`,
`doc_nodes`, `extractions`, `claim_sources`, `embeddings`, plus the
`nodes_fts` virtual table and its FTS5 shadow tables. See
`03_Format_Spec/LUN-FORMAT_v0.1.md` for full historical documentation.

**v0.3** — in development. Two tracks:
- *Removal phase* per SPEC-002 D5: drop integer rowids on `extractions`
  (ULID becomes the operational PK). Also drops the columns flagged by
  `meta.deprecated_columns` in v0.2 builds. No spec drafted yet.
- *Governance arc* per SPEC-005 + companion (both `accepted/` 2026-05-21):
  `annotation_ledger` table, append-only triggers, SHA-256 hash chain,
  actor registry, 8 event types with per-type payload schemas. Engine
  implementation pending.

---

## Open concerns (as of 2026-05-21)

Governance arc — v0.2 was foundational, not governance. The next round
of work is what the foundation was built for:

1. **Ledger spec accepted, implementation pending** — SPEC-005
   (annotation ledger) and its companion SPEC-005_payload-schemas both
   moved to `01_Specs/accepted/` on 2026-05-21. Both specifications are
   complete; the engine repo has not yet implemented either. v0.3
   cartridges with the ledger ship when the engine implementation lands.
2. **Single confidence axis** — SPEC-003 produced raw signals; SPEC-004
   (implemented 2026-05-22) defines the four-axis composition
   (authority, contestation, temporal, resonance) and reader v0.3.1
   ships the canonical reference composer
   `lun.format/reference-v1@1.0.0`. v0.2 cartridges yield non-NULL
   Authority + Temporal axes immediately; Contestation + Resonance
   light up once the cartridge is rebuilt against v0.3 (ledger present).
3. **Contract verification table** [future spec]
4. **Role-based access metadata** [future spec — placeholder is the
   "actor roles spec" referenced from SPEC-005]

Carried-forward from v0.2 (tracked in implemented specs' Phase 5 closeouts):

5. Lansing 9.5%-baseline measurement undefined — Path B reconstruction lost
   `#` headings, producing a structurally-valid but 0/0 anchor cartridge
6. Form-feed `\x0c` artifacts surviving into `doc_nodes.content` from PDF
   page breaks
7. v0.3 territory: drop integer `extractions.id` (SPEC-002 D5; already
   flagged by `meta.deprecated_columns` in v0.2 builds)
8. Full SPEC-001 orphan semantic classification (synthesis/filtered
   detection deferred; likely SPEC-004 consumer territory)
9. Backend logprob exposure — `HaikuResult.usage` not surfaced yet
10. `magic.txt` upstream registration for `LUNC` (`0x4C554E43`) and `LUNM`
    (`0x4C554E4D`) with the SQLite project — courtesy, not load-bearing

Closed concerns from the 2026-04-21 audit (orphan claims, hardcoded
confidence, parser-mangled title, AUTOINCREMENT IDs) all shipped in v0.2.
See `01_Specs/implemented/` for the resolving specs.

---

## Working principles

1. **The file is the source of truth.** `schema.py` should be derived from
   or validated against a real `.lun`, not the other way around.
2. **Every cartridge gets audited before it ships.** Generic SQLite tooling
   should be sufficient — if only Luna can validate a `.lun`, the format
   has failed.
3. **Schema additions, not changes.** Every evolution should be backward
   compatible. Old readers read new files by ignoring what they don't know.
4. **Content-addressed over location-addressed.** Anything that crosses a
   cartridge boundary needs a stable identity.
5. **Separate data from interpretation.** Raw signals in the file,
   scoring algorithms in code.
6. **Invalid states should be unrepresentable.** Foreign keys, CHECK
   constraints, NOT NULL — use the database.
7. **Append-only where possible.** Ledgers, annotations, and history
   should never rewrite.

---

## Glossary

- **Cartridge** — a `.lun` file in the cartridge family; a portable knowledge unit
- **Family** — a set of related `.lun` files sharing a schema and `application_id`. Currently: cartridge (`'LUNC'`) and runtime matrix (`'LUNM'`)
- **application_id** — 4-byte SQLite header field that identifies the file format family. Set as a required contract per SPEC-006
- **Node** — an entry in `doc_nodes`; document, section, paragraph, or sentence
- **Extraction** — an LLM-generated summary, claim, or entity
- **Anchor** — a row in `claim_sources` linking a claim to a source node
- **Orphan claim** — a claim with no anchor row
- **Imprint** — the process of a claim entering the Memory Matrix with weight
- **The Wall** — the boundary between a cartridge's local data and the
  shared Memory Matrix
- **Annotation** — an event recorded against a cartridge by an actor
  (ambassador, elder, oracle)
- **Ledger** — the append-only log of annotation events within a cartridge
- **Contract** — a declared rule about who can do what with a cartridge
- **Spectral signature** — a compact representation of a cartridge's
  knowledge used for cross-community matching
