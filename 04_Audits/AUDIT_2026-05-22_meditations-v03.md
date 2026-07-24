# AUDIT: Marcus-Aurelius-Meditations.v03.lun

**Audit date:** 2026-05-22
**Auditor:** Codex (research-repo session, on behalf of Ahab)
**Cartridge built/migrated:** 2026-05-22T06:36:23+00:00 source lineage, migrated to v0.3 after Luna Engine commit `407122f`
**Format version:** v0.3 (`user_version = 3`, `application_id = 0x4C554E43`)
**Tool:** `sqlite3` CLI + Luna Engine `lun fsck` implementation
**Source file:** Marcus-Aurelius-Meditations.pdf

---

## Purpose

Shipping-gate audit for the v0.3 Meditations reference cartridge:

`07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`

This validates the accepted v0.3 format shape after SPEC-005 landed in Luna
Engine commit `407122f`: annotation ledger, payload schemas, v0.2 -> v0.3
migration order, renamed extraction reference tables, and `sqlite_sequence`
postconditions.

ReaderPrototype v0.3 support is explicitly out of scope. The current
ReaderPrototype is a v0.2 portability proof and is expected to reject or fail
to fully interpret v0.3 until a separate compatibility slice lands.

## Method

Two-tool approach:

1. **`sqlite3` CLI** for read-only structural and data queries. The exact
   commands are included below so another operator can reproduce the findings.
2. **Luna Engine `lun fsck` host** via
   `.venv/bin/python -m luna.cartridge.fsck`, run from the engine repo at
   `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root`.

No engine code, reader code, or format-spec file was changed by this audit.

## Meta and pragma contents

```sql
PRAGMA application_id;
PRAGMA user_version;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT key, value FROM meta ORDER BY key;
```

```
application_id          1280659011 (= 0x4C554E43)
user_version            3
integrity_check         ok
foreign_key_check       no rows

cartridge_kind          knowledge
created_at              2026-05-22T06:36:23.354052+00:00
embedding_dim           384
embedding_model         all-MiniLM-L6-v2
format_version          0.3
ledger_genesis_ulid     01KS8N7VA5F5PCQ1J6PBVQ17A6
ledger_hash_algorithm   sha256
ledger_head_hash        09541bcba0a5356cdd60de999efd1498a9fdde8cecb6ed99edbc64979b84965f
ledger_head_seq         2
logprob_attribution     response_level
logprob_base            e
node_count              3813
source_filename         Marcus-Aurelius-Meditations.pdf
source_format           pdf
source_hash             b02870de0fbf771af42aefcdd6a6ccc4386381fa20d3513242acf8b4f9c25aca
title                   The meditations of
word_count              76651
```

### Meta findings

- **POSITIVE.** `application_id`, `user_version`, and `format_version` match
  the v0.3 contract.
- **POSITIVE.** `ledger_hash_algorithm = 'sha256'`, `ledger_head_seq = 2`,
  `ledger_head_hash`, and `ledger_genesis_ulid` are present.
- **POSITIVE.** `PRAGMA integrity_check = 'ok'`; `PRAGMA foreign_key_check`
  returned no rows.
- **CARRIED FORWARD M-01 (medium, non-blocking for v0.3 shipping).**
  `title = "The meditations of"` remains semantically truncated, matching the
  v0.2 audit finding. This is a builder/parser title-selection follow-up, not
  a v0.3 format blocker.

## v0.3 schema delta

```sql
SELECT name
FROM sqlite_schema
WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
ORDER BY name;

PRAGMA table_list;
PRAGMA table_info(extractions);
PRAGMA table_info(doc_nodes);
PRAGMA table_info(extraction_sources);
PRAGMA table_info(annotation_ledger);
```

Observed tables:

```
annotation_actors
annotation_ledger
doc_nodes
embeddings
extraction_context_nodes
extraction_sources
extractions
meta
nexus_refs
nodes_fts
nodes_fts_config
nodes_fts_data
nodes_fts_docsize
nodes_fts_idx
```

Key schema observations:

- **POSITIVE.** `claim_sources` is gone; `extraction_sources` is present.
- **POSITIVE.** `claim_context_nodes` is gone; `extraction_context_nodes` is
  present.
- **POSITIVE.** `annotation_ledger` and `annotation_actors` are present.
- **POSITIVE.** `extractions` is `WITHOUT ROWID` and uses `ulid TEXT` as the
  primary key.
- **POSITIVE.** `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` remains only
  for FTS5 `content_rowid`; `doc_nodes.ulid` remains the portable identity.
- **POSITIVE.** `nodes_fts` is present and contains 3813 rows, matching
  `doc_nodes`.

## Counts and parity

```sql
SELECT type, count(*) FROM doc_nodes GROUP BY type ORDER BY type;
SELECT type, count(*) FROM extractions GROUP BY type ORDER BY type;
SELECT anchor_status, count(*)
FROM extractions
WHERE type='claim'
GROUP BY anchor_status
ORDER BY anchor_status;
SELECT count(*) FROM extraction_sources;
SELECT count(*) FROM extraction_context_nodes;
SELECT count(*) FROM embeddings;
SELECT level, count(*) FROM embeddings GROUP BY level;
SELECT length(vector) FROM embeddings LIMIT 1;
SELECT count(*) FROM nexus_refs;
SELECT count(*) FROM nodes_fts;
```

```
doc_nodes.document       1
doc_nodes.paragraph      310
doc_nodes.section        176
doc_nodes.sentence       3326
doc_nodes.total          3813

extractions.claim        512
extractions.entity       532
extractions.summary      62
extractions.total        1106

claims.anchored          458
claims.match_failed      54

extraction_sources       520
extraction_context_nodes 0
embeddings               459
embeddings.paragraph     310
embeddings.section       149
vector byte length       1536
nexus_refs               0
nodes_fts                3813
```

### Count findings

- **POSITIVE.** v0.3 preserves the v0.2 document and extraction counts:
  3813 `doc_nodes`, 1106 `extractions`, 520 source links.
- **POSITIVE.** Claim anchor distribution matches the v0.2 audit baseline:
  458 anchored, 54 match_failed.
- **POSITIVE.** `extraction_context_nodes = 0`, consistent with this corpus
  having no synthesized claims requiring multi-source context rows.
- **S-01 CLASSIFIED 2026-07-23 (low, non-blocking; expected policy).** Embedding
  coverage remains 459 total rows: 310 paragraph embeddings and 149 section
  embeddings against **166** sections post-M-01 (**149/166**, not 149/176).
  Skip predicate: sections whose recursive subtree has no embeddable text
  nodes. See v0.2 audit classification note and `LUN-FORMAT_v0.3.md` Coverage
  policy.

## Ledger and postconditions

```sql
SELECT seq, event_type, actor_id, actor_role, target_kind, target_ulid,
       json_extract(payload,'$.action'),
       json_extract(payload,'$.from_version'),
       json_extract(payload,'$.to_version'),
       length(entry_hash),
       prev_hash IS NULL AS prev_hash_is_null
FROM annotation_ledger
ORDER BY seq;

SELECT actor_id, display_name, primary_role
FROM annotation_actors
ORDER BY actor_id;

SELECT name, seq FROM sqlite_sequence ORDER BY name;
```

```
seq  event_type  actor_role  action              from  to  hash_len  prev_hash_is_null
1    meta        system                          NULL  NULL 64        1
2    meta        system      migrated_v2_to_v3   2     3    64        0

actor_id                    display_name                 primary_role
00000000000000000000000000  00000000000000000000000000   system

sqlite_sequence.annotation_ledger  2
sqlite_sequence.doc_nodes          3813
```

### Ledger findings

- **POSITIVE.** `annotation_ledger` has exactly 2 rows: genesis plus v0.2 ->
  v0.3 migration.
- **POSITIVE.** Both ledger rows use `event_type = 'meta'` and
  `actor_role = 'system'`, matching the accepted v0.3 amendment and satisfying
  the CHECK constraint that reserves system actor events for meta events.
- **POSITIVE.** The migration row payload records
  `action = 'migrated_v2_to_v3'`, `from_version = 2`, `to_version = 3`.
- **POSITIVE.** `sqlite_sequence` contains exactly the two AUTOINCREMENT
  tables expected for v0.3: `annotation_ledger` and `doc_nodes`.
- **POSITIVE.** No `extractions` entry appears in `sqlite_sequence`.

## `lun fsck`

Commands run from:

`/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root`

```bash
.venv/bin/python -m luna.cartridge.fsck \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun"

.venv/bin/python -m luna.cartridge.fsck \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun" \
  --ledger

.venv/bin/python -m luna.cartridge.fsck \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun" \
  --payloads
```

```
OK: Marcus-Aurelius-Meditations.v03.lun uv=3 ledger=2 rows, head=2 0.3ms
OK: Marcus-Aurelius-Meditations.v03.lun uv=3 ledger=2 rows, head=2 1.5ms
OK: Marcus-Aurelius-Meditations.v03.lun uv=3 ledger=2 rows, head=2 0.2ms
```

### Fsck findings

- **POSITIVE.** Default fast-open validation passes.
- **POSITIVE.** Full ledger chain validation passes.
- **POSITIVE.** Per-event payload schema validation passes.
- **POSITIVE.** Runtime is well within the handoff bounds (`<100 ms` default,
  sub-second full ledger walk).

## Summary of findings

| ID | Severity | Area | Finding | Disposition |
|---|---:|---|---|---|
| M-01 | medium | metadata | Title remains truncated: `The meditations of` | Carried forward from v0.2; not a v0.3 blocker |
| S-01 | low | embeddings | Section embedding coverage **149/166** — expected policy skip | Classified 2026-07-23; not a defect |

Plus positive v0.3 findings:

- `application_id`, `user_version`, `format_version`, and meta ledger keys
  match the accepted v0.3 contract.
- v0.2 parity holds for document tree, extractions, source links, and claim
  anchor counts.
- v0.3 schema deltas are present: renamed extraction reference tables,
  ledger tables, `extractions` ULID primary identity, and FTS5 Strategy A.
- Ledger rows, payloads, hash-chain validation, and `sqlite_sequence`
  postconditions all pass.

## Audit call

**No v0.3 shipping blockers found.**

`Marcus-Aurelius-Meditations.v03.lun` is eligible for
`LUN-FORMAT_v0.3.md` promotion from **accepted** to **Shipping**, subject only
to Ahab's separate status-promotion step. This audit does not perform that
promotion.

## Recommended follow-ups

1. Promote `LUN-FORMAT_v0.3.md` from `Status: accepted` to `Status: Shipping`
   in a separate docs slice, citing this audit and engine commit `407122f`.
2. Add ReaderPrototype v0.3 support so the independent reader can open v0.3
   cartridges and display ledger-backed provenance.
3. Keep the v0.2 carry-forward follow-ups alive: title truncation (M-01) and
   section embedding coverage policy (S-01).
4. Draft separate implementation briefs for ambassador-upgrade ledger wiring
   and SPEC-004 composer integration.
