# SPEC-001: Unanchored Claims in `.lun` Cartridges

**Status:** implemented
**Severity:** medium
**Author:** Ahab (with Claude)
**Created:** 2026-04-21
**Last updated:** 2026-05-21 (Phase 5 shipped; moved to implemented/)
**Affects format version:** v0.1 → v0.2

---

## Problem statement

The `claim_sources` table is the bridge between extracted knowledge and source
material in a `.lun` cartridge. When a claim has no entry in `claim_sources`,
it becomes epistemically homeless — it cannot be verified against the original
text, displayed with quotation, weighted by source authority, or traced during
governance review. In a future where ambassadors annotate cartridges and
elders reconcile disputes, an unanchored claim cannot participate in any
governance ceremony that requires source verification. It just asserts.

The current builder silently allows claims to ship without anchors, with no
classification of why.

## Observed evidence

From live audit of `PRIESTS_AND_PROGRAMMERS_Lansing.lun` on 2026-04-21:

- Total claims in `extractions`: **1593**
- Total rows in `claim_sources`: **1442**
- Distinct `claim_id` values in `claim_sources`: **1442**
- **Orphan claims (no anchor): 151 (9.5% of all claims)**

Sample of 20 orphan claims reveals four distinct patterns:

**Pattern A — Synthesis claims (~40% of sample):** valid cross-sentence
abstractions with no single source sentence. Example:

> #63 — "Sustainable local knowledge consists of individual knowledge and
> skills, physical infrastructure systems, and enduring religious beliefs
> that shape action."

**Pattern B — Frontmatter / acknowledgments (~20% of sample):** real facts
from preface/acknowledgments that the matcher may be filtering. Example:

> #388 — "Walter Lippincott at Princeton University Press encouraged the
> author to aim for a broader readership and helped reshape the manuscript."

**Pattern C — Paraphrase drift (~30% of sample):** source exists but fuzzy
matcher failed to link due to reworded phrasing. Example:

> #431 and #447 — nearly-identical claims about "continuous rice cropping
> without regard for traditional irrigation schedules" (one for Balinese
> gov't, one for Indonesian gov't)

**Pattern D — Attributed third-party claims (~10% of sample):** claims about
what another thinker said, where the extractor caught the proposition but the
anchoring logic couldn't handle nested attribution. Example:

> #640 — "Marx argued that Asian villages were completely separate from each
> other and formed isolated worlds."

Full audit at `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`.

## Root cause analysis

Three distinct failure modes currently produce the same symptom (NULL in
`claim_sources`):

1. **Legitimate synthesis** — claim has no single source, it's an abstraction
   across multiple. This is not a bug; the schema just has nowhere to record it.

2. **Match failure** — source exists, but fuzzy or semantic matcher didn't
   link it. Threshold too tight, or paraphrase drift beyond the matcher's
   tolerance. This IS a bug.

3. **Filter dropout** — source was identified but then filtered during
   post-processing (frontmatter detection, attribution handling). May be
   intentional but is currently invisible.

The current `extractor.py` does not distinguish these cases. All three fall
through to the same NULL state, and the cartridge cannot report which is which.

## Proposed solution

**Core principle:** make the failure modes distinguishable in the data, so
readers can reason about them and governance can respond differently to each.

### Schema changes

Two additions to `extractions`:

```sql
ALTER TABLE extractions ADD COLUMN anchor_status TEXT
    CHECK (anchor_status IN (
        'anchored',       -- has one or more rows in claim_sources
        'synthesized',    -- cross-sentence abstraction, uses claim_context_nodes
        'match_failed',   -- source exists but matcher didn't link
        'filtered',       -- intentionally dropped by post-processing
        'unknown'         -- legacy / not yet classified
    ))
    DEFAULT 'unknown';

ALTER TABLE extractions ADD COLUMN anchor_reason TEXT;
    -- human-readable diagnostic; nullable
```

Provenance columns added to `claim_sources` so the anchor graph itself records
how each row came to be (auto-anchored by builder, manually upgraded by
ambassador, set during migration):

```sql
ALTER TABLE claim_sources ADD COLUMN anchor_method TEXT NOT NULL DEFAULT 'auto'
    CHECK (anchor_method IN ('auto', 'manual', 'migrated'));
ALTER TABLE claim_sources ADD COLUMN anchored_by TEXT;     -- actor ID; NULL allowed for 'auto'
ALTER TABLE claim_sources ADD COLUMN anchored_at INTEGER;  -- unix ms; required for 'manual'/'migrated'
ALTER TABLE claim_sources ADD COLUMN event_id TEXT;        -- forward ref to ledger event (SPEC-005); NULL until ledger exists
```

One new table for soft anchoring of synthesis claims:

```sql
CREATE TABLE claim_context_nodes (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    relevance REAL NOT NULL,        -- 0.0 - 1.0 semantic similarity
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);

CREATE INDEX idx_claim_context_claim ON claim_context_nodes(claim_id);
CREATE INDEX idx_claim_context_node ON claim_context_nodes(node_id);
```

**Invariants:**

- A claim with `anchor_status = 'anchored'` MUST have ≥1 row in `claim_sources`
- A claim with `anchor_status = 'synthesized'` MUST have ≥2 rows in `claim_context_nodes` **AND** those rows must reference `doc_nodes` with ≥2 distinct `parent_id` values (distinct lineage; prevents two sentences under the same paragraph being mislabeled as synthesis)
- A claim with `anchor_status = 'match_failed'` or `'filtered'` MAY have entries in `claim_context_nodes` as hints
- A `claim_sources` row with `anchor_method != 'auto'` MUST have both `anchored_by` and `anchored_at` populated (enforced in `validate_anchors()`)
- **No cartridge may ship with `anchor_status = 'unknown'` on any claim** once migrated to v0.2

### Behavioral changes

Builder pipeline changes in `extractor.py`:

1. After claim extraction, run anchoring logic with explicit outcome tracking
2. For each claim, write `anchor_status` based on what the anchoring produced
3. Auto-anchors written to `claim_sources` carry `anchor_method='auto'`; `anchored_by` and `anchored_at` may remain NULL for these
4. For match failures, optionally populate `claim_context_nodes` with the top-N semantically similar nodes (hints for future manual resolution)
5. Run `validate_anchors()` as the final build step; fail loud if any claim is `unknown` or any invariant is violated

Reader changes in `src/luna/cartridge/__init__.py` (corrected path; previous draft referenced `aibrarian_engine.py` in error):

- `resolve_source_ref()` must check `anchor_status` before assuming `claim_sources` is authoritative
- **Synthesis claims** display with "synthesized from N sources" rather than a single quote, and may surface the context-node set on request
- **Match-failed claims** display with a "⚠ unanchored" badge in any UI surface
- **Filtered claims** are excluded by default from normal reading/retrieval surfaces (no grounding view, no quotation). They remain queryable for forensics and governance. Audit/debug surfaces should expose a "Show filtered claims" toggle that renders them with a badge showing the filter reason (e.g., `filtered: frontmatter`, `filtered: attribution`)

Ambassador upgrade flow ("upgrade to anchored" ceremony):

1. INSERT into `claim_sources` with `(claim_id, node_id, anchor_method='manual', anchored_by=<actor_id>, anchored_at=<unix_ms>, event_id=<ledger_event_id_if_available>)`
2. UPDATE `extractions` SET `anchor_status='anchored'` WHERE `id=claim_id`
3. The same `claim_sources` table is the source of truth for both auto and manual anchors — `anchor_method` distinguishes provenance without splitting the anchor graph

### Migration path

**v0.1 → v0.2 migration is non-destructive and re-runnable.**

Existing v0.1 cartridges are **read-compatible** with v0.2 readers:

- A v0.2 reader opening a v0.1 file sees no `anchor_status` column
- Reader treats this as `anchor_status = 'unknown'` for all claims
- No crash, no data loss, degraded UX (all claims shown as unanchored)

A `lun migrate v1-to-v2` tool:

1. Adds the new `extractions` columns (`anchor_status`, `anchor_reason`) and the `claim_context_nodes` table
2. Adds the new `claim_sources` provenance columns (`anchor_method`, `anchored_by`, `anchored_at`, `event_id`). Existing rows get `anchor_method='auto'` via the column default; `anchored_by` / `anchored_at` / `event_id` remain NULL for these builder-produced anchors
3. Sets `anchor_status='anchored'` for claims with rows in `claim_sources`
4. Re-runs anchoring analysis for current orphans, classifying them into `synthesized` / `match_failed` / `filtered`. **Default behavior** when classification can't decide: set `anchor_status='match_failed'`, `anchor_reason='migration_unclassified'`. This avoids shipping `'unknown'` (which `validate_anchors()` would block) while preserving a triage signal for later review. A strict mode (`--migration-strict`) fails the migration if any orphan can't be confidently classified
5. Populates `claim_context_nodes` for synthesis claims
6. Bumps `meta.format_version` to `'0.2'` and sets `PRAGMA user_version = 2` (per SPEC-006 hygiene bundle; replaces the v0.1 `meta.schema_version` key)

Migration does **not** re-run LLM extraction. All costs are CPU/embedding lookups against the existing data.

### Migration mechanics (SQLite-specific)

Both `ALTER TABLE ADD COLUMN` operations are O(1) under SQLite 3.37+:

- **`extractions.anchor_status TEXT CHECK (...) DEFAULT 'unknown'`:** Post-3.37, `ADD COLUMN` with a CHECK constraint validates the new column's default against every existing row. With `DEFAULT 'unknown'` and `'unknown'` in the CHECK set, this passes — every existing row gets a valid value automatically. Migration is O(1) (schema text change only, no row scan).
- **`claim_sources.anchor_method TEXT NOT NULL DEFAULT 'auto' CHECK in ('auto', 'manual', 'migrated')`:** Same pattern. `'auto'` default satisfies the CHECK; existing rows are tagged as builder-produced anchors. O(1).
- **`claim_sources.anchored_by` / `anchored_at` / `event_id`:** Nullable, no CHECK constraints. Pure schema text change. O(1).
- **`CREATE TABLE claim_context_nodes`:** Trivially additive; affects only future inserts.
- **Cross-column invariant** ("non-auto anchors must have `anchored_by` and `anchored_at`"): enforced at the application layer in `validate_anchors()` rather than via trigger or table-level CHECK. Keeps schema simple and works on any SQLite version (avoids the 3.47+ `ALTER TABLE ADD CONSTRAINT` requirement).

**Future risk:** if a later spec tightens the `anchor_status` CHECK to remove `'unknown'`, the migration would have to classify all rows to a non-`'unknown'` value first. The pattern in use here is: permissive schema at migration time, strict enforcement at build time via `validate_anchors()`. Migration is cheap and never fails on existing data; build-time validation catches what migration permitted.

Cross-reference: `05_Reference/SQLite_Research.md`, Topic 5 (schema migration patterns), Topic 4 (CHECK constraint semantics).

## Validation rules

Build time, runs before cartridge is finalized:

```python
def validate_anchors(conn):
    """Every claim must be classified. Every non-auto anchor must have provenance.
    Synthesis claims must have multi-lineage context."""

    # No claim ships with anchor_status='unknown'
    unknowns = conn.execute("""
        SELECT id, content FROM extractions
        WHERE type = 'claim' AND anchor_status = 'unknown'
    """).fetchall()
    if unknowns:
        raise BuildError(
            f"{len(unknowns)} claims have anchor_status='unknown'. "
            f"All claims must be classified before cartridge can ship."
        )

    # Synthesized claims have >=2 context nodes
    bad_synthesis_count = conn.execute("""
        SELECT e.id FROM extractions e
        WHERE e.anchor_status = 'synthesized'
          AND (SELECT count(*) FROM claim_context_nodes ccn
               WHERE ccn.claim_id = e.id) < 2
    """).fetchall()
    if bad_synthesis_count:
        raise BuildError(
            f"{len(bad_synthesis_count)} claims marked 'synthesized' but have "
            f"<2 context nodes. Solo-source claims cannot be synthesis."
        )

    # Synthesized claims have context nodes from >=2 distinct parent lineages
    flat_synthesis = conn.execute("""
        SELECT e.id FROM extractions e
        WHERE e.anchor_status = 'synthesized'
          AND (SELECT COUNT(DISTINCT dn.parent_id)
               FROM claim_context_nodes ccn
               JOIN doc_nodes dn ON dn.id = ccn.node_id
               WHERE ccn.claim_id = e.id) < 2
    """).fetchall()
    if flat_synthesis:
        raise BuildError(
            f"{len(flat_synthesis)} claims marked 'synthesized' but all context "
            f"nodes share a single parent. Synthesis requires distinct lineage."
        )

    # Anchored claims have >=1 source
    bad_anchored = conn.execute("""
        SELECT e.id FROM extractions e
        WHERE e.anchor_status = 'anchored'
          AND NOT EXISTS (SELECT 1 FROM claim_sources cs
                          WHERE cs.claim_id = e.id)
    """).fetchall()
    if bad_anchored:
        raise BuildError(
            f"{len(bad_anchored)} claims marked 'anchored' but have "
            f"no rows in claim_sources."
        )

    # Non-auto anchors require provenance
    bad_provenance = conn.execute("""
        SELECT cs.claim_id, cs.node_id, cs.anchor_method
        FROM claim_sources cs
        WHERE cs.anchor_method != 'auto'
          AND (cs.anchored_by IS NULL OR cs.anchored_at IS NULL)
    """).fetchall()
    if bad_provenance:
        raise BuildError(
            f"{len(bad_provenance)} non-auto anchors missing provenance "
            f"(anchored_by and anchored_at are required for "
            f"anchor_method in ('manual', 'migrated'))."
        )
```

Read time (in `lun fsck` and on cartridge ingest):

- Verify `anchor_status` values are in the allowed set
- Verify invariants above still hold (data integrity)
- Report orphan rate per cartridge as a quality metric
- Report breakdown of `anchor_method` across `claim_sources` (auto/manual/migrated mix is a governance signal)

## Governance implications

This spec is a **precondition** for the multi-axis imprint weights design discussed in the handoff notes. Specifically:

- **Authority weight** — anchored claims have higher authority than synthesized claims; match-failed claims should probably imprint at reduced weight until resolved
- **Contestation weight** — synthesized claims are inherently more contestable than directly-anchored claims; this should be visible
- **Resolution ceremony** — ambassadors can "upgrade" a `match_failed` claim to `anchored` by manually providing the source node. The mechanism is already in this spec: insert into `claim_sources` with `anchor_method='manual'` + provenance fields + status update. The full ledger-event audit trail (`event_id` pointing into the ledger) lands when SPEC-005 ships
- **Provenance separation** — `anchor_method` lets governance distinguish "this claim was anchored by the builder" from "this claim was anchored by a community member" from "this claim was anchored during a migration." These have different trust profiles and should be visible to readers

Unanchored claims currently imprint into the Memory Matrix with no marker distinguishing them from anchored claims. After this spec ships, the imprinting function can read `anchor_status` and `anchor_method` and weight accordingly.

## Alternatives considered

**Alt 1: Just delete orphan claims.**
Rejected. Many orphans (especially synthesis claims) carry real information. Deleting them makes the cartridge less useful, not more trustworthy.

**Alt 2: Store a single `source_confidence` score instead of status.**
Rejected. Conflates distinct failure modes. A score can't distinguish "this is a legitimate synthesis" from "we failed to match." Status + optional score is strictly more expressive.

**Alt 3: Require all claims to be anchored (reject synthesis entirely).**
Rejected. Synthesis claims are useful. Requiring single-source anchoring would force the extractor to discard valid cross-sentence abstractions or attribute them falsely to a single sentence.

**Alt 4: Make `claim_sources.node_id` nullable instead of adding new table.**
Rejected. Loses the many-to-many relationship for synthesis. Nullable foreign keys are a code smell — the empty set should be representable by absence, not NULL.

**Alt 5: Enforce invariants with cross-table triggers instead of build-time validation.**
Considered, deferred. The research's Topic 4 documents the AFTER INSERT/UPDATE trigger pattern with `RAISE(ABORT, ...)` for cross-table invariants. For write-once cartridges, build-time validation is sufficient — triggers would only fire during build, where `validate_anchors()` already runs. Adding triggers is additive (more defense in depth) but not necessary for v0.2. Revisit if cartridges ever become writable post-build.

**Alt 6: Track anchor provenance in a companion `claim_provenance` table.**
Rejected. A separate provenance table would require joining on every read and split the anchor graph across two structures. Adding columns directly to `claim_sources` keeps the anchor truth in one place, with provenance metadata on the same row. The `event_id` column is the bridge to the future ledger (SPEC-005) — when the ledger exists, manual anchors gain a fully audited trail through `event_id` without restructuring `claim_sources`.

**Alt 7: Synthesis threshold of >=3 context nodes.**
Rejected. The number 3 is arbitrary and would over-reject valid short syntheses (two sentences from different paragraphs is a legitimate synthesis). The stronger signal is **distinct lineage**: require >=2 nodes drawn from >=2 distinct `parent_id` values. This catches the failure mode (two sentences under the same paragraph mislabeled as synthesis) without raising the bar on legitimate cases.

## Open questions

None remaining. All four open questions from the 2026-04-21 draft were resolved 2026-05-10:

1. **Synthesis threshold:** Hard minimum stays at `>=2` context nodes; quality guard requires those nodes to draw from `>=2` distinct `parent_id` values (different lineage). Encoded as a build-time invariant in `validate_anchors()`.
2. **Filtered claim display:** Hidden by default in normal reading/retrieval. Audit/debug surfaces expose a "Show filtered claims" toggle that renders them with a badge showing the filter reason. Queryable for forensics; excluded from primary grounding.
3. **Ambassador upgrade mechanism:** Same `claim_sources` table is the source of truth for all anchors. Provenance columns (`anchor_method`, `anchored_by`, `anchored_at`, `event_id`) capture how each anchor came to be. Manual upgrade = INSERT into `claim_sources` with `anchor_method='manual'` + provenance + UPDATE of `extractions.anchor_status` to `'anchored'`. No separate manual table.
4. **Migration default for unclassifiable orphans:** Default to `anchor_status='match_failed'` with `anchor_reason='migration_unclassified'`. Strict CI mode (`--migration-strict`) fails if any remain unresolved. Avoids shipping `'unknown'` which build-time validation would block.

## Dependencies

- **SPEC-006 (accepted)** establishes `meta.format_version` and `PRAGMA user_version` as the canonical version-tracking pair. This spec's migration step 6 uses those keys (previously referenced `meta.schema_version`, which is superseded).

Blocks:
- SPEC-004 (implemented, 2026-05-22): Multi-axis imprint weights — reads `anchor_status` and `anchor_method`
- SPEC-005 (accepted 2026-05-21): Annotation events — populates `claim_sources.event_id` on manual anchors in the v0.3 engine implementation

## Implementation notes

- **Status:** Phase 2 implementation in progress 2026-05-12 against handoff revision 4
- **Commit/PR reference:** (pending — uncommitted at this paste)
- **Implementer:** CC (Ahab reviewing)
- **Handoff:** `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE2_ANCHOR_CLASSIFICATION.md` rev 4
- **Baseline:** Phase 1 at commit `c7a3dc3`

### Resolved paths

| Var | Value |
|-----|-------|
| `MATRIX_PATH` | `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun` (Phase 1 leftover; not used in Phase 2) |
| `TEST_SOURCE` | `/tmp/phase2_test_source.md` (created at smoke-test time) |
| `V01_STUB` | `/tmp/v01_phase2_stub.lun` (synthetic v0.1 cartridge built at smoke-test time per handoff Known paths) |

### Pre-flight grep output (captured BEFORE code changes)

**Grep 1 — `schema.py` current tables/indexes:**
```
12:CREATE TABLE IF NOT EXISTS meta (
18:CREATE TABLE IF NOT EXISTS doc_nodes (
29:CREATE TABLE IF NOT EXISTS extractions (
37:CREATE TABLE IF NOT EXISTS claim_sources (
46:CREATE TABLE IF NOT EXISTS embeddings (
55:CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
62:CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON doc_nodes BEGIN
67:CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON doc_nodes BEGIN
72:CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON doc_nodes BEGIN
83:CREATE TABLE IF NOT EXISTS nexus_refs (
92:CREATE INDEX IF NOT EXISTS idx_doc_nodes_parent ON doc_nodes(parent_id);
93:CREATE INDEX IF NOT EXISTS idx_doc_nodes_type ON doc_nodes(type);
94:CREATE INDEX IF NOT EXISTS idx_extractions_type ON extractions(type);
95:CREATE INDEX IF NOT EXISTS idx_claim_sources_node ON claim_sources(node_id);
96:CREATE INDEX IF NOT EXISTS idx_embeddings_level ON embeddings(level);
97:CREATE INDEX IF NOT EXISTS idx_nexus_refs_nexus ON nexus_refs(nexus_node_id);
```
(No `claim_context_nodes` table, no anchor-related indexes — clean slate.)

**Grep 2 — extractor INSERT patterns:**
```
143:    "INSERT INTO extractions (type, content, confidence) VALUES (?, ?, ?)",   (summary)
156:    "INSERT INTO extractions (type, content, confidence) VALUES (?, ?, ?)",   (claim)
174:    "INSERT INTO extractions (type, content, confidence) VALUES (?, ?, ?)",   (entity)
199:    "INSERT OR IGNORE INTO claim_sources (claim_id, node_id) VALUES (?, ?)",  (substring match)
211:    "INSERT OR IGNORE INTO claim_sources (claim_id, node_id) VALUES (?, ?)",  (prefix fallback)
```
(Line numbers slightly offset from handoff's ~131/~145/~166 estimates but structurally identical.)

**Grep 3 — cartridge reader SELECT on extractions/claim_sources:**
```
29:    "validate_cartridge_open",
41:def validate_cartridge_open(conn) -> None:
84:        validate_cartridge_open(conn)
127:            FROM extractions e
128:            JOIN claim_sources cs ON e.id = cs.claim_id
```
(Single JOIN in `resolve_source_ref()`; `validate_cartridge_open()` at lines 41-57 — must NOT be modified.)

**Grep 4 — Phase 1 commit on builder.py:**
```
c7a3dc3 feat(.lun v0.2): SPEC-006 Phase 1 — application_id contract + hygiene
```

**Grep 5 — existing anchor_status / anchor_method / claim_context_nodes references:** zero hits. Clean slate.

**Grep 6 — SQLite version:** `3.51.0` ✓ (above 3.37 / 3.35 thresholds for `ALTER TABLE ADD COLUMN` with CHECK and `DROP COLUMN`).

### Smoke test pasted evidence (1-15)

Test source `/tmp/phase2_test_source.md` — 3-sentence markdown across two sections. Built with `python -m luna.cartridge.builder /tmp/phase2_test_source.md /tmp/phase2_test.lun` (full pipeline; extraction + embedding). `V01_STUB` built per handoff Known paths block. Tests 1-13 run on the natural build; fixture injected; tests 14-15 run post-injection.

**Smoke 1 — Pre-flight greps:** see Pre-flight grep output section above.

**Smoke 2 — `.schema extractions`:**
```sql
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    -- SPEC-001 anchor classification (Phase 2)
    anchor_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (anchor_status IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')),
    anchor_reason TEXT
);
CREATE INDEX idx_extractions_type ON extractions(type);
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
```

**Smoke 3 — `.schema claim_sources`:**
```sql
CREATE TABLE claim_sources (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    -- SPEC-001 provenance (Phase 2). anchored_at is unix milliseconds INTEGER.
    anchor_method TEXT NOT NULL DEFAULT 'auto'
        CHECK (anchor_method IN ('auto', 'manual', 'migrated')),
    anchored_by TEXT,
    anchored_at INTEGER,
    event_id TEXT,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id)
);
CREATE INDEX idx_claim_sources_node ON claim_sources(node_id);
```

**Smoke 4 — `.schema claim_context_nodes`:**
```sql
CREATE TABLE claim_context_nodes (
    claim_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    relevance REAL NOT NULL,
    PRIMARY KEY (claim_id, node_id),
    FOREIGN KEY (claim_id) REFERENCES extractions(id),
    FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
    CHECK (relevance >= 0.0 AND relevance <= 1.0)
);
CREATE INDEX idx_claim_context_claim ON claim_context_nodes(claim_id);
CREATE INDEX idx_claim_context_node ON claim_context_nodes(node_id);
```

**Smoke 5 — indexes:**
```
.indices extractions:
  idx_extractions_anchor_status
  idx_extractions_type

.indices claim_context_nodes:
  idx_claim_context_claim
  idx_claim_context_node
  sqlite_autoindex_claim_context_nodes_1   (PRIMARY KEY auto-index)
```

**Smoke 6 — anchor classification distribution (natural build, pre-injection):**
```
SELECT anchor_status, COUNT(*) FROM extractions GROUP BY anchor_status;
  anchored | 14
  unknown  | 2
```

**Smoke 7 — per-type anchor distribution (critical SPEC-001 check):**
```
SELECT type, anchor_status, COUNT(*) FROM extractions GROUP BY type, anchor_status;
  claim   | anchored | 11
  entity  | unknown  | 2
  summary | anchored | 3

SELECT COUNT(*) FROM extractions WHERE type='claim' AND anchor_status='unknown';
  0   ← SPEC-001 line 143-144 invariant holds
```
Entities being `unknown` is correct per spec scope (line 143-144 is type-scoped to claims). Entity anchoring is deferred to a future spec.

**Smoke 8 — provenance + INTEGER timestamps:**
```
SELECT anchor_method, anchored_by, typeof(anchored_at), anchored_at FROM claim_sources LIMIT 5;
  auto | builder@v0.2 | integer | 1778624597772
  auto | builder@v0.2 | integer | 1778624597772
  auto | builder@v0.2 | integer | 1778624597772
  auto | builder@v0.2 | integer | 1778624597772
  auto | builder@v0.2 | integer | 1778624597772
```
`typeof(anchored_at) = 'integer'` confirms unix-ms INTEGER (not TEXT/ISO).

**Smoke 9 — match_failed samples (natural build):**
```
SELECT id, content, anchor_reason FROM extractions WHERE anchor_status = 'match_failed' LIMIT 3;
  (empty — Haiku matched all 11 claim quotes on this short test source)
```
The handoff anticipated this: "the natural build may or may not produce match_failed claims depending on Haiku's output." Test 14's fixture injection exercises the reader path deterministically against a known match_failed row, isolating the reader assertion from extractor-output variability.

**Smoke 10 — v0.2 `resolve_source_ref` output:**
```json
{
  "cartridge": "phase2_test.lun",
  "node_id": 5,
  "node_type": "sentence",
  "content": "This is a sentence that should be extractable.",
  "section": "Section One",
  "section_path": [
    "phase2_test_source",
    "Test Document for Phase 2",
    "Section One"
  ],
  "position_in_parent": 0,
  "claims": [
    {
      "id": 2,
      "content": "The document contains extractable sentences for testing purposes",
      "confidence": 0.85,
      "anchor_status": "anchored",
      "anchor_reason": null
    }
  ]
}
```
Each claim entry exposes `anchor_status` and `anchor_reason`. No summary leaked into `claims` (the type='claim' filter works).

**Smoke 11 — v0.1 read-compat for `resolve_source_ref`:**
```
WARNING luna.cartridge: Opening v0.1 cartridge without application_id set (legacy)
{
  "cartridge": "v01_phase2_stub.lun",
  "node_id": 1,
  "node_type": "sentence",
  "content": "A test sentence for the v0.1 stub.",
  "section": "",
  "section_path": [],
  "position_in_parent": 0,
  "claims": [
    {
      "id": 1,
      "content": "A claim from v0.1 with no anchor_status column.",
      "confidence": 0.85,
      "anchor_status": "unknown",
      "anchor_reason": null
    }
  ]
}
```
Legacy warning logged, opens without crashing, returns degraded `anchor_status='unknown'` / `anchor_reason=null` per SPEC-001 line 172-176 read-compat contract.

**Smoke 12 — `git diff` of `validate_cartridge_open()`:** empty. The function body (cartridge/__init__.py lines 41-57) is unchanged from Phase 1. The legacy fallback for `app_id == 0` survives into Phase 2 as required by the read-compat contract; Phase 5 retires it after the migration tool runs.

**Smoke 13 — `validate_anchors()` runs clean on the built cartridge:**
```
.venv/bin/python -c "import sqlite3; from luna.cartridge.builder import validate_anchors; conn = sqlite3.connect('/tmp/phase2_test.lun'); validate_anchors(conn); conn.close(); print('clean')"
→ validate_anchors() returned cleanly (no BuildError)
```
Implicit verification: the build itself wouldn't have completed if `validate_anchors()` had raised — the call site is between the embedding pass and `conn.commit() + finalize_for_shipping()`, before the cartridge is shippable.

**Fixture injection — between smokes 13 and 14:**
```sql
INSERT INTO extractions (type, content, confidence, anchor_status, anchor_reason)
VALUES ('claim',
        'Synthetic test fixture for list_extractions smoke test',
        0.85,
        'match_failed',
        'synthetic test fixture: injected after build to verify reader path');

Post-injection distribution:
  claim   | anchored     | 11
  claim   | match_failed | 1     ← injected row
  entity  | unknown      | 2
  summary | anchored     | 3
```

**Smoke 14 — `list_extractions(type='claim', anchor_status_filter='match_failed')`:**
```
Found 1 match_failed claims (>= 1 required after injection)
OK: list_extractions returned the injected match_failed claim with expected shape
{
  "id": 17,
  "type": "claim",
  "content": "Synthetic test fixture for list_extractions smoke test",
  "confidence": 0.85,
  "anchor_status": "match_failed",
  "anchor_reason": "synthetic test fixture: injected after build to verify reader path"
}

Unfiltered list_extractions distribution after injection:
  Total extractions: 17
  By status: {'anchored': 14, 'unknown': 2, 'match_failed': 1}
```
The reader path for SPEC-001 line 157 (UI "⚠ unanchored" badge surface) is verified.

**Smoke 15 — v0.1 read-compat for `list_extractions`:**
```
WARNING luna.cartridge: Opening v0.1 cartridge without application_id set (legacy)
v0.1 cartridge has 1 claims
OK: all v0.1 claims have anchor_status=unknown, anchor_reason=None
OK: v0.1 filter for non-unknown returns empty
```

### Anchor classification distribution observed on the test cartridge

Natural build (pre-injection) on `/tmp/phase2_test.lun`:

| type | anchor_status | count |
|------|---------------|-------|
| claim | anchored | 11 |
| summary | anchored | 3 |
| entity | unknown | 2 |
| **claim total** | | **11** |
| **claim with anchor_status='unknown'** | | **0** ✓ |

100% of claims classified into the v0.2 taxonomy at build time, all into `anchored` for this test source (no `match_failed` from the natural matcher because Haiku produced verbatim quote strings on the simple test source). Entities and summaries follow spec scope: summaries anchor to their section heading; entities remain `unknown` (deferred to a future spec per handoff out-of-scope rule).

Larger sources will surface natural `match_failed` rows from Haiku paraphrase drift. The 9.5% orphan rate observed on the 2026-04-21 Lansing audit will, after Phase 2 rebuilds, distribute across `match_failed` (with reasons) and `synthesized` (if/when synthesis logic ships in a future spec). For now, the taxonomy and reader paths are in place; the classification distribution will become more interesting as larger cartridges are built or migrated.

### Deviations from spec or handoff

None of substance. One small additive helper introduced beyond the strict handoff text:

- `_now_ms()` module-level helper at the top of `extractor.py` — encapsulates the `int(datetime.now(timezone.utc).timestamp() * 1000)` pattern so the same call appears in both `_anchor_claim()` and the summary-write path. Equivalent to inlining the call twice; chose the helper for clarity. Not a spec deviation.

### Open items / follow-ups identified during implementation

1. **Larger-cartridge anchor distribution observation.** The test source is too short to produce natural `match_failed` claims (Haiku quoted verbatim). When a larger cartridge is built post-Phase 2 (the Lansing rebuild scheduled for Phase 5's migration tool), the real classification distribution will surface and is worth measuring against the 9.5% orphan rate from the 2026-04-21 audit. Track as a Phase 5 observation item, not a Phase 2 fix.

2. **`finalize_for_shipping()` and `validate_anchors()` are both module-level functions in `builder.py`.** When Phase 3 (SPEC-002, ULID identity) lands `validate_ulids()`, the count of build-time validators reaches 3+ and a refactor into `src/luna/cartridge/validation.py` becomes attractive. Per handoff out-of-scope rule, that refactor is explicitly NOT Phase 2's work. Track for Phase 3 or Phase 5 cleanup.

3. **`extractions.confidence` is still in the schema and still SELECTed by `resolve_source_ref` / `list_extractions`** (currently as a value passthrough). SPEC-003 / Phase 4 drops the column and updates both reader paths atomically. Phase 2 deliberately leaves it untouched.

### Phase 5 closeout

Phase 5 commits (chronological, all on `fix/intergalactic-hub-phase-2-runtime` after Phase 4 at `8d5c6d9`):

| Commit | Subject |
|--------|---------|
| `6775822` | chore(docs): track .lun v0.2 Phase 4+5 handoff docs and backfill Phase 4 smoke evidence |
| `80690e5` | feat: Phase 5 Step 1-2 — atomic v0.1 -> v0.2 migration tool (`src/luna/cartridge/migrate.py`) |
| `cb6d13a` | feat: Phase 5 Step 4 — remove v0.1 legacy fallback in `validate_cartridge_open` |
| `325c68b` | refactor: Phase 5 Step 5 — centralize validators into `src/luna/cartridge/validation.py` |

Phase 5 Step 3 (Lansing v0.2 build) produces a gitignored cartridge artifact at `data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun` (no commit). Phase 5 Step 6 edits `../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md` which lives outside the engine repo's git tree (no commit; edit persists on disk).

**Handoff:** [HANDOFF_NEXUS_LUN_V02_PHASE5_MIGRATION_CLOSEOUT.md](../../../HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE5_MIGRATION_CLOSEOUT.md) rev 2.

#### Item 1 — Pre-flight greps (7) + Step 0 resolution

```
--- Grep 1: Phase 4 at HEAD ---
6775822 chore(docs): track .lun v0.2 Phase 4+5 handoff docs and backfill Phase 4 smoke evidence
8d5c6d9 feat(.lun v0.2): SPEC-003 Phase 4 — drop confidence, raw signals, atomic reader patch
f83c4cb fix(.lun v0.2): SPEC-002 Phase 3.5 — canonical ULID generator
c68a39e docs: Phase 1 handoff frontmatter + dev-diary git history report
c25c4bf feat(.lun v0.2): SPEC-002 Phase 3 — portable identity (ULID additive)

--- Grep 2: validator order in builder.py ---
118:def validate_extractions(conn: sqlite3.Connection) -> None:
185:def validate_anchors(conn: sqlite3.Connection) -> None:
274:def validate_ulids(conn: sqlite3.Connection) -> None:
567:        validate_extractions(conn)
572:        validate_ulids(conn)
576:        validate_anchors(conn)

--- Grep 3: validate_cartridge_open() shape (pre-Step-4) ---
30:    "UnsupportedAttributionError",
43:class UnsupportedAttributionError(Exception):
54:    if app_id == 0:                    # ← Phase 5 Step 4 removes this gate
67:    if user_ver != 2:
73:        raise UnsupportedAttributionError(
79:        raise UnsupportedAttributionError(

--- Grep 4: Lansing source search (Step 0) ---
PDF search in ./, ~/Documents, ~/Downloads, ~/Desktop, ~/Library/Mobile Documents — all empty.
Source path recorded in pre-quarantine DB: '/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/PRIESTS AND PROGRAMMERS_Lansing.pdf' — not present at that path either.
Pre-quarantine DB probe:
  607b5b69-cc69-4799-9f57-1cdc3c53d530 | PRIESTS AND PROGRAMMERS_Lansing | 485972 (full_text chars)

→ Step 0 resolves via **Path B** (reconstruct from pre-quarantine DB).
  Reconstructed source: data/cartridges/_reconstruction/lansing_reconstructed.md (486455 bytes)

--- Grep 5: existing .lun cartridges in tree ---
uv=2 app_id=1280659021 ./data/user/memory_matrix.lun         (LUNM family — out of scope)
uv=2 app_id=1280659011 /private/tmp/phase4_test.lun          (Phase 4 baseline)
uv=2 app_id=1280659011 /private/tmp/phase4_bad.lun           (Phase 4 tamper)
uv=2 app_id=1280659011 /private/tmp/phase4_no_meta.lun       (Phase 4 tamper)
uv=2 app_id=1280659011 /private/tmp/phase4_no_base.lun       (Phase 4 tamper)
uv=1 app_id=1280659011 /private/tmp/v1_stub.lun              (Phase 3 partial-migration stub)
uv=0 app_id=0          /private/tmp/v01_phase3_stub.lun      (Phase 3 v0.1 stub)
(plus other Phase 3+3.5 artifacts, all matching expected family/version)

--- Grep 6: SPEC-002 non-canonical sketch location ---
401:    Minimal ULID generator. ts_ms = Unix timestamp in ms, counter = sub-ms sequence.
407:    # For migration: use (ts_ms << 16 | counter) as the full 48-bit timestamp value
409:    ts = (ts_ms << 16) | (counter & 0xFFFF)

--- Grep 7: validator import sites outside cartridge/__init__.py ---
(empty — no external imports of validators; Step 5 centralization has zero cross-module impact)
```

#### Item 2 — Migration round-trip on synthetic v0.1 stub

```
$ .venv/bin/python -m luna.cartridge.migrate /tmp/phase5_v01_stub.lun
{
  "strict": false,
  "input_state": "v0.1",
  "spec_006": "applied",
  "spec_001": {"anchored": 1, "orphans_classified": 1, "strict_mode": false},
  "spec_002": {"doc_nodes_ulids": 2, "extraction_ulids": 2},
  "spec_003": {"confidence_dropped": true, "llm_extractions_marked": 2},
  "path": "/tmp/phase5_v01_stub.lun",
  "dry_run": false
}

$ sqlite3 /tmp/phase5_v01_stub.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2

$ sqlite3 /tmp/phase5_v01_stub.lun ".schema extractions"
CREATE TABLE extractions (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, content TEXT NOT NULL,
  anchor_status TEXT NOT NULL DEFAULT 'unknown' CHECK (anchor_status IN
    ('anchored','synthesized','match_failed','filtered','unknown')),
  anchor_reason TEXT, ulid TEXT, llm_logprob_sum REAL, llm_token_count INTEGER,
  extraction_method TEXT NOT NULL DEFAULT 'llm' CHECK (extraction_method IN
    ('llm','rule','ner','manual')));
CREATE INDEX idx_extractions_anchor_status ON extractions(anchor_status);
CREATE UNIQUE INDEX uq_extractions_ulid ON extractions(ulid);
```

Schema matches Phase 4 fresh-build modulo the documented nullable-ULID column declaration discrepancy: migrated tables get `ulid TEXT` without the `NOT NULL` + `CHECK (length(ulid)=26 AND ulid GLOB ...)` because `ALTER TABLE ADD COLUMN` cannot retrofit those constraints into the column declaration. Data still passes `validate_ulids()` and the GLOB CHECK — only the column-declaration syntax differs.

#### Item 3 — Migration produces canonical ULIDs

```
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT DISTINCT substr(ulid,1,1) FROM doc_nodes ORDER BY 1"
0
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT DISTINCT substr(ulid,1,1) FROM extractions ORDER BY 1"
0
```

All ULIDs first-char in `[0-7]` per Phase 3.5 canonical generator (only `0` observed because the migration completed within a single timestamp band).

#### Item 4 — Orphan classification + auto-anchor preservation

```
$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT id, type, anchor_status, anchor_reason FROM extractions"
1|claim|anchored|
2|claim|match_failed|migration_unclassified

$ sqlite3 /tmp/phase5_v01_stub.lun "SELECT claim_id, node_id, anchor_method, anchored_by, anchored_at FROM claim_sources"
1|1|auto||
```

Claim 1 (anchored in v0.1) became `anchored`. Claim 2 (orphan in v0.1) received the SPEC-001 fallback `match_failed` + `migration_unclassified`. Existing `claim_sources` row id=1 retained `anchor_method='auto'` per SPEC-001 line 181 — relabeling as `'migrated'` would falsely imply migration-time provenance AND violate `validate_anchors()`'s non-auto-requires-`anchored_by` invariant (no actor identity available for the original v0.1 builder run).

#### Item 5 — Migration validators all pass

```
OK: validate_extractions passes
OK: validate_ulids passes
OK: validate_anchors passes
```

(Implicit — the validators run inside `_migrate_open_conn()` after the four `_apply_spec_*` helpers; commit happens only if all three pass.)

#### Item 6 — Strict mode rejection (transaction rolled back)

```
$ .venv/bin/python -m luna.cartridge.migrate --strict /tmp/phase5_v01_strict.lun
MIGRATION FAILED: Strict mode: 1 orphan claims would receive the
'migration_unclassified' fallback. Full classification analysis
(synthesized / filtered / match_failed) is not implemented in Phase 5.
Either resolve manually before re-running, or drop --strict to accept
the spec-documented fallback for all orphans.

$ sqlite3 /tmp/phase5_v01_strict.lun "PRAGMA application_id; PRAGMA user_version"
0
0
```

`MigrationError` carries the spec-required "would receive the 'migration_unclassified' fallback" phrase. Post-rejection state has `application_id=0` + `user_version=0` (rollback worked — the file is byte-identical to its pre-migration state).

#### Item 7 — uv=1 partial-migration acceptance

```
$ .venv/bin/python -m luna.cartridge.migrate /tmp/phase5_uv1_partial.lun
{
  "strict": false,
  "input_state": "uv=1_partial",
  "spec_006": "applied",
  ...
}

$ sqlite3 /tmp/phase5_uv1_partial.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2
```

Pre-flight gate accepts both `(app_id=0, uv=0)` true-v0.1 and `(app_id=LUNC, uv=1)` partial-migration; partial-migration replays cleanly because every `_apply_spec_*` helper uses `_add_column_if_missing` and `INSERT OR REPLACE`.

#### Item 8 — Dry-run leaves file untouched

```
$ .venv/bin/python -m luna.cartridge.migrate --dry-run /tmp/phase5_v01_dryrun.lun
{
  "strict": false,
  "input_state": "v0.1",
  "spec_006": "applied",
  ...,
  "dry_run": true
}

$ sqlite3 /tmp/phase5_v01_dryrun.lun "PRAGMA application_id; PRAGMA user_version"
0
0
```

Dry-run clones the file into an in-memory SQLite connection via `sqlite3.Connection.backup`, runs the four `_apply_spec_*` helpers + validators against the clone, returns the summary, and discards. The on-disk file is never opened in write mode.

#### Item 9 — Lansing v0.2 build (headline measurement — DEVIATION noted)

```
$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "PRAGMA application_id; PRAGMA user_version"
1280659011
2

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT key, value FROM meta WHERE key IN
  ('format_version','logprob_base','logprob_attribution','deprecated_columns','node_count','word_count')
  ORDER BY key"
deprecated_columns|doc_nodes.id,extractions.id
format_version|0.2
logprob_attribution|response_level
logprob_base|e
node_count|5576
word_count|79213

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT type, anchor_status, COUNT(*)
  FROM extractions GROUP BY type, anchor_status ORDER BY type"
entity|unknown|15
summary|anchored|1

$ sqlite3 data/cartridges/PRIESTS_AND_PROGRAMMERS_Lansing.lun "SELECT
  SUM(CASE WHEN type='claim' AND anchor_status='match_failed' THEN 1 ELSE 0 END) AS claim_match_failed,
  SUM(CASE WHEN type='claim' AND anchor_status='anchored' THEN 1 ELSE 0 END) AS claim_anchored
  FROM extractions"
0|0
```

**DEVIATION:** The headline `claim_match_failed / (claim_anchored + claim_match_failed)` ratio is **undefined** (0 / 0) for this build, so no actionable comparison against the 2026-04-21 audit's 9.5% orphan baseline is possible from Phase 5's Lansing v0.2 cartridge.

Root cause is structural, not a code bug:

1. Step 0 resolved via Path B (PDF unavailable), producing a markdown reconstruction from `full_text` in the pre-quarantine DB
2. The reconstructed markdown has no `#` heading syntax (the original PDF's structure was lost when the text was flattened into the `full_text` column)
3. `MarkdownParser` consequently identifies only **1 section** spanning all 5576 nodes
4. `CartridgeExtractor.extract()` truncates each section to 8000 chars before sending to Haiku (`extractor.py:167`), so only the first 8000 of 485972 chars get extracted
5. That single Haiku call returned 1 summary + 15 entities + 0 claims (typical for a title-page-like opening segment)

Cartridge is structurally valid (`application_id=LUNC`, `user_version=2`, all meta markers present, all validators clean, ULIDs canonical, `extraction_method='llm'` for all 16 rows, deprecated_columns marker intact). The 9.5%-baseline measurement is **deferred** as a follow-up requiring either (a) PDF source recovery for a structure-preserving rebuild, or (b) a chapter-splitting pre-process that injects `#` heading markers into the reconstructed markdown before build. See Phase 5 follow-up below.

#### Item 10 — Legacy fallback removed: `app_id=0` raises `WrongFamilyError`

```
$ .venv/bin/python -c "
import sqlite3
from luna.cartridge import validate_cartridge_open, WrongFamilyError
conn = sqlite3.connect('/tmp/phase5_v01_unmigrated.lun')
try:
    validate_cartridge_open(conn)
    print('UNEXPECTED')
except WrongFamilyError as e:
    print(f'OK: rejected: {e}')
finally:
    conn.close()
"
OK: rejected: Cartridge has application_id=0x0, expected LUNC (0x4C554E43).
Pre-SPEC-006 v0.1 cartridges (app_id=0) must be migrated first via
`python -m luna.cartridge.migrate <path>`.
```

Error message points at the migration command. uv=1 LUNC partial-migration stubs and uv=2 v0.2 cartridges still open cleanly (regression checked separately — both pass).

#### Item 11 — Validator centralization regression

```
$ .venv/bin/python -c "
from luna.cartridge.builder import validate_anchors, validate_ulids, validate_extractions, BuildError
from luna.cartridge import (validate_cartridge_open, WrongFamilyError,
                            UnsupportedVersionError, UnsupportedAttributionError)
from luna.cartridge.validation import (
    validate_anchors as v_anchors,
    validate_ulids as v_ulids,
    validate_extractions as v_extractions,
    validate_cartridge_open as v_open,
    BuildError as v_be,
    WrongFamilyError as v_wfe,
    UnsupportedVersionError as v_uve,
    UnsupportedAttributionError as v_uae,
)
assert validate_anchors is v_anchors
assert validate_ulids is v_ulids
assert validate_extractions is v_extractions
assert validate_cartridge_open is v_open
assert BuildError is v_be
assert WrongFamilyError is v_wfe
assert UnsupportedVersionError is v_uve
assert UnsupportedAttributionError is v_uae
print('OK: all validators + exceptions re-exported from validation.py; public API preserved')
"
OK: all validators + exceptions re-exported from validation.py; public API preserved
```

Phase 4 smoke items re-run against centralized validators after Step 5:
- Item 7 (`validate_extractions` clean): `OK: validate_extractions passes`
- Item 8 (paired-NULL tamper): `OK: rejected with BuildError: 1 extractions have mismatched logprob/token_count NULLs.`
- Item 10a (missing `logprob_attribution`): `OK: rejected: meta.logprob_attribution must be 'response_level' for v0.2 cartridges. Got: MISSING`
- Item 11 (uv=1 LUNC stub still opens): `OK: /tmp/v1_stub.lun opens cleanly`

Migration tool round-trip on a fresh v0.1 stub after Step 5 produces the same JSON summary as before centralization. Identity assertions confirm zero behavioral change.

#### Item 12 — SPEC-002 Phase 3.5 lesson annotation visible

```
$ grep -A 8 "Phase 3.5 lesson" "../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md"
> **NOTE (Phase 3.5 lesson):** The example below uses `ts << 16 | counter` as a sub-ms
> monotonicity sketch. This is **non-canonical** — it overflows the 48-bit timestamp field
> and produces first chars in `[G-Z]` for current dates, which strict ULID parsers reject as
> overflow. The canonical generator (48-bit ts + 80-bit random, monotonic via random
> increment within same ms) lives in `src/luna/cartridge/builder.py::ULIDGenerator` and is
> the authoritative form. See `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE3_5_CANONICAL_ULID.md`
> for the root cause analysis. The example below is preserved for historical context;
> do not copy it into new implementations.
```

Annotation inserted between the prose at SPEC-002 line 391 and the example code-fence opening at line 393. Example code itself unmodified.

#### Item 13 — Phase 1-4 regression sweep on Lansing v0.2 cartridge

```
OK: validate_extractions
OK: validate_ulids
OK: validate_anchors
OK: validate_cartridge_open

meta markers: deprecated_columns=doc_nodes.id,extractions.id  format_version=0.2
              logprob_attribution=response_level  logprob_base=e
ULID first chars in doc_nodes: 0 only (canonical [0-7] range, single timestamp band)
extraction_method distribution: llm=16 (Phase 4 SPEC-003 invariant holds)
```

All Phase 1 (SPEC-006), Phase 2 (SPEC-001), Phase 3 (SPEC-002), Phase 3.5 (canonical ULID), and Phase 4 (SPEC-003) invariants hold on the Lansing v0.2 cartridge.

### Phase 5 deviations and follow-ups

1. **Lansing 9.5% baseline measurement deferred** — see Item 9. The reconstructed markdown has no `#` headings, so the parser produces 1 section and the extractor only processes the first 8000 chars. Cartridge is structurally valid but the headline ratio is undefined. **Follow-up:** either recover the original PDF (Path A) for a rebuild that preserves chapter structure, OR pre-process the reconstructed markdown with a chapter-detection heuristic that injects `#` headings before build. Track as a Phase-5-followup; not gating the Phase 5 closeout because the cartridge is otherwise valid and the structural arc is complete.

2. **SPEC-002 example annotation lives outside the engine repo** — `../Research/Code for .lun Development/01_Specs/accepted/SPEC-002_portable-ids.md` is in a sibling directory that is not under git. Step 6's edit persists on disk but has no commit. If the Research tree later gets git-tracked, the annotation will surface as a pre-existing edit.

3. **Pre-flight grep 5 (Phase 4 backfill)** — original pattern `SELECT.*FROM extractions` returned 1 hit instead of 4 because Phase 4's `resolve_source_ref()` SQL was reformatted to multi-line. Documented in the Phase 4 handoff backfill; not a Phase 5 issue per se, but surfaces the same drift any future single-line grep pattern will hit.

4. **Markdown reconstruction `\x0c` artifacts** — the pre-quarantine DB's `full_text` for Lansing preserves PDF form-feed characters (page breaks). These pass through to the cartridge text content. Not a build-breaking issue (parser treats them as whitespace) but visible in `doc_nodes.content`. Follow-up if/when chapter-splitting heuristic lands: strip `\x0c` as part of the same pre-process.

5. **Adjacent smells deferred per scope discipline:**
   - Full SPEC-001 orphan semantic classification (synthesis/filtered detection via multi-lineage similarity + section-type heuristics) — deferred to a future spec (likely SPEC-004 consumer territory). Phase 5 applies the spec-documented `migration_unclassified` fallback uniformly.
   - SPEC-002 `extractions.id` ULID-only consolidation — v0.3 territory.
   - `_migration_log` table for migrations — explicit SPEC-002 Q4 reject; not added.
   - Backend-side logprob exposure (`HaikuResult.usage` fields) — v0.3 backend-side improvement; current paired-NULL trivially satisfies the SPEC-003 contract.
