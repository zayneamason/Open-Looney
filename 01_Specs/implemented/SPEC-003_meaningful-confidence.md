# SPEC-003: Replace Hardcoded Confidence with Raw Signals

**Status:** implemented
**Severity:** medium
**Author:** Ahab (with Claude)
**Created:** 2026-05-10
**Last updated:** 2026-05-21 (Phase 5 shipped; moved to implemented/)
**Affects format version:** v0.2

---

## Problem statement

The `extractions.confidence` column exists, has a `REAL DEFAULT 1.0` type, and is queryable as if it carries information. It does not. Every row in the audited cartridge has `confidence = 0.85` or `confidence = 0.9` — two constants set unconditionally by the builder based on extraction type. The column is theater: it advertises a signal that doesn't exist.

This violates working principle #5 ("Separate data from interpretation. Raw signals in the file, scoring algorithms in code"). A scalar `confidence` is interpretation by definition — it combines multiple potential signals (model uncertainty, source quality, anchor strength) into one number, but the current builder collapses everything to a hardcoded constant before any combination happens. There's no data; there's just a number that lies.

The correct fix is structural: drop the misleading column, capture the raw signals the builder can actually access, distinguish LLM-derived extractions from rule-based ones, and let future spec work (SPEC-004 multi-axis weights) compose real signals into trust scores.

## Observed evidence

From `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`, Finding E-01:

> All confidence values are 0.85 or 0.9 constants. Specifically: claims and entities get `confidence = 0.85`; summaries get `confidence = 0.9`. The column has full column statistics: 2 distinct values across 4,124 rows. No row carries any signal that the builder didn't hardcode.

Looking at the builder source (`src/luna/cartridge/extractor.py`):

```python
# Approximated from observed behavior
def _build_claim_extraction(text: str) -> dict:
    return {"type": "claim", "content": text, "confidence": 0.85}

def _build_summary_extraction(text: str) -> dict:
    return {"type": "summary", "content": text, "confidence": 0.9}
```

The constants are not derived from any property of the extraction. They reflect a stale design intent (presumably: "summaries are slightly more trustworthy than claims because they're broader") rather than a measurement.

The current cartridge reader explicitly selects this column. In `src/luna/cartridge/__init__.py`, the `resolve_source_ref()` query includes `e.confidence` in its column list. Any v0.2 migration that drops `confidence` must atomically patch this reader; readers that select a dropped column raise `OperationalError` at query time.

## Root cause analysis

Three converging causes:

1. **The `confidence` column was added before raw signals were available.** v0.1's builder calls the LLM with a simple prompt and parses the JSON response. The Anthropic SDK at the time did not consistently expose token-level logprobs, so the builder had nothing real to write. A constant was chosen instead of a NULL or an absent column.

2. **Single-scalar trust is the wrong shape for the problem.** Even with a real signal, collapsing it into one number to be interpreted as "how much should I trust this" mixes orthogonal dimensions (model uncertainty, source authority, anchor strength, age) into a value that can't be decomposed. SPEC-004 (multi-axis imprint weights) will eventually require these dimensions to be separable.

3. **No spec required the column to mean anything.** v0.1 was a single-author project; the column's semantics were defined by whatever the builder wrote, which was never formally specified.

## Proposed solution

**Drop the misleading column. Add real raw signals where they exist. Distinguish extraction sources. Document the attribution method explicitly. Document that composite trust scores belong in code, not in the file.**

### Schema changes

```sql
-- Drop the misleading column.
ALTER TABLE extractions DROP COLUMN confidence;

-- Add real raw signals.
ALTER TABLE extractions ADD COLUMN llm_logprob_sum REAL;
ALTER TABLE extractions ADD COLUMN llm_token_count INTEGER;
ALTER TABLE extractions ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'llm'
    CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual'));

-- Document the convention machine-readably.
INSERT OR REPLACE INTO meta (key, value) VALUES ('logprob_base', 'e');
INSERT OR REPLACE INTO meta (key, value) VALUES ('logprob_attribution', 'response_level');
```

Column semantics:

- **`llm_logprob_sum REAL`** — sum of natural-log token probabilities for the LLM call that produced this extraction. Range `(-∞, 0]`. A value of `0.0` means the model assigned `p = 1.0` (effectively impossible for non-trivial output); a value of `-2.3` means joint `p ≈ 0.1`. NULL means "no LLM logprob signal for this row" — either the API didn't return logprobs, or the extraction came from a non-LLM path. Convention is natural log (base `e`), builder-enforced; readers MUST NOT guess or convert. **Attribution is response-level**: all extractions produced by a single LLM call share the same value (see `meta.logprob_attribution` below).

- **`llm_token_count INTEGER`** — number of content tokens covered by `llm_logprob_sum`. For response-level attribution, this is the total token count for the LLM response. Paired with `llm_logprob_sum`: both NULL or both populated (invariant enforced in `validate_extractions()`). Lets readers compute mean logprob (`sum / count`) for length-normalized scoring without storing the mean directly.

- **`extraction_method TEXT`** — how this row was produced. Values: `'llm'` (LLM extraction, the v0.1 default and the only method currently in use), `'ner'` (named-entity recognition from a non-LLM model), `'rule'` (regex or rule-based extraction), `'manual'` (operator-supplied, e.g. ambassador annotation in a future spec). Strict CHECK; new methods must be added by spec amendment, not by ad-hoc insertion.

- **`meta.logprob_base = 'e'`** — documents the log base machine-readably. Readers refuse to proceed if missing or set to anything other than `'e'`.

- **`meta.logprob_attribution = 'response_level'`** — documents how per-row logprob values were computed. Current v0.2 value is `'response_level'`: one LLM call's logprob sum and token count are copied to every extraction produced by that call. Future values might include `'token_span'` (per-extraction attribution via token-span mapping; see Open Question 5 resolution and "Future work" below) or `'claim_level'` (one LLM call per extraction). Readers refuse to proceed if missing or if the value is not in the set they support.

The resulting `extractions` table (combined with SPEC-001 and SPEC-002 additions, for the complete v0.2 picture):

```sql
CREATE TABLE extractions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,  -- stays through v0.2 (SPEC-002 phase 1)
    type              TEXT NOT NULL,
    content           TEXT NOT NULL,
    anchor_status     TEXT CHECK (anchor_status IN (     -- SPEC-001
                          'anchored', 'synthesized', 'match_failed', 'filtered', 'unknown'
                      )) DEFAULT 'unknown',
    anchor_reason     TEXT,                              -- SPEC-001
    ulid              TEXT NOT NULL,                     -- SPEC-002
    llm_logprob_sum   REAL,                              -- SPEC-003 (this spec)
    llm_token_count   INTEGER,                           -- SPEC-003 (this spec)
    extraction_method TEXT NOT NULL DEFAULT 'llm'        -- SPEC-003 (this spec)
                      CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual')),
    CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*')
);
```

No `confidence` column. The hardcoded scalar is gone.

### Behavioral changes

Builder (`src/luna/cartridge/extractor.py`):

1. Stop writing `confidence`. The column no longer exists.
2. When calling the LLM, request logprobs at the response level. Capture the **natural log** of token probabilities for the full completion; sum them, count them, store the pair. The conversion happens at the API boundary if the SDK returns a different base; the value stored in the cartridge is always `ln`.
3. **Response-level attribution.** After parsing the JSON response (one call yields summary + multiple claims + multiple entities for a section), copy the call's `llm_logprob_sum` and `llm_token_count` to every extraction row produced by that call. All extractions from the same call share identical values. This is a deliberate property of the v0.2 contract — the signal is per-call uncertainty, not per-extraction discrimination.
4. If logprobs are unavailable (older SDK, non-supporting model): leave both `llm_logprob_sum` and `llm_token_count` NULL. The paired-NULL invariant is enforced in `validate_extractions()`. Never fabricate.
5. Set `extraction_method = 'llm'` for all current builder paths. Other values come from future builders (NER, rule-based) that don't exist yet in v0.2.
6. Set `meta.logprob_base = 'e'` and `meta.logprob_attribution = 'response_level'` once per cartridge at build finalization.

Reader (`src/luna/cartridge/__init__.py`):

1. **Patch required atomically with migration:** the SELECT in `resolve_source_ref()` currently includes `e.confidence`. Remove that column from the SELECT and from any downstream consumer of the row dict. This is a hard break — no shim. Readers that haven't been patched will raise `sqlite3.OperationalError: no such column: e.confidence` when they hit a v0.2 cartridge.
2. **Interpret `llm_logprob_sum` as call-level uncertainty, not per-claim discrimination.** Multiple extractions from the same section will have identical logprob values; this reflects that they share a single LLM call's output. Treating them as independent per-extraction signals would be statistically wrong. Readers that want per-extraction granularity must wait for a future spec (see Future work below).
3. Any code that previously derived "trust" from `confidence` migrates to read `anchor_status` (categorical signal from SPEC-001), `extraction_method` (provenance signal), and `llm_logprob_sum` / `llm_token_count` (LLM uncertainty signal at call scope). The composition algorithm — how these combine into a UI-displayable trust indicator — lives in code, not in the cartridge. SPEC-004 formalizes this; in the interim, any composition is application-level and clearly labeled as such.
4. Reader verifies `meta.logprob_base = 'e'` and `meta.logprob_attribution = 'response_level'` on cartridge open. Mismatch is a "refuse to open" condition for v0.2 readers.

### Migration path

The v0.1 → v0.2 migration step for SPEC-003 runs inside the same transaction as SPEC-001 and SPEC-002 migrations:

```sql
-- Drop the constant column. Information loss is zero — every value was 0.85 or 0.9.
ALTER TABLE extractions DROP COLUMN confidence;

-- Add real signal columns.
ALTER TABLE extractions ADD COLUMN llm_logprob_sum REAL;
ALTER TABLE extractions ADD COLUMN llm_token_count INTEGER;
ALTER TABLE extractions ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'llm'
    CHECK (extraction_method IN ('llm', 'rule', 'ner', 'manual'));

-- Existing extractions are all LLM-derived (v0.1 only has the LLM path). Default 'llm' is correct.
-- llm_logprob_sum and llm_token_count remain NULL — we don't have the data and won't fabricate.

-- Document the convention.
INSERT OR REPLACE INTO meta (key, value) VALUES ('logprob_base', 'e');
INSERT OR REPLACE INTO meta (key, value) VALUES ('logprob_attribution', 'response_level');
```

Existing cartridges (currently just `PRIESTS_AND_PROGRAMMERS_Lansing.lun`) are rebuilt from source with the v0.2 builder, which captures response-level logprobs natively at extraction time. The rebuilt cartridge will have populated `llm_logprob_sum` and `llm_token_count` (shared across each section's extraction batch) where the API supplied them.

**The reader patch is non-optional and must ship atomically with the migration tool.** `src/luna/cartridge/__init__.py` currently selects `e.confidence`; after the migration, that SELECT raises. The same PR that lands the migration must remove the column from the reader's SELECT statement and any downstream consumers. A migration that ships without the reader patch breaks every read against a migrated cartridge.

`memory_matrix.lun` is unaffected — it has its own schema and doesn't carry `extractions.confidence`. No matrix migration needed for this spec.

### Migration mechanics (SQLite-specific)

Cross-reference: `05_Reference/SQLite_Research.md`, Topic 5.

`ALTER TABLE ... DROP COLUMN` was added in SQLite 3.35.0 (2021-03). The codex 2026-05-10 deep dive confirmed the runtime uses SQLite 3.53.0. Pre-3.35 environments are not in scope.

`DROP COLUMN` semantics:
- The column is removed from the schema text
- Existing row data for the dropped column is reclaimed on next `VACUUM` (the v0.2 finalize pragma stack from SPEC-006 includes `VACUUM`, so this happens automatically)
- Indexes that reference the dropped column are automatically removed
- `extractions.confidence` has no index in v0.1, so no index cleanup is needed

The three `ADD COLUMN` operations are all O(1) under SQLite 3.37+:
- `llm_logprob_sum REAL` — nullable, no constraints
- `llm_token_count INTEGER` — nullable, no constraints
- `extraction_method TEXT NOT NULL DEFAULT 'llm' CHECK in (...)` — DEFAULT `'llm'` satisfies the CHECK for all existing rows. Same pattern as SPEC-001's `anchor_method` migration

The paired-NULL invariant (`llm_logprob_sum IS NULL` iff `llm_token_count IS NULL`) cannot be expressed as a column-level CHECK because it references multiple columns. Table-level CHECK constraints can be added via `ALTER TABLE ADD CONSTRAINT` (3.47+) but the runtime version doesn't require that path. The invariant is enforced in `validate_extractions()` at build time, matching the precedent set by SPEC-001's `validate_anchors()` for cross-column invariants.

## Validation rules

Build time, runs in `validate_extractions()` before cartridge finalization:

```python
def validate_extractions(conn):
    """Sanity checks on extraction signals (SPEC-003)."""

    # No confidence column should exist post-migration
    cols = [row[1] for row in conn.execute("PRAGMA table_info(extractions)").fetchall()]
    if "confidence" in cols:
        raise BuildError(
            "extractions.confidence still exists. SPEC-003 migration did not complete."
        )

    # llm_logprob_sum, when present, must be ≤ 0 (it's a natural log of a probability)
    bad_logprob = conn.execute("""
        SELECT id, llm_logprob_sum FROM extractions
        WHERE llm_logprob_sum IS NOT NULL
          AND (llm_logprob_sum > 0.0 OR llm_logprob_sum < -1000.0)
    """).fetchall()
    if bad_logprob:
        raise BuildError(
            f"{len(bad_logprob)} extractions have llm_logprob_sum outside (-1000, 0]. "
            f"Natural log of a probability must be ≤ 0. A value below -1000 suggests "
            f"a unit error (log10 vs ln) or extreme content length."
        )

    # llm_token_count, when present, must be a positive integer
    bad_tokens = conn.execute("""
        SELECT id, llm_token_count FROM extractions
        WHERE llm_token_count IS NOT NULL AND llm_token_count <= 0
    """).fetchall()
    if bad_tokens:
        raise BuildError(
            f"{len(bad_tokens)} extractions have non-positive llm_token_count."
        )

    # Paired-NULL invariant: llm_logprob_sum and llm_token_count are both NULL or both populated
    mismatched = conn.execute("""
        SELECT id FROM extractions
        WHERE (llm_logprob_sum IS NULL) != (llm_token_count IS NULL)
    """).fetchall()
    if mismatched:
        raise BuildError(
            f"{len(mismatched)} extractions have mismatched logprob/token_count NULLs. "
            f"Both must be populated together or both NULL."
        )

    # extraction_method enforced by CHECK; this is a belt-and-suspenders read-time guard
    bad_method = conn.execute("""
        SELECT id, extraction_method FROM extractions
        WHERE extraction_method NOT IN ('llm', 'rule', 'ner', 'manual')
    """).fetchall()
    if bad_method:
        raise BuildError(f"{len(bad_method)} extractions have invalid extraction_method values.")

    # logprob_base meta marker present and correct
    base = conn.execute(
        "SELECT value FROM meta WHERE key = 'logprob_base'"
    ).fetchone()
    if not base or base[0] != 'e':
        raise BuildError(
            f"meta.logprob_base must be 'e' (natural log). Got: {base[0] if base else 'MISSING'}"
        )

    # logprob_attribution meta marker present and correct for v0.2
    attribution = conn.execute(
        "SELECT value FROM meta WHERE key = 'logprob_attribution'"
    ).fetchone()
    if not attribution or attribution[0] != 'response_level':
        raise BuildError(
            f"meta.logprob_attribution must be 'response_level' for v0.2. "
            f"Got: {attribution[0] if attribution else 'MISSING'}"
        )
```

Read time (in `lun fsck`):

- Verify `extractions.confidence` does not exist (post-v0.2)
- Verify `extractions.llm_logprob_sum`, `llm_token_count`, `extraction_method` exist with expected types
- Verify `meta.logprob_base = 'e'` and `meta.logprob_attribution = 'response_level'`
- Verify the paired-NULL invariant
- Report distribution stats: count of `extraction_method` values, NULL rate for `llm_logprob_sum`, percentile breakdown when populated. Also report the number of distinct `llm_logprob_sum` values relative to total row count — under response-level attribution, this should be roughly the section count, not the row count. A surprising ratio is a builder bug

## Governance implications

This spec is a precondition for SPEC-004 (multi-axis imprint weights). The multi-axis design requires *separable* raw signals — at minimum:

- **Anchor strength** (from SPEC-001 `anchor_status` + `claim_sources` presence)
- **Model uncertainty** (from SPEC-003 `llm_logprob_sum` and `llm_token_count`, at call scope per `meta.logprob_attribution`)
- **Source quality** (future; could be derived from `doc_nodes.type` — frontmatter vs. body)
- **Extraction provenance** (from SPEC-003 `extraction_method`, distinguishing LLM output from rule-based)

Without SPEC-003, the only "trust" signal in the file is a constant. SPEC-004 cannot meaningfully compose anything from a constant. After SPEC-003, SPEC-004 has at least one real continuous signal (at call scope), a categorical signal for extraction source, and explicit metadata about the attribution method so downstream composers can reason correctly about granularity.

Annotation events (SPEC-005) will eventually carry their own trust signals (ambassador confidence, ledger-event verification scores). The convention established here — raw signal in the file, composition in code, metadata documenting attribution — extends to those.

## Future work

This spec accepts response-level logprob attribution as the v0.2 contract. Two future enhancements are anticipated but explicitly out of scope here:

- **Token-span attribution (option `'token_span'`).** A future spec can introduce per-extraction logprob attribution by mapping each extraction's content tokens back to the LLM API's logprob span output. This is a builder change only; the schema is identical. The new spec writes `meta.logprob_attribution = 'token_span'` and the same columns now carry per-extraction values. Readers that support `'token_span'` get higher-fidelity per-row uncertainty; readers that only support `'response_level'` refuse to open the cartridge per the meta-marker validation. Backward-compatible at the file level, forward-compatible at the schema level.

- **`llm_call_id TEXT` column.** A future spec can add an explicit call grouping column so readers can identify which extractions came from the same LLM call without joining on doc_node + timestamp. Trivial schema addition (one nullable TEXT column with a ULID per LLM call). Especially useful if both `'response_level'` and `'token_span'` cartridges coexist in a deployment — the grouping is meaningful for `'response_level'` cartridges and informational for `'token_span'`. Deferred from this spec because it's not required for the v0.2 contract to be useful.

## Alternatives considered

**Alt 1: Keep `confidence`, redefine its semantics as "raw LLM logprob."**
Rejected. Redefines a column's meaning without renaming it; any v0.1 reader that interpreted the constant as a trust score gets silently broken when v0.2 cartridges carry negative real values in the same column. A rename eliminates this ambiguity by failing loud (column missing).

**Alt 2: Drop `confidence`, add NO replacement.**
Considered. Cleanest minimal change. Rejected because we're touching the schema anyway and capturing real signals costs almost nothing in marginal effort. Forward-investment for SPEC-004 with low cost.

**Alt 3: Drop `confidence`, add a multi-column raw-signal schema now.**
Rejected. Adds `anchor_score`, `source_quality_score`, `temporal_decay`, etc. directly. This is SPEC-004's job. Doing it in SPEC-003 conflates two distinct decisions (remove the lie vs. design the trust model) into one spec, and risks shipping the multi-axis design before it's been thought through. SPEC-003 stays small and removes the immediate problem while adding only the signals the builder can actually capture today.

**Alt 4: Keep `confidence` and add a `confidence_source` column documenting where the value came from.**
Rejected. Doesn't fix the problem — the value is still a constant 0.85 / 0.9. Adding metadata about its provenance doesn't make it informative.

**Alt 5: Store mean logprob instead of summed logprob.**
Rejected. Mean discards length, which matters for some trust computations. Storing `llm_logprob_sum` and `llm_token_count` preserves both views — readers can compute mean by dividing if they want it. Storing mean loses information.

**Alt 6: Use a single nullable `llm_logprob` column without `llm_token_count`.**
Rejected. Without token count, readers cannot reconstruct mean logprob or length-normalize for cross-extraction comparison. Two paired columns capture both raw and derivable signals; the marginal cost of one INTEGER column is negligible.

**Alt 7: Backwards-compatibility VIEW exposing a synthesized `confidence` value.**
Rejected. The column was carrying noise; any reader that depended on it was depending on noise. The v0.2 upgrade is the right moment to make this explicit. Production reader code in `src/luna/cartridge/__init__.py` is patched atomically with the migration (see Behavioral changes); no shim layer needed.

**Alt 8: Add an `llm_call_id` column to group extractions produced by the same LLM call.**
Considered. With response-level attribution, multiple rows share the same logprob values, and `llm_call_id` makes that grouping explicit and queryable. Deferred to a future spec rather than added here — the v0.2 contract is useful without it, and adding columns "just in case" is the wrong direction. Reconsider when the first downstream consumer actually needs explicit grouping (likely SPEC-004 or a token-span attribution spec).

**Alt 9: Claim-level LLM generation (one call per extraction) for true per-row logprobs.**
Rejected for v0.2. Highest fidelity per-row signal, but ~10× the API calls, ~10× the latency, ~10× the token spend. Disproportionate cost for a v0.2 milestone where the goal is to replace a lie with a measurement; refinement can come in a future spec if per-row granularity proves important.

## Open questions

None remaining. All five open questions resolved 2026-05-10:

1. **Logprob convention.** Natural log (`ln`), enforced in builder. Validator guard: `llm_logprob_sum ≤ 0`. Meta marker: `meta.logprob_base = 'e'`, refused if absent or mismatched on read.

2. **Token count alongside logprob.** Yes. Added `llm_token_count INTEGER`. Field renamed to `llm_logprob_sum` for explicitness. Paired-NULL invariant enforced in `validate_extractions()`.

3. **Non-LLM extractions.** Added `extraction_method TEXT NOT NULL DEFAULT 'llm' CHECK in ('llm', 'rule', 'ner', 'manual')`. Removes the ambiguity between "no logprob from LLM" and "not an LLM extraction at all".

4. **Backwards-compatibility for readers expecting `confidence`.** Hard break. Atomic reader patch required: `src/luna/cartridge/__init__.py` updated to drop `e.confidence` from the SELECT in `resolve_source_ref()`. No shim.

5. **Per-row logprob attribution from batch JSON extraction.** Response-level attribution (option `(a)`). One LLM call's `llm_logprob_sum` and `llm_token_count` are copied to every extraction produced by that call. Documented machine-readably via `meta.logprob_attribution = 'response_level'`, refused on read if absent or unsupported. Readers interpret this as call-level uncertainty, not per-claim discrimination. Token-span attribution (option `(c)`) and explicit `llm_call_id` grouping are queued as future work (see Future work section); not blockers for v0.2.

## Dependencies

- **SPEC-006 (accepted)** — establishes the v0.2 version-tracking and migration framework that SPEC-003 plugs into. SPEC-003's migration is one transaction step in the same v1-to-v2 migration tool that SPEC-001 and SPEC-002 share.

Blocks:
- **SPEC-004 (implemented, 2026-05-22)** — multi-axis imprint weights. SPEC-004 reads `llm_logprob_sum`, `extraction_method`, and (in combination with SPEC-001) `anchor_status` as input signals. Without SPEC-003, SPEC-004 has no real model-uncertainty signal to compose.

Does not block, but interacts with:
- **SPEC-001 (accepted)** — `anchor_status` is another raw signal that SPEC-004 will compose with the SPEC-003 signals. SPEC-003's design treats these as orthogonal axes, not substitutes.
- **SPEC-002 (accepted)** — independent. ULID identity and logprob signal don't interact.

Future:
- **Token-span attribution spec** — refines per-row logprob attribution from `'response_level'` to `'token_span'`. Builder change only; schema unchanged. Bumps `meta.logprob_attribution`. See Future work.
- **`llm_call_id` spec** — adds explicit call grouping column when the first downstream consumer needs it.

## Implementation notes

*(Filled in when status moves to `implemented`)*

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:

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
