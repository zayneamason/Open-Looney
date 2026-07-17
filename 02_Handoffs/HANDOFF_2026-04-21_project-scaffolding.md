# HANDOFF: Project scaffolding and initial audit

**Date:** 2026-04-21
**Session type:** Initial project setup
**Participants:** Ahab, Claude
**Duration:** ~1 conversation arc, interrupted by MCP timeouts mid-scaffolding

---

## What happened this session

1. Opened a conversation about file format design principles, starting
   from general theory (text vs binary vs archive formats) and narrowing
   to design principles for `.lun` evolution (versioning, forward/backward
   compat, append-only, content addressing, etc.)

2. Received the SQLite source tree and README as uploads. Confirmed
   SQLite itself uses blockchain-style integrity (Fossil manifest with
   SHA3 hashes) for its own code — validating that the governance pattern
   Ahab is designing is already proven.

3. Opened SQLite Fiddle (WASM SQLite CLI in browser) and loaded
   `PRIESTS_AND_PROGRAMMERS_Lansing.lun` into it. Ran a live audit using
   only generic SQL. Proved the portability thesis and surfaced eight
   findings across meta, extractions, anchoring, and schema.

4. Decided to formalize this as an ongoing research project. Created
   folder structure at:
   `/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/`

5. Landed initial docs:
   - `00_README/README.md` — project overview, conventions, glossary
   - `01_Specs/TEMPLATE.md` — spec template
   - `01_Specs/active/SPEC-001_orphan-claims.md` — first real spec
   - `03_Format_Spec/LUN-FORMAT_v0.1.md` — current shipping format
   - `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md` — first audit

---

## Project purpose (short version)

Track the evolution of the `.lun` cartridge format from a v1 read artifact
into a governed, ledger-backed, multi-community knowledge substrate.
Separate research and specification from implementation. The folder is
the source of truth for what the format should become; Luna's codebase
implements what's accepted.

## Current state of things

### Format: v0.1 shipping (schema_version=1)

Six first-class tables plus FTS5 virtual table. Documented in
`03_Format_Spec/LUN-FORMAT_v0.1.md`. Known limitations captured there.

### Active specs

- **SPEC-001: Orphan claims** — active, ready for acceptance review.
  Proposes `anchor_status` column on `extractions` and new
  `claim_context_nodes` table to distinguish four orphan failure modes.
  Pure additive schema change. v0.1 cartridges remain read-compatible.

### Pending specs (identified in audit, not yet drafted)

- **SPEC-002: Portable node IDs** — AUTOINCREMENT integer IDs break
  cross-cartridge references. Proposal will be content-addressed or UUID
  identifiers for any node/extraction that crosses a boundary.

- **SPEC-003: Meaningful confidence scores** — current `confidence`
  column is hardcoded constants. Options: compute real scores, remove
  the column, or migrate to a multi-signal JSON column.

- **SPEC-004: Multi-axis imprint weights** — replaces single
  `confidence` score with separate authority/contestation/temporal/
  resonance dimensions. Depends on SPEC-001.

- **SPEC-005: Annotation events** — append-only ledger for actor
  events (ambassador upgrades, elder reconciliations, etc.). Depends
  on SPEC-001 and SPEC-004. Introduces governance primitives.

### Minor findings without specs yet

- M-01: Parser-mangled title ("/. Stephen Lansing") — build-time
  meta validation needed
- M-02: `source_path` leaks builder environment path
- S-02: No `application_id` pragma set
- S-03: No `user_version` pragma set (redundant with `schema_version`)

All captured in audit. Need a decision on whether each gets its own
small spec or rolls into a "v0.2 hygiene" bundle spec.

## Recommended next moves

In priority order, starting from next session:

### Immediate (next session)

1. **Review SPEC-001.** Does the `anchor_status` taxonomy capture the
   failure modes correctly? Are the invariants right? Answer the open
   questions, particularly #1 (min context nodes for synthesis) and #4
   (migration default for unclassifiable orphans).

2. **Decide spec acceptance process.** SPEC-001 is ready to move from
   active to accepted. What's the ceremony? Probably just: read, edit,
   move file. Document this in the README if adopting.

3. **Write `lun fsck` prototype.** Standalone Python script, no Luna
   imports, implements the validation checklist from
   `LUN-FORMAT_v0.1.md`. Lives in `06_Prototypes/`. Running it against
   `PRIESTS_AND_PROGRAMMERS_Lansing.lun` should reproduce the same
   findings this audit produced manually.

### Short-term (next few sessions)

4. **Audit a second cartridge.** Confirm findings are format-wide,
   not specific to this one build. Candidates in the Luna docs folder.

5. **Draft SPEC-002, SPEC-003, and a "v0.2 hygiene" bundle spec** for
   the minor findings. Decide whether governance specs (004, 005)
   need more design work before drafting.

6. **Re-run the audit with full schema dump.** Fiddle truncated the
   column lists for `doc_nodes` and `extractions`. Need the complete
   DDL to update `LUN-FORMAT_v0.1.md`.

### Medium-term (research arc)

7. **Design v0.2 format** as a concrete target. Combine accepted specs
   into a single format spec bump. Aim for: additive over v0.1, no
   breaking changes, hygiene fixes bundled with SPEC-001.

8. **Draft v0.3 governance spec skeleton.** Annotation events, ledger
   tables, actor roles. This is where the work with Memory Matrix
   integration gets real.

9. **Build a browser-based `.lun` viewer** using SQLite WASM. Forces
   us to clearly define what external consumers should be able to do
   with a cartridge. Doubles as reference implementation.

## Key design principles established

Captured in the README glossary and working principles section. Worth
repeating here because they should guide every future decision:

1. **The file is the source of truth.** `schema.py` derives from or
   validates against real `.lun` files, not the other way around.

2. **Every cartridge gets audited before it ships.** Using generic
   SQLite tooling — if only Luna can validate a `.lun`, the format has
   failed.

3. **Schema additions, not changes.** Every evolution is backward
   compatible. Old readers open new files by ignoring unknowns.

4. **Content-addressed over location-addressed.** Anything crossing a
   cartridge boundary needs stable identity.

5. **Separate data from interpretation.** Raw signals in the file,
   scoring algorithms in code.

6. **Invalid states should be unrepresentable.** Foreign keys, CHECK
   constraints, NOT NULL — use the database to enforce contracts.

7. **Append-only where possible.** Ledgers, annotations, and history
   should never rewrite.

## Open questions carried forward

- **Governance design is still loose.** Owner/ambassador/elder/oracle
  roles are named but their mechanics aren't specified yet. Need to
  push on these before SPEC-005.

- **Cross-cartridge traversal is an unopened problem.** Annotations
  bridge a single cartridge to the Memory Matrix, but two cartridges
  referencing each other hasn't been designed.

- **Binary format question deferred.** Custom binary / C-level format
  for hot-path reads was discussed and explicitly deferred as phase-5
  optimization. SQLite remains canonical.

- **Blockchain-style ledger vs simple append-only log.** Current
  thinking is hash-chained rows inside SQLite rather than distributed
  blockchain. But nothing's locked in yet.

## Session meta-notes

- MCP filesystem server hung twice during this session on long file
  writes. Workaround was to chunk writes into ≤30 line pieces via
  Desktop Commander. For future sessions, default to chunked writes
  on specs and audits; they're long.

- SQLite Fiddle as an audit surface worked beautifully. Zero setup,
  real CLI, full portability proof. Recommend this as the canonical
  audit method until/unless something specific requires Luna-side
  tooling.

- Conversation ran long and got into good structural territory.
  Worth pulling key ideas back into permanent docs (this folder)
  rather than losing them in chat history.
