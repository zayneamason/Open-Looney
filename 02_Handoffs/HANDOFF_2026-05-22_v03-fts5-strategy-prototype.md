# HANDOFF: LUN v0.3 Q1 FTS5 strategy prototype

**Date:** 2026-05-22
**From:** Codex
**To:** Claude Code
**Purpose:** Resolve `LUN-FORMAT_v0.3.md` Q1 with measured engine-side evidence: keep `doc_nodes.id` only for FTS5, or remove it and rebuild FTS around ULIDs.

**Execution status:** Implemented by Codex on 2026-05-22. The engine script and report now exist, the full probe ran, and the measured recommendation is **Recommend A**. This document remains the repeatable runbook for rerunning or auditing the prototype.

---

## Current state

`03_Format_Spec/LUN-FORMAT_v0.3.md` is active, not accepted. Q1 blocks promotion because FTS5 external-content mode needs an INTEGER `content_rowid`, while the v0.3 portability goal wants `doc_nodes.id` removed.

The current draft carries Strategy A as baseline DDL:

- keep `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT` only as FTS5 `content_rowid`
- make `doc_nodes.ulid` the canonical application identifier
- move all cross-table references to ULIDs

The open alternative is Strategy B:

- drop `doc_nodes.id`
- rebuild FTS5 around a mapping/contentless design
- choose B only if it is measurably close enough and does not break search UX

This handoff is a prototype slice. Do not rewrite production schema, migration, builder, or reader code yet.

---

## Repos and fixed paths

Research repo, read-only except this handoff:

```bash
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development
```

Engine repo, implementation target:

```bash
/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root
```

Reference cartridge:

```bash
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun
```

Prototype script to add in the engine repo:

```bash
scripts/probe_v03_fts5_strategies.py
```

Report to produce in the engine repo:

```bash
Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md
```

---

## Guardrails

Do not edit these files in this slice:

- `src/luna/cartridge/schema.py`
- `src/luna/cartridge/builder.py`
- `src/luna/cartridge/migrate.py`
- `03_Format_Spec/LUN-FORMAT_v0.3.md`

Do not promote v0.3 from active to accepted.

Do not modify the source Meditations `.lun`; copy it into a temp workspace before probing.

Keep existing unrelated dirty files out of the slice. On 2026-05-22, the engine repo already had an unrelated untracked `tmp/` directory.

---

## Required pre-flight checks

Run from the engine repo:

```bash
pwd
ls src/luna/cartridge
python3 - <<'PY'
import sqlite3
print(sqlite3.sqlite_version)
PY
```

Confirm the v0.2 schema still has the current FTS shape:

```bash
sed -n '1,180p' src/luna/cartridge/schema.py
```

Confirm the reference cartridge opens and has FTS rows:

```bash
sqlite3 "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun" \
  "select count(*) from doc_nodes; select count(*) from nodes_fts;"
```

Expected on the current cartridge: `3813` doc nodes and `3813` FTS rows.

---

## Prototype script requirements

Add `scripts/probe_v03_fts5_strategies.py`.

The script must:

- accept `--source`, `--out-dir`, `--report`, `--build-runs`, `--query-runs`, and `--warmups`
- default `--source` to the Meditations `.lun` path above
- default `--out-dir` to `/private/tmp/lun_v03_fts5_probe`
- default `--report` to `Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md`
- never mutate the source cartridge
- create fresh strategy databases under the output directory
- emit a machine-readable `results.json`
- emit the Markdown report

Use this fixed query set:

```text
virtue
death
reason
soul
justice
nature
emperor
"human nature"
```

Measure:

- FTS build time: 3 fresh rebuilds per strategy
- query latency: 5 warmups, then 30 timed top-20 searches per query
- storage: whole DB bytes after `VACUUM`, plus FTS/index bytes from `dbstat` when available
- correctness: indexed row count, top-20 ULID parity against Strategy A, snippet/highlight availability

---

## Strategy definitions

### Strategy A — current-compatible baseline

Keep the v0.2/v0.3-baseline FTS shape:

- `doc_nodes.id INTEGER PRIMARY KEY AUTOINCREMENT`
- `nodes_fts` external content table:

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    content,
    content='doc_nodes',
    content_rowid='id'
);
```

Search path:

```sql
nodes_fts.rowid -> doc_nodes.id
```

### Strategy B1 — ULID document table plus rowid mapping

Build a transformed copy:

- `doc_nodes` has no integer `id`
- `doc_nodes.ulid` is the canonical key
- `parent_id` becomes `parent_ulid`
- add `doc_nodes_fts_map(rowid INTEGER PRIMARY KEY, node_ulid TEXT UNIQUE NOT NULL)`
- create a normal/contentful FTS5 table:

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(content);
```

Populate `nodes_fts.rowid` from `doc_nodes_fts_map.rowid`.

Search path:

```sql
nodes_fts.rowid -> doc_nodes_fts_map.node_ulid -> doc_nodes.ulid
```

### Strategy B2 — ULID document table plus contentless FTS5

Build the same ULID-only `doc_nodes` and mapping table as B1, but use contentless FTS5:

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(content, content='');
```

If native `snippet()` / `highlight()` is unavailable or unusable, measure a manual snippet fallback by retrieving `doc_nodes.content` and extracting a short window around the matched token.

If the local SQLite version rejects the contentless table, record B2 as unsupported and keep the rest of the probe running.

---

## Decision rule

Eliminate any strategy that fails one of these viability checks:

- cannot return stable `node_ulid` results
- cannot maintain top-20 ULID parity with Strategy A for the fixed query set
- breaks snippet/highlight behavior without a proven fallback

Then compare viable B strategies to Strategy A:

- if B1 or B2 is within 10% of A on storage, build time, and query latency, recommend the best viable B strategy
- if both B strategies are more than 10% worse or add unacceptable query complexity, recommend Strategy A

The report must end with exactly one recommendation label:

```text
Recommend A
Recommend B1
Recommend B2
```

---

## Acceptance checklist

The slice is complete when:

- `scripts/probe_v03_fts5_strategies.py` exists in the engine repo
- the script runs successfully against the Meditations cartridge
- unsupported B2, if encountered, is reported without failing the whole probe
- `Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md` exists
- `/private/tmp/lun_v03_fts5_probe/results.json` exists
- the report includes:
  - SQLite version
  - source cartridge path and SHA-256
  - strategy schema summary
  - metric table
  - correctness table
  - final recommendation label
  - follow-up edits needed in `LUN-FORMAT_v0.3.md`

Suggested command:

```bash
python3 scripts/probe_v03_fts5_strategies.py \
  --source "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun" \
  --out-dir /private/tmp/lun_v03_fts5_probe \
  --report Docs/Reports/REPORT_2026-05-22_v03_fts5_strategy_prototype.md \
  --build-runs 3 \
  --query-runs 30 \
  --warmups 5
```

---

## Reporting back

Report:

- final recommendation label
- metric deltas vs Strategy A
- whether snippets/highlights survive
- exact report path
- any production-code implications for the next v0.3 implementation handoff
