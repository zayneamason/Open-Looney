# HANDOFF: SPEC-005 engine implementation — annotation ledger + payload schemas

**Date:** 2026-05-22
**From:** Ahab (with Claude, research-repo session)
**To:** Claude Code (engine-repo session)
**Purpose:** Translate accepted SPEC-005 (annotation ledger) + SPEC-005_payload-schemas into a phased engine-side implementation against `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`. Produces v0.3 cartridges (`user_version = 3`) with append-only ledger, hash chain, genesis row, per-event-type payload validation, and `lun fsck` flags.
**Refresh note:** Updated 2026-05-22 after `LUN-FORMAT_v0.3.md` became the active implementation target. Q1 and Q2 are resolved; Q3 migration step order remains the only format-spec blocker.

---

## Overview

Four phases, dependency-ordered. Each is a single session of work or less. Stop at the end of any phase if blocked; do not skip ahead. This handoff is **engine-repo work only** — the research repo at `/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/` is read-only during this work.

| Phase | Subject | Scale | Output |
|---|---|---|---|
| 1 | Schema + triggers + `ledger.py` core | ~1 session | `schema.py` additions; new `ledger.py` |
| 2 | `payload_schemas.py` + per-event validators + genesis payload | ~1 session | New `payload_schemas.py` |
| 3 | Integration: builder, `resolve_source_ref`, migrate v2→v3 | ~1 session | Patches to `builder.py`, `__init__.py`, `migrate.py` |
| 4 | Validators + `lun fsck` flags + integration tests | ~1 session | `validation.py` additions; CLI flags; tests; Meditations v0.3 rebuild |

The handoff is structured to mirror `02_Handoffs/HANDOFF_2026-05-22_post-reader-v02-roadmap.md` — the engine implementer should recognize the form. `03_Format_Spec/LUN-FORMAT_v0.3.md` is now active and is the implementation target. SPEC-004 implementation and SPEC-001/003/004 cross-reference updates are explicitly OUT of scope here.

### Locked v0.3 decisions

- **Q1 — FTS5 strategy:** use Strategy A. Keep `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` only as FTS5's `content_rowid`; `doc_nodes.ulid` is canonical application identity.
- **Q2 — table rename:** use the symmetric rename. v0.3 uses `extraction_sources` and `extraction_context_nodes`; do not add v0.3 compatibility aliases for `claim_sources` or `claim_context_nodes`.
- **Q3 — migration order:** still open. Engine implementation must validate whether the draft order in `LUN-FORMAT_v0.3.md` works and report the result before the format spec can promote from `active` to `accepted`.

---

## Required reading

Order matters. Read items 1–3 in full before Phase 1; items 4–9 are reference material to consult as the phases call for them; items 10–11 are structural / verification context.

1. **`01_Specs/implemented/SPEC-005_annotation-ledger.md`** — primary spec, 962 lines. Full read. The phases below cite line ranges from this doc.
2. **`01_Specs/implemented/SPEC-005_payload-schemas.md`** — companion spec, 585 lines. Full read. Phase 2 + Phase 4 cite line ranges from this doc.
3. **`03_Format_Spec/LUN-FORMAT_v0.3.md`** — active implementation target. Full read. Q1/Q2 are resolved; Q3 is the live engine-validation gate.
4. `02_Handoffs/HANDOFF_2026-05-22_v03-fts5-strategy-prototype.md` and engine report `Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md` — Q1 evidence for Strategy A.
5. `08_Journal/2026-05-22.md` — read the continuation sections for Q1 and Q2 resolution context.
6. `01_Specs/implemented/SPEC-001_orphan-claims.md` — § "Ambassador upgrade flow" is the canonical first consumer of `insert_ledger_event()`. In v0.3, the flow turns into an `extraction_sources` row + `extractions` UPDATE plus a ledger event row.
7. `01_Specs/implemented/SPEC-002_portable-ids.md` — § "Phase 3 — additive ULID columns". The ULID generator (`ULIDGenerator` in builder.py, hardened in Phase 3.5) is already in the engine repo; reuse it for `annotation_ledger.ulid` and `annotation_actors.actor_id`. Do not re-implement.
8. `01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md` — § "Schema changes" + § "Finalize stack". v0.3 needs `PRAGMA user_version = 3` and `meta.format_version = '0.3'`; the finalize pragma stack from SPEC-006 (`optimize → wal_checkpoint(TRUNCATE) → journal_mode=DELETE → VACUUM`) carries forward unchanged.
9. `05_Reference/SQLite_Research.md` Topic 3 (lines 25–105) — hash-chain prior art (Fossil's manifest chain); soft-covenant trigger pattern that SPEC-005's append-only triggers implement.
10. `04_Audits/AUDIT_2026-05-22_meditations-v02.md` — reference v0.2 cartridge baseline. After Phase 4, rebuild Meditations as v0.3 and run a parity check against this audit's per-table counts.
11. `02_Handoffs/HANDOFF_2026-05-22_post-reader-v02-roadmap.md` — structural template for this handoff. Per-phase format, "What NOT to do" conventions, and reporting-back template all come from there.

---

## Pre-flight checks

Run each before starting Phase 1.

### Check 1 — Engine repo at expected path

```bash
ls -d "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/"
```

Expected: directory exists. All engine paths in this handoff are relative to it.

### Check 2 — Cartridge module starting state

```bash
ls "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/src/luna/cartridge/"
```

Expected: `__init__.py`, `builder.py`, `embedder.py`, `extractor.py`, `migrate.py`, `parsers/`, `schema.py`, `validation.py`. Phase 1 adds `ledger.py`; Phase 2 adds `payload_schemas.py`. If either already exists, **stop** and figure out why — someone else may have started this work.

### Check 3 — Locate `lun fsck` CLI entry point

```bash
grep -rln "fsck" "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/src/luna/cli/" 2>/dev/null
```

Expected location: `src/luna/cli/console.py` per the research-repo grep on 2026-05-22. Confirm before Phase 4 — if `lun fsck` lives elsewhere or doesn't exist yet, that becomes a Phase 4 sub-task (build the CLI host, then add the flags). Report the actual location in the Phase 4 status note.

### Check 4 — Existing test baseline green

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/" && pytest tests/ 2>&1 | tail -20
```

Expected: green. If red, fix or document the baseline failure before starting — do not start implementation with an already-failing test suite, because Phase 4 will struggle to distinguish new failures from pre-existing ones.

### Check 5 — `resolve_source_ref` location confirmed

```bash
grep -n "def resolve_source_ref" "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/src/luna/cartridge/__init__.py"
```

Expected: one match. Phase 3 patches this function. If absent, the function has been refactored — find it and update Phase 3 paths accordingly.

---

## Phase 1 — Schema + triggers + `ledger.py` core

**Source of truth:** `01_Specs/implemented/SPEC-005_annotation-ledger.md` lines 99–264.

### Schema additions to `src/luna/cartridge/schema.py`

Per spec lines 103–178, append to the `LUN_SCHEMA` constant:

- **`annotation_ledger` table** (lines 103–135) — 11 columns: `seq` AUTOINCREMENT PK, `ulid` UNIQUE + format CHECK, `entry_ts`, `event_type` + 8-value CHECK, `actor_id`, `actor_role` + 5-value CHECK, `target_kind`, `target_ulid`, `target_cartridge_ulid`, `payload` (TEXT JSON), `prev_hash` (nullable for genesis only), `entry_hash` UNIQUE + 64-char CHECK. Six multi-column CHECK constraints (lines 129–134).
- **4 indexes** on the ledger (lines 137–140): `idx_ledger_target`, `idx_ledger_actor`, `idx_ledger_type`, `idx_ledger_ts`.
- **2 BEFORE triggers** (lines 147–157): `annotation_ledger_no_update` and `annotation_ledger_no_delete`. Both raise `SQLITE_CONSTRAINT` on attempted UPDATE/DELETE. These are the soft-covenant append-only enforcement (Topic 3 of SQLite_Research).
- **`annotation_actors` table** (lines 163–172) — WITHOUT ROWID, ULID PK, `display_name`, `first_seen`, `last_seen`, `primary_role`, optional `public_key`.
- **4 new `meta` keys** (lines 174–178): `ledger_hash_algorithm = 'sha256'`, `ledger_genesis_ulid`, `ledger_head_seq`, `ledger_head_hash`. These get populated by the builder at finalize time, not by schema DDL itself.

### v0.3 format alignment in `src/luna/cartridge/schema.py`

In the same schema pass, align the engine schema with `LUN-FORMAT_v0.3.md`:

- Keep `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` only for FTS5 `content_rowid`; all application-facing references use `doc_nodes.ulid`.
- Convert `doc_nodes.parent_id` to `parent_ulid`.
- Convert `extractions` to `ulid TEXT PRIMARY KEY ... WITHOUT ROWID`; drop `extractions.id`.
- Rename `claim_sources` to `extraction_sources` with `extraction_ulid` + `node_ulid` composite key.
- Rename `claim_context_nodes` to `extraction_context_nodes` with `extraction_ulid` + `node_ulid` composite key.
- Convert `embeddings(node_id, level)` to `embeddings(node_ulid, level)`.
- Preserve Strategy A FTS5 DDL exactly: `nodes_fts(content, content='doc_nodes', content_rowid='id')` plus triggers on `doc_nodes.id`.

Do not add v0.3 compatibility aliases for `claim_sources` or `claim_context_nodes`.

### New module `src/luna/cartridge/ledger.py`

Per spec lines 199–264 + 292–380. New file with:

- `SYSTEM_ACTOR_ULID = "00000000000000000000000000"` constant (26 zero chars; passes ULID format CHECK; encodes 1970-01-01T00:00:00Z which no real build will produce — lines 187–195).
- `insert_ledger_event(conn, *, event_type, actor_id, actor_role, target_kind=None, target_ulid=None, target_cartridge_ulid=None, payload)` — the canonical insert pattern. Computes `seq` (next AUTOINCREMENT), `entry_ts` (monotone non-decreasing per current `meta.ledger_head_seq`), pulls `prev_hash` from `meta.ledger_head_hash` (NULL only at genesis), builds the 10-field canonical serialization (spec lines 202–214) pipe-separated, SHA-256-hashes it, INSERTs the row, then UPDATEs `meta.ledger_head_seq` + `meta.ledger_head_hash` atomically. Transaction-wrapped (`BEGIN IMMEDIATE` ... `COMMIT`).
- `verify_ledger_chain(conn)` — walks the ledger in `seq` order, recomputes each `entry_hash`, asserts continuity. Returns True / raises `LedgerVerificationError` with the offending `seq`.
- `build_genesis_payload(meta_dict)` — returns a canonical JSON string with exactly 4 required keys: `application_id`, `user_version`, `source_hash`, `format_version` (per payload-schemas.md lines 213–220). Uses the canonical JSON serialization mandate (spec lines 232–239): `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
- `LedgerVerificationError` exception class.

### Acceptance criteria

- Schema applies cleanly to a fresh in-memory SQLite DB (`sqlite3.connect(":memory:")` + `executescript(LUN_SCHEMA)`).
- Triggers reject UPDATE and DELETE — assert `sqlite3.IntegrityError` raised on attempted mutation.
- `insert_ledger_event()` produces **deterministic** hashes: same input → same `entry_hash`, byte-identical. Unit-test with a fixed payload + fixed timestamps.
- `verify_ledger_chain()` returns True on a single-row genesis-only ledger.
- `verify_ledger_chain()` raises `LedgerVerificationError` after a manually-corrupted `entry_hash` (test fixture: insert genesis legitimately, then SQL-bypass the trigger via `PRAGMA writable_schema=ON` + manual UPDATE — purely for the test).
- New unit tests live at `tests/cartridge/test_ledger.py` (or the equivalent path in the engine's test layout — confirm during Phase 1).

### Out of scope for Phase 1

- Wiring `insert_ledger_event()` into the builder (Phase 3).
- Payload validation (Phase 2).
- CLI flags (Phase 4).

---

## Phase 2 — `payload_schemas.py` + per-event validators + genesis payload

**Source of truth:** `01_Specs/implemented/SPEC-005_payload-schemas.md` lines 84–319.

### New module `src/luna/cartridge/payload_schemas.py`

Per payload-schemas.md lines 89–227 (per-event-type contracts) and 268–319 (validator pseudocode):

- `EVENT_SCHEMAS: dict[str, EventSchema]` mapping each `event_type` to an `EventSchema` declaring:
  - `required_keys: set[str]`
  - `optional_keys: set[str]`
  - `key_types: dict[str, type]` (or a typed-dict reference)
  - `consistency_rules: list[Callable[[dict], None]]` — lambdas that raise on rule failure
- **8 event types** (each with full per-key contracts from payload-schemas.md lines 91–206):
  - `claim_anchored` (lines 91–104)
  - `claim_disputed` (lines 105–118)
  - `claim_filtered` (lines 119–130)
  - `claim_reconciled` (lines 131–144)
  - `summary_overridden` (lines 145–158)
  - `cartridge_reviewed` (lines 159–172)
  - `cartridge_imported` (lines 173–193)
  - `meta` (lines 194–206) — including the genesis special case (lines 213–220) with 4 required keys
- `validate_payloads(conn) -> None` — walks `annotation_ledger`, JSON-parses each `payload`, asserts shape against the matching `EVENT_SCHEMAS` entry. Raises `BuildError` with offending `seq` on any mismatch.
- Pre-built helper: `validate_payload_against_schema(payload_dict, event_type, schema)` for unit-testing without DB round-trip.

### Acceptance criteria

- All 8 event types declared with full per-key contracts matching spec line ranges above.
- `validate_payloads()` **rejects** (one negative test per case): missing required key, present unknown key (when unknown-key strictness is opt-in — see payload-schemas.md lines 228–246; the default is to preserve unknown keys verbatim for hash integrity, NOT to reject), wrong value type, failed consistency-rule lambda, unknown `event_type` string.
- `validate_payloads()` **accepts**: correctly-shaped payloads for all 8 event types, including the genesis row's `meta` payload with exactly the 4 required keys.
- Unit tests at `tests/cartridge/test_payload_schemas.py` cover each event type with one positive + one negative case minimum (16 tests minimum + extras for genesis).

### Out of scope for Phase 2

- Calling `validate_payloads()` from `validate_ledger()` (Phase 4).
- Inserting any real (non-genesis) payloads into a cartridge (Phase 3).

---

## Phase 3 — Integration: builder, `resolve_source_ref`, migrate v2→v3

**Source of truth:** `01_Specs/implemented/SPEC-005_annotation-ledger.md` lines 267–502.

### Patch `src/luna/cartridge/builder.py`

Per spec lines 267–298:

1. After the cartridge body is finalized but **before** the SPEC-006 finalize stack (`optimize → wal_checkpoint → journal_mode=DELETE → VACUUM`), insert the genesis row:
   ```python
   from .ledger import insert_ledger_event, SYSTEM_ACTOR_ULID, build_genesis_payload
   from .payload_schemas import EVENT_SCHEMAS  # used for build-time validation

   genesis_payload = build_genesis_payload(meta)
   insert_ledger_event(
       conn,
       event_type="meta",
       actor_id=SYSTEM_ACTOR_ULID,
       actor_role="system",
       payload=genesis_payload,
   )
   ```
2. Initialize the 4 new meta keys: `ledger_hash_algorithm = 'sha256'`, `ledger_genesis_ulid = <ulid of genesis row>`, `ledger_head_seq = 1`, `ledger_head_hash = <entry_hash of genesis row>`.
3. UPSERT the system actor into `annotation_actors`: `display_name='system'`, `primary_role='system'`, `first_seen = last_seen = genesis.entry_ts`.
4. **Bump version markers:** `PRAGMA user_version = 3`; `meta.format_version = '0.3'`. Both happen as part of the builder finalization; cartridges produced by the v0.3-aware builder are v0.3 from the start.

### Patch `src/luna/cartridge/__init__.py`

Per spec lines 427–440:

- Add `include_provenance: bool = True` parameter to `resolve_source_ref()`. When `True` (default), augment each `claim_sources` entry in the return value with an `event: dict | None` sub-dict pulled by joining `annotation_ledger` on `claim_sources.event_id`. For v0.2 cartridges that don't have a ledger, `event` is always `None`; this is backwards-compatible behavior.
- Add new function `ledger_events(target_ulid: str) -> list[dict]` that returns ordered ledger events targeting a given ULID. Returns empty list if the ledger doesn't exist or the target has no events. Document the return shape.

Naming note: for v0.3 cartridges, the provenance source table is
`extraction_sources`, not `claim_sources`. Keep v0.2 read compatibility where
needed, but all v0.3 writer/query paths should use `extraction_sources` and
`extraction_context_nodes`.

### Patch `src/luna/cartridge/migrate.py`

Per spec lines 450–502 and `LUN-FORMAT_v0.3.md` § "Migration from v0.2". Add
a new function `_migrate_v2_to_v3(conn)`:

1. Apply the schema additions from Phase 1 (`annotation_ledger`, `annotation_actors`, triggers, indexes).
2. Initialize the 4 new meta keys (as in builder).
3. Insert the **genesis row** — `event_type='meta'`, `payload` carries the v0.2 cartridge identity (existing `application_id` + `user_version=2` + `source_hash` + `format_version='0.2'` BEFORE the version bump; this records what the cartridge *was* at migration time per spec lines 466–478).
4. Insert a **second `meta` event** recording the migration itself — same `event_type='meta'`, payload carries `{"action": "migrated_v2_to_v3", "from_version": 2, "to_version": 3, "migrated_at": <unix ms>}`.
5. Rewrite the v0.2 tables into the v0.3 layout:
   - `claim_sources` → `extraction_sources`
   - `claim_context_nodes` → `extraction_context_nodes`
   - integer FK columns become ULID FK columns
   - `extractions.id` is dropped
   - `doc_nodes.id` is retained only for FTS5; `parent_id` becomes `parent_ulid`
   - `embeddings.node_id` becomes `embeddings.node_ulid`
6. Verify Strategy A FTS5 survived the rewrite: `nodes_fts` still uses
   `content_rowid='id'`, triggers still attach to `doc_nodes.id`, and
   `nodes_fts` count matches eligible `doc_nodes`.
7. Verify `sqlite_sequence` contains `doc_nodes` only; `extractions` must not
   remain in `sqlite_sequence`.
8. UPSERT the system actor.
9. Bump `PRAGMA user_version = 3` and `meta.format_version = '0.3'` atomically with the inserts.

Atomic transaction: all of the above happens inside `BEGIN IMMEDIATE` ... `COMMIT`. On failure, the cartridge stays at v0.2 — no half-migrated state.

### Q3 migration-order validation gate

`LUN-FORMAT_v0.3.md` Q3 is still open. During `_migrate_v2_to_v3()` work,
Claude Code must validate whether the draft order works:

1. ledger creation
2. genesis event insertion
3. migration event insertion
4. table rewrites
5. FTS5 / `sqlite_sequence` verification
6. version/meta bump

If this order works, Phase 3's report must say so explicitly and cite the
round-trip test that proved it. If implementation requires reordering, stop
after documenting the failing constraint and the proposed corrected order.
Do not continue to Phase 4 or produce a v0.3 reference cartridge until Ahab
patches the research spec.

### Acceptance criteria

- **Fresh v0.3 build** produces a single-row ledger (genesis) on cartridge finalization. `validate_ledger()` (when Phase 4 lands) reports clean.
- **v0.2 → v0.3 migration** produces a 2-row ledger (genesis + migration-event); both rows hash-chain correctly; `meta.ledger_head_hash` matches the second row's `entry_hash`; `user_version` and `format_version` both bumped.
- **Q3 evidence** is recorded: either the draft order stands with test evidence, or the work stops with a corrected-order proposal for research-spec patching.
- `resolve_source_ref(handle, ref, include_provenance=True)` returns extraction source rows with the `event` sub-dict for anchored extractions with non-NULL `event_id`. For v0.2 cartridges (no ledger), `event` is `None` and no SQL error is raised.
- `ledger_events(target_ulid)` returns ordered event list for a given target ULID; returns empty list for unknown ULIDs.
- **Existing v0.2 cartridge tests still pass** — no regression on the SPEC-001/002/003/006 invariants.
- **Round-trip test:** build a small synthetic cartridge as v0.2 → migrate to v0.3 → re-open v0.3 → `validate_ledger()` returns clean.

### Out of scope for Phase 3

- The `validate_ledger()` function itself (Phase 4).
- `lun fsck` CLI flags (Phase 4).
- Real (non-`meta`) event inserts beyond the migration record (those happen through the ambassador-upgrade flow per SPEC-001; that flow's update to use `insert_ledger_event` is a follow-up after Phase 4 lands).

---

## Phase 4 — Validators + `lun fsck` flags + integration tests

**Source of truth:** `01_Specs/implemented/SPEC-005_annotation-ledger.md` lines 517–706; `01_Specs/implemented/SPEC-005_payload-schemas.md` lines 338–368.

### Patch `src/luna/cartridge/validation.py`

Add `validate_ledger(conn) -> None` implementing the 7-step pipeline (spec lines 517–681):

- **Step 0** — Trigger DDL presence: SELECT trigger CREATE statements from `sqlite_schema`, whitespace-normalize, compare to canonical strings. Reject if either trigger is missing or mutated.
- **Step 1** — Head pointer match: `meta.ledger_head_seq` and `meta.ledger_head_hash` agree with the actual MAX(seq) row in `annotation_ledger`.
- **Step 2a** — Per-row payload re-serialization: JSON-parse each `payload`, re-serialize with canonical settings (`sort_keys=True, separators=(",", ":"), ensure_ascii=False`), assert byte-equal to stored. Any mismatch means the writer didn't honor the canonical-payload mandate (lines 232–250).
- **Step 2b** — Per-row hash recompute: rebuild the 10-field canonical serialization, SHA-256-hash it, assert byte-equal to stored `entry_hash`.
- **Step 3** — Chain continuity: each row's `prev_hash` equals the previous row's `entry_hash` (in `seq` order). Only the genesis row has `prev_hash IS NULL`.
- **Step 4** — Monotone `entry_ts`: each row's `entry_ts >= previous row's entry_ts` (non-decreasing in `seq` order).
- **Step 5** — Actor consistency: every `actor_id` referenced in the ledger has a row in `annotation_actors`.
- **Step 6** — Genesis row uniqueness: exactly one row with `prev_hash IS NULL`, and it must be at `seq = 1`.
- **Step 7** — Call `validate_payloads(conn)` from `payload_schemas.py` (per payload-schemas.md line 314).

Each step raises `LedgerValidationError` with a clear message including the offending `seq` or row identifier.

### Fast-open path

Per spec lines 682–706: the **default** `lun fsck` (no flags) runs only Step 0 (trigger DDL check) + Step 1 (head pointer match). Both are O(1) regardless of ledger size. Document this in the validator file as a separate `validate_ledger_fast(conn)` function that calls only Steps 0 + 1; full-chain `validate_ledger()` calls all 7 steps.

### `lun fsck` CLI flags

Location confirmed in pre-flight Check 3 (expected `src/luna/cli/console.py`). Add flags:

- `lun fsck` (no flags) — runs `validate_ledger_fast()`; expected <100ms on any-size cartridge.
- `lun fsck --ledger` (alias: `--full-chain`) — runs full `validate_ledger()` (all 7 steps). Wall-clock scales with ledger size; expected sub-second for any realistic cartridge.
- `lun fsck --ledger-head` — runs only Step 1. Used by CI integrations that want a quick "has the ledger advanced" check.
- `lun fsck --payloads` — runs only `validate_payloads()` (Step 7 standalone). Useful for debugging payload-format issues without full chain walk.
- Existing `lun fsck` behavior (cartridge open validation per SPEC-006 `validate_cartridge_open`) continues to run as part of the default; the ledger fast-check is additive.

### Migration tool auto-validation

Patch `migrate.py` CLI entrypoint: after `_migrate_v2_to_v3()` completes, run `validate_ledger()` (full chain). Fail loud — if the migration produced an unverifiable ledger, the migration tool rolls back the transaction and reports the error. No silent half-state.

### Integration test: Meditations v0.3 rebuild

End-to-end smoke test:

1. Rebuild `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun` as v0.3 (either fresh build from the source PDF if available, OR migrate the existing v0.2 reference cartridge via `_migrate_v2_to_v3()`).
2. Run `lun fsck --ledger` against the v0.3 result. Expected: exit 0, clean output.
3. Run `lun fsck --payloads`. Expected: exit 0, clean output.
4. Run reproducibility queries from `04_Audits/AUDIT_2026-05-22_meditations-v02.md` § "Document tree" + § "Extractions" + § "Claim anchoring" against the v0.3 cartridge. Counts MUST match the audit baseline (ledger is additive — no impact on existing data):
   - `doc_nodes`: 3813 total (1 document + 176 sections + 310 paragraphs + 3326 sentences)
   - `extractions`: 1106 total (512 claims + 532 entities + 62 summaries)
   - `extraction_sources`: 520 rows (v0.3 name for the v0.2 `claim_sources` logical data: 458 anchored claims + 62 anchored summaries)
   - `anchor_status` distribution: 458 anchored / 54 match_failed / 0 synthesized / 0 filtered / 0 unknown-on-claims
5. Save the v0.3 cartridge alongside the v0.2 one (e.g., `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`) so the research-repo can audit it later.

### Acceptance criteria

- Full chain walk on a **corrupted cartridge** (test fixture with manually-edited `entry_hash`) returns a clear `LedgerValidationError` naming the offending `seq`.
- `--ledger-head` runs in O(1) wall-clock regardless of ledger size (test with a 1k+ row synthetic ledger).
- Default `lun fsck` validates a v0.3 cartridge in under 100ms (Meditations v0.3 cartridge as the realistic benchmark).
- v0.3 Meditations rebuild produces **identical** logical doc_nodes/extractions/source-anchor counts to the v0.2 audit baseline (`extraction_sources` is the v0.3 name for the old `claim_sources` data; ledger is additive).
- `pytest` suite green; new tests cover all four phases. Test count increases by >= 15 (rough lower bound: 4 ledger tests + 16+ payload tests + chain-walk tests + migration round-trip).

### Out of scope for Phase 4

- Wiring `insert_ledger_event()` into the SPEC-001 ambassador-upgrade flow — separate follow-up after Phase 4. (Phase 4 establishes the ledger infrastructure; real event types flowing through it is consumer-side work.)
- Research-spec amendments beyond Q3 reporting. The active `LUN-FORMAT_v0.3.md` already exists; if engine implementation invalidates the draft migration order, stop and return the corrected order for a research-repo patch.
- `lun fsck` performance optimization beyond the O(1) fast path. SPEC-005's fast-open path is already designed for it.

---

## What NOT to do

Mirroring the post-reader roadmap (§234-242):

- **No research-repo edits.** This handoff is engine-repo work only. The research repo at `/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/` is read-only during this work. Read freely; don't write.
- **No new spec drafting.** SPEC-005 + payload-schemas are accepted and authoritative. The handoff implements them; it does not re-design them. If implementation surfaces a real design issue, **stop** and flag back to Ahab for a research-repo amendment session — do NOT change the spec inline as a workaround.
- **No SPEC-004 implementation.** SPEC-004 is application-layer trust composition, not engine-layer ledger. Out of scope. SPEC-004 is accepted as a contract but remains non-engine work for this handoff.
- **No research-spec edits from the engine repo.** `LUN-FORMAT_v0.3.md` is active and authoritative for this implementation. If engine work surfaces a real design issue, stop and report it back for a research-repo amendment instead of patching the spec from the engine implementation session.
- **No README updates** in either repo. "v0.3 in development" framing is a separate cleanup pass.
- **No skipping the migration tool.** SPEC-005 requires both fresh-build AND v0.2→v0.3 migration paths. Implement both in Phase 3.
- **No bypassing the soft-covenant triggers in legitimate code paths.** The triggers can be defeated by `PRAGMA writable_schema=ON` + DROP TABLE / DROP TRIGGER, and that's a documented honest limit (SQLite_Research Topic 3). The implementation MUST NOT use that escape hatch for any legitimate writer code — only test fixtures may bypass triggers, and only with explicit `PRAGMA writable_schema=ON` calls that make the test's intent clear.
- **No fabricated logprob data.** The carried-forward Phase 5 item 5 (backend logprobs not exposed) is unchanged; SPEC-005 implementation does not surface logprobs and must not fabricate them.
- **No Priests-and-Programmers / Lansing references** in new code, tests, or docs. Marcus-Aurelius-Meditations is the canonical example.

---

## Reporting back

After each phase, append a status note to the engine-side working journal (location: engine team's call — this handoff doesn't prescribe where the engine repo's journal lives; if no such convention exists, create `08_Journal/2026-05-XX.md` in the engine repo or a `JOURNAL.md` at the engine root).

One block per completed phase, per the template:

```
## Phase N — <subject> (completed)

- What changed (one line per file touched)
- Anything unexpected
- Any decisions surfaced for Ahab
```

### Phase 4 closing note additionally includes

1. **v0.3 cartridge produced** — yes/no + absolute path to the v0.3 Meditations build
2. **`lun fsck --ledger` runtime** on Meditations v0.3 (wall-clock seconds)
3. **Test suite delta** — passing test count before vs. after (e.g., `342 → 368, +26 new`)
4. **CLI location** — confirmed path to `lun fsck` (validates pre-flight Check 3); if a new CLI host was created, document where
5. **Q3 migration-order result** — state whether the draft order stood, with the test name/output proving it; if it did not stand, stop and provide the corrected order + blocking constraint for Ahab
6. **Decisions for Ahab** — anything else the engine surfaced (e.g., design ambiguities discovered, performance trade-offs taken, naming conventions chosen)
7. **Pointer to v0.3 Meditations cartridge** for downstream research-repo audit work

Keep the per-phase notes tight; the engine repo's code + tests are the substantive output.

---

## Cross-references

File paths the engine team uses throughout:

- **SPEC-005 ledger spec** (target of implementation): `01_Specs/implemented/SPEC-005_annotation-ledger.md`
- **SPEC-005 payload schemas** (companion target): `01_Specs/implemented/SPEC-005_payload-schemas.md`
- **Active v0.3 format target**: `03_Format_Spec/LUN-FORMAT_v0.3.md`
- **Q1 FTS5 evidence**: `02_Handoffs/HANDOFF_2026-05-22_v03-fts5-strategy-prototype.md` and engine report `Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md`
- **Reference v0.2 cartridge** for migration testing: `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun`
- **v0.2 audit baseline** for regression check: `04_Audits/AUDIT_2026-05-22_meditations-v02.md`
- **Tauri reader** (expected to reject v0.3 cartridges per SPEC-006 + SPEC-002 Q5 retirement — see `LUN-FORMAT_v0.2.md` File identification step 3): `06_Prototypes/ReaderPrototype/`
- **Engine repo root**: `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`
- **Cartridge module**: `src/luna/cartridge/`
- **CLI host** (likely): `src/luna/cli/console.py`
- **Tests root** (likely): `tests/`
- **Existing handoff this one mirrors**: `02_Handoffs/HANDOFF_2026-05-22_post-reader-v02-roadmap.md`
- **SPEC-002 Phase 3 cowork brief** (secondary structural template — for the "per-phase decisions + questions" style): `02_Handoffs/HANDOFF_2026-05-10_spec-002-cowork-brief.md`
- **Format spec for SPEC-006 / v0.2 baseline reference**: `03_Format_Spec/LUN-FORMAT_v0.2.md`
- **SQLite hash-chain prior art**: `05_Reference/SQLite_Research.md` Topic 3 (lines 25–105)

---

## Expected outputs at handoff completion

After all four phases ship:

1. `src/luna/cartridge/schema.py` — `annotation_ledger` + `annotation_actors` tables, triggers, indexes added.
2. `src/luna/cartridge/ledger.py` — new file with `insert_ledger_event`, `verify_ledger_chain`, `build_genesis_payload`, `SYSTEM_ACTOR_ULID`, `LedgerVerificationError`.
3. `src/luna/cartridge/payload_schemas.py` — new file with `EVENT_SCHEMAS` (8 event types), `validate_payloads`.
4. `src/luna/cartridge/builder.py` — patched to insert genesis row, populate ledger meta keys, bump `user_version=3` + `format_version='0.3'`.
5. `src/luna/cartridge/__init__.py` — `resolve_source_ref()` accepts `include_provenance`; new `ledger_events()` function exposed.
6. `src/luna/cartridge/migrate.py` — `_migrate_v2_to_v3()` function added; CLI entry auto-runs full chain validation.
7. `src/luna/cartridge/validation.py` — `validate_ledger()` (7 steps) + `validate_ledger_fast()` (Steps 0+1).
8. `src/luna/cli/console.py` (or actual `lun fsck` host) — flags `--ledger`, `--ledger-head`, `--full-chain`, `--payloads` added; default behavior runs fast-open path.
9. `tests/cartridge/test_ledger.py`, `tests/cartridge/test_payload_schemas.py`, `tests/cartridge/test_migrate_v3.py` (or engine's equivalent paths) — full coverage for all four phases.
10. v0.3 Meditations cartridge produced and saved alongside the v0.2 reference.

The handoff is **complete** when all 10 outputs are in place, `pytest` is green, and `lun fsck --ledger` on the v0.3 Meditations cartridge exits clean.
