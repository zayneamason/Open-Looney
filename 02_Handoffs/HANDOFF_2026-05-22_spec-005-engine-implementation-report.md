# HANDOFF: SPEC-005 engine implementation — completion report

**Date:** 2026-05-22
**From:** Claude Code (engine-repo session against `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`)
**To:** Ahab (research-repo session)
**Pair to:** `02_Handoffs/HANDOFF_2026-05-22_spec-005-engine-implementation.md` (original implementation brief)
**Engine commit:** `407122f` (`feat(cartridge): SPEC-005 v0.3 schema + annotation ledger + lun fsck`)
**Purpose:** Report-back closing the SPEC-005 + payload-schemas engine implementation. All four phases shipped; v0.3 reference cartridge produced; Q3 migration-order evidence captured. This document surfaced two spec inconsistencies for research-repo amendment; the research closeout has now patched them.

---

## Status at a glance

| Phase | Subject | Status | Tests |
|---|---|---|---|
| 1 | Schema + triggers + `ledger.py` core | ✅ complete | 9 |
| 2 | `payload_schemas.py` + per-event validators | ✅ complete | 27 |
| 3 | Builder genesis-row + v0.3 schema alignment + migrate v2→v3 + reader provenance | ✅ complete | 24 |
| 4 | `validate_ledger` + `lun fsck` CLI + Meditations v0.3 rebuild | ✅ complete | 21 |
| **Total** | | | **81 green** |

Cartridge regression check (extraction_pipeline + 3 substrate suites): 34 passed, 2 pre-existing failures unchanged from baseline (scribe/librarian actor batching, unrelated to schema).

---

## Acceptance artifact

**v0.3 reference cartridge produced:**
`07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun` (2.6 MB)

Built by migrating the v0.2 reference (`Marcus-Aurelius-Meditations.lun`) via `python -m luna.cartridge.migrate`. The v0.2 reference is unchanged.

### Audit parity vs. `AUDIT_2026-05-22_meditations-v02.md`

Every count matches exactly — the migration is logically lossless:

| Metric | v0.2 baseline | v0.3 actual |
|---|---|---|
| `doc_nodes` (1 doc + 176 sections + 310 paragraphs + 3326 sentences) | 3813 | 3813 ✓ |
| `extractions` (512 claims + 532 entities + 62 summaries) | 1106 | 1106 ✓ |
| `extraction_sources` (was `claim_sources` in v0.2) | 520 | 520 ✓ |
| `anchor_status`: anchored / match_failed (claims) | 458 / 54 | 458 / 54 ✓ |
| `annotation_ledger` rows | n/a | 2 (genesis + migration `meta` event) ✓ |
| `PRAGMA user_version` | 2 | 3 ✓ |
| `meta.format_version` | `'0.2'` | `'0.3'` ✓ |

### `lun fsck` runtime on Meditations v0.3

| Mode | Runtime | Target |
|---|---|---|
| default (fast path) | **0.2 ms** | <100 ms |
| `--ledger` (full 7-step chain walk) | **1.2 ms** | sub-second |
| `--payloads` | **0.1 ms** | (informational) |

All comfortably under the handoff acceptance bounds.

---

## Q3 migration-order evidence (LUN-FORMAT_v0.3.md gate)

**The draft migration order in `LUN-FORMAT_v0.3.md` held.** Evidence: `tests/test_cartridge_migrate_v3.py::test_q3_migration_order_holds` exercises the documented order end-to-end (ledger creation → genesis event → migration event → table rewrites → FTS5/`sqlite_sequence` verification → meta/pragma bump), then `verify_ledger_chain` + `validate_payloads` both pass. Phase 4 added `validate_ledger()` to the migration tail as the auto-validation hook — corrupt migrations now fail loud before commit.

**Closeout result:** `LUN-FORMAT_v0.3.md` is now `Status: accepted`; the two amendments below are applied.

---

## Decisions surfaced (research-repo amendments applied)

### 1. Migration event `event_type` — spec inconsistency

The pre-closeout `LUN-FORMAT_v0.3.md` migration step specified the migration event as `event_type='cartridge_imported'` with `actor_role='system'`:

```json
{"relationship": "migrated_from", "source_application_id": "0x4C554E43",
 "source_source_hash": "<existing meta.source_hash>",
 "source_user_version": 2}
```

But the `annotation_ledger` table CHECK constraint forbids this combination:

```sql
CHECK (actor_role != 'system' OR event_type = 'meta')  -- system actor reserved for chain meta-events
```

The original engine handoff at lines 235–237 instead specifies `event_type='meta'` for the same event, which the CHECK accepts. **Engine implementation follows the handoff** (uses `event_type='meta'` with a payload of `{"action":"migrated_v2_to_v3", "from_version":2, "to_version":3, "source_hash":..., "migrated_at":...}`).

**Closeout amendment applied:** `LUN-FORMAT_v0.3.md` §"Migration from v0.2" step 4 now says `event_type = 'meta'` and uses the action-shaped payload. The CHECK constraint remains tight and the actor-role taxonomy remains unambiguous.

### 2. `sqlite_sequence` postcondition wording

The pre-closeout `LUN-FORMAT_v0.3.md` `sqlite_sequence` section allowed only the `doc_nodes` entry in v0.3 cartridges, with `extractions` removed. **In practice, `annotation_ledger.seq INTEGER PRIMARY KEY AUTOINCREMENT` also registers in `sqlite_sequence`** — so v0.3 cartridges legitimately have two entries: `doc_nodes` and `annotation_ledger`. The Phase 3 migration's `_verify_v03_postconditions` correctly accepts this (it only rejects the presence of `extractions`).

**Closeout amendment applied:** the wording now says `sqlite_sequence` MUST list `doc_nodes` and `annotation_ledger` (the two AUTOINCREMENT tables in v0.3); `extractions` MUST NOT appear.

---

## What shipped (file inventory)

### Engine repo (`/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`)

New modules:
- `src/luna/cartridge/ledger.py` — `SYSTEM_ACTOR_ULID`, `insert_ledger_event`, `verify_ledger_chain`, `build_genesis_payload`, `canonical_hash_input`, `LedgerVerificationError`.
- `src/luna/cartridge/payload_schemas.py` — `EventSchema`/`EventProxy` dataclasses, `EVENT_SCHEMAS` dict (8 event types), `validate_payload_against_schema`, `validate_payloads`.
- `src/luna/cartridge/fsck.py` — `lun fsck` CLI host (`_cli`, `_run_fsck`).

Patched modules:
- `src/luna/cartridge/schema.py` — v0.3 alignment: `claim_sources`→`extraction_sources`, `claim_context_nodes`→`extraction_context_nodes`, `extractions` WITHOUT ROWID ULID PK, `doc_nodes.parent_id`→`parent_ulid`, `embeddings(node_ulid, level)` PK. Strategy A FTS5 preserved on `doc_nodes.id`. Ledger DDL + indexes + append-only triggers added.
- `src/luna/cartridge/builder.py` — `PRAGMA user_version=3`; `meta.format_version='0.3'`; genesis row + 4 ledger meta keys (`ledger_hash_algorithm`, `ledger_genesis_ulid`, `ledger_head_seq`, `ledger_head_hash`) inserted before `finalize_for_shipping`; `parent_ulid` direct write; `validate_payloads(conn)` added to the pre-finalize validator chain.
- `src/luna/cartridge/extractor.py` — drops `cursor.lastrowid`, uses ULID identity throughout; writes to `extraction_sources`; tree traversal via `parent_ulid`.
- `src/luna/cartridge/embedder.py` — `parent_ulid` joins; embeddings INSERT uses `(node_ulid, level, vector)`.
- `src/luna/cartridge/validation.py` — `validate_anchors` + `validate_ulids` dispatch on `_detect_schema_version()` to serve both v0.2 and v0.3; `validate_cartridge_open` accepts `user_version in {1, 2, 3}` plus checks `ledger_hash_algorithm='sha256'` for v0.3; new `validate_ledger` (7 steps) + `validate_ledger_fast` (steps 0+1) + `LedgerValidationError` + `EXPECTED_NO_UPDATE_DDL`/`EXPECTED_NO_DELETE_DDL` constants.
- `src/luna/cartridge/__init__.py` — `resolve_source_ref(include_provenance=True)` joins `annotation_ledger` via `extraction_sources.event_id` for v0.3 cartridges; new `ledger_events(lun_path, target_ulid)` helper; v0.2 fallback path preserved; re-exports `LedgerValidationError`, `validate_ledger`, `validate_ledger_fast`.
- `src/luna/cartridge/migrate.py` — new `_migrate_v2_to_v3()` + `migrate()` auto-dispatcher; inline ledger insert helper so the migration stays atomic under `BEGIN EXCLUSIVE`; CLI routes by current `user_version`; `validate_ledger()` wired into the validator chain.
- `pyproject.toml` — added `lun = "luna.cartridge.fsck:_cli"` entry point.

Tests:
- `tests/test_cartridge_ledger.py` (9), `tests/test_cartridge_payload_schemas.py` (27), `tests/test_cartridge_builder_v03.py` (9), `tests/test_cartridge_migrate_v3.py` (9), `tests/test_cartridge_resolve_source_ref_v03.py` (6), `tests/test_cartridge_validate_ledger.py` (14), `tests/test_cartridge_fsck_cli.py` (7).
- `tests/substrate/test_bridge001_v02_read_adapter.py` — updated to use inline v0.2 DDL (since `LUN_SCHEMA` is now v0.3-shaped); the substrate's v0.2 read adapter coverage is preserved.

### Research repo (this repo)

Single new artifact:
- `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun` — v0.3 reference cartridge built 2026-05-22 by migrating the v0.2 reference. No source / spec / handoff files were edited during the engine implementation work.

---

## How to verify from the engine repo

From `cd "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/"`:

```bash
# 1. Full SPEC-005 unit suite (81 tests, 81 passed in 0.81s)
pytest tests/test_cartridge_ledger.py tests/test_cartridge_payload_schemas.py \
       tests/test_cartridge_builder_v03.py tests/test_cartridge_migrate_v3.py \
       tests/test_cartridge_resolve_source_ref_v03.py \
       tests/test_cartridge_validate_ledger.py tests/test_cartridge_fsck_cli.py -v

# 2. lun fsck against the v0.3 reference cartridge
MED="../Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun"
python -m luna.cartridge.fsck "$MED"             # fast path (~0.2 ms)
python -m luna.cartridge.fsck "$MED" --ledger    # full 7-step (~1.2 ms)
python -m luna.cartridge.fsck "$MED" --payloads  # schema check (~0.1 ms)

# 3. End-to-end build → migrate roundtrip on your own data
python -m luna.cartridge.builder some.md --no-extract --no-embed
python -m luna.cartridge.fsck some.lun --ledger
cp existing-v02.lun copy.lun && python -m luna.cartridge.migrate copy.lun
python -m luna.cartridge.fsck copy.lun --ledger

# 4. Direct SQLite inspection
sqlite3 "$MED" "SELECT seq, event_type, actor_role FROM annotation_ledger ORDER BY seq;
                SELECT count(*) FROM extraction_sources;
                PRAGMA user_version;"
```

After `pip install -e .` the `lun` command lands on PATH and the `python -m luna.cartridge.` prefix is unnecessary.

---

## Recommended next steps (research-repo side)

Closed by the 2026-05-22 research closeout:

- Patched the two spec inconsistencies in `LUN-FORMAT_v0.3.md`.
- Promoted `LUN-FORMAT_v0.3.md` from `Status: active` to `Status: accepted`.
- Promoted the SPEC-005 ledger and payload-schema docs into `01_Specs/implemented/`, with implementation notes citing commit `407122f`.
- Wrote `04_Audits/AUDIT_2026-05-22_meditations-v03.md`; the audit found no v0.3 shipping blockers.
- Promoted `LUN-FORMAT_v0.3.md` from `Status: accepted` to `Status: Shipping`.

Remaining in dependency order:

1. **Update memory entries**:
   - `project_v02_status.md` → reflect v0.3 engine implementation complete, Meditations v0.3 reference produced.
   - `project_reference_cartridge_v02.md` → add a sibling note pointing at the v0.3 reference.

## Recommended next steps (engine-repo side — separate handoffs)

These were explicitly out of scope for the SPEC-005 handoff. Each warrants its own brief:

- **SPEC-001 ambassador-upgrade flow rewrite** to use `insert_ledger_event` (ledger infrastructure is ready; consumer wiring is the next slice). Reference: SPEC-005 lines 382–425.
- **ReaderPrototype (`06_Prototypes/ReaderPrototype/`) v0.3 support** — the Tauri reader at `src-tauri/src/cartridge.rs` has a hard `user_version == 2` check that currently rejects v0.3 cartridges. Bump to accept 3 (and optionally call `validate_ledger_fast` via FFI or a sidecar shell-out).
- **SPEC-004 (multi-axis imprint weights)** — now unblocked: the Contestation and Resonance axes can be computed from the ledger event stream that v0.3 provides.

---

## Cross-references

- **Source handoff (this report closes):** `02_Handoffs/HANDOFF_2026-05-22_spec-005-engine-implementation.md`
- **Format spec (live target):** `03_Format_Spec/LUN-FORMAT_v0.3.md`
- **v0.3 shipping-gate audit:** `04_Audits/AUDIT_2026-05-22_meditations-v03.md`
- **SPEC-005 ledger (implemented):** `01_Specs/implemented/SPEC-005_annotation-ledger.md`
- **SPEC-005 payload schemas (implemented):** `01_Specs/implemented/SPEC-005_payload-schemas.md`
- **v0.2 audit baseline (compared against):** `04_Audits/AUDIT_2026-05-22_meditations-v02.md`
- **v0.2 reference cartridge:** `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun`
- **v0.3 reference cartridge (new):** `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`
- **Engine repo root:** `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`
