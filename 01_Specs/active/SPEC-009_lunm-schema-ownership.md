# SPEC-009: LUNM schema ownership and the table manifest

**Status:** active
**Severity:** high
**Author:** Ahab (with Claude)
**Created:** 2026-07-21
**Last updated:** 2026-07-21
**Affects format version:** LUNM v0.1 (no `user_version` bump — see § Migration path)

---

## Problem statement

Nobody can enumerate a LUNM file's schema. `src/luna/substrate/schema.sql` reads like the schema of record and is treated as such by every audit to date, but it declares 47 of the live matrix's **89** tables. The remaining 42 arrive from twenty-three other files scattered across the engine — including a `migrations/` directory loaded by filesystem path rather than by import, two of whose files nothing executes at all. There is no artifact — in the engine or in this repo — that answers "which tables does a LUNM file contain, and who put them there." SPEC-009 creates that artifact and the rule that keeps it true. It deliberately ratifies **no** table's DDL; that is SPEC-011 and beyond, and it cannot be attempted responsibly until the families are named and owned.

## Observed evidence

All counts taken 2026-07-21 against Luna Engine HEAD `2ed07b0` and the live matrix at `data/user/memory_matrix.lun` (`application_id = 1280659021`, `user_version = 2`).

- **The 2.4× gap.** `schema.sql` contains 47 `CREATE TABLE` statements across 933 lines. `select count(*) from sqlite_master where type='table'` on the live matrix returns **89**.
- **Twenty-four files declare `CREATE TABLE`** against the matrix: `substrate/schema.sql`, `substrate/database.py`, `substrate/aibrarian_schema.py`, `substrate/collection_annotations.py`, `substrate/collection_lock_in.py`, `substrate/vec_fallback.py`, `substrate/images.py`, `substrate/assets.py`, `memory/cluster_manager.py`, `lunascript/schema.py`, `qa/database.py`, `lunafm/spectral.py`, `tuning/session.py`, `services/kozmo/graph.py`, `diagnostics/trace_schema.sql`, `cartridge/schema.py`, `cartridge/migrate.py`, `intergalactic_hub/storage/db.py`, `intergalactic_hub/schema/load_entities.py`, and four numbered files in the engine-root `migrations/` directory.
- **A family split across two owners.** `entities` is declared in `substrate/aibrarian_schema.py`; `entity_relationships`, `entity_mentions` and `entity_versions` are created by `_migrate_entity_relationship_evidence()` and siblings in `substrate/database.py`. One conceptual family, two owners, no declaration of that fact anywhere.
- **DDL loaded by filesystem path, outside the package.** The live matrix contains `ambassador_protocol` and `ambassador_audit_log`. Neither has a `CREATE TABLE` anywhere in `src/luna/`: `_migrate_ambassador_tables()` resolves `project_root() / "migrations" / "004_ambassador_protocol.sql"` (`database.py:1233`) and `executescript`s the file it finds. Any audit that greps the substrate package misses these tables entirely — as this spec's own first draft did.
- **An entire second DDL location, and part of it is dead.** The engine root holds `migrations/`, six numbered `.sql` files declaring 11 tables between them. Only two are referenced by any loader in `src/`, `scripts/` or `intergalactic_hub/`: `001_entity_system.sql` and `006_turn_type.sql`. **`002_conversation_history.sql` (3 tables) and `003_access_bridge.sql` (2 tables) have zero loaders** — five tables of DDL that nothing in the tree executes. Whether they were superseded by `schema.sql`, applied by hand once, or simply abandoned is unrecoverable from the code, which is precisely the condition SPEC-009 exists to end.
- **Nine tables carry no family prefix**: `clusters`, `entities`, `projects`, `protocols`, `quests`, `roles`, `sessions`, `tasks`, `threads`. Prefix is therefore a real convention (17 `ih_`, 11 `memory_`, 7 `task_`, 6 `lunascript_`, 4 `entity_`) but an unreliable one, and cannot serve as an identification mechanism.
- **The gap has already produced a defect in a shipped spec.** [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) § 4.3 originally enumerated ~31 out-of-scope tables against a believed total of ~36. Both numbers were wrong, the enumeration was incomplete on the day it was written, and its Q5 resolution had to delete the list and restate the boundary intensionally. SPEC-008's `lunm.schema_fingerprint` key (Q2) was deferred to this spec for the same reason: a hash of `schema.sql` fingerprints roughly half the file.

## Root cause analysis

Two causes, and they are not the same problem.

1. **DDL ownership was never a decision.** The engine grew subsystems, each of which reasonably created the tables it needed. No rule said where DDL belongs, so it went wherever the subsystem lived. This is not a defect in any individual subsystem — `intergalactic_hub/storage/db.py` owning `ih_*` is a perfectly good local design. The defect is that the *union* is undeclared, so it is unknowable without executing the engine and reading `sqlite_master`.

2. **The schema of record was assumed, not designated.** `schema.sql` looks canonical: it is named like a schema, it is loaded first, and it runs through `executescript` at `database.py:199`. Every audit reached for it. Nothing ever said it was complete, and it never was. An artifact that *looks* authoritative but is not is worse than no artifact, because it stops people looking further — which is exactly what happened to SPEC-008.

## Proposed solution

Three normative rules, plus one enforcement mechanism. No DDL is ratified.

### 4.1 Rule 1 — single declared owner

Every table in a LUNM file MUST have exactly one **owner**: the subsystem responsible for its DDL, identified by module path (e.g. `luna.substrate`, `intergalactic_hub.storage`). Two owners declaring the same table is a defect. A table with no owner is a defect. Where a family is currently split — `entities` versus `entity_*` — SPEC-009 does not mandate which owner wins; it mandates that one be chosen and recorded.

### 4.2 Rule 2 — the manifest

Owners and their tables MUST be enumerable **without executing the engine**. This is satisfied by a checked-in manifest mapping owner → table names → classification. The manifest is a static, reviewable artifact: it diffs in pull requests, it can be read by a spec author, and it does not require a live matrix or an importable engine to consult.

Runtime derivation (walking `sqlite_master`, or a registration API called at boot) is explicitly rejected as the *primary* mechanism — see § Alternatives. A live matrix reflects what one install happens to contain, which is a function of which subsystems loaded on that machine. The manifest states what the schema *is*.

The manifest's location and serialization format are Q1 below.

### 4.3 Rule 3 — every table is classified

Each manifest entry MUST carry exactly one classification:

| Classification | Meaning | Examples |
| --- | --- | --- |
| `format-invariant` | A LUNM file is not a LUNM file without it. The eight tables of [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) § 4.3, and only those, until amended. | `memory_nodes`, `nexus_registry`, `profile_config` |
| `engine-extension` | Present in every normal install; carries no LUNM guarantee. A reader MUST NOT assume it. | `quests`, `tasks`, `topology_clusters` |
| `conditional` | Present only when its subsystem is loaded. Absence is not a defect. | the `ih_*` family, gated on the Hub preload path |
| `vestigial` | Declared but unused, scheduled for removal. Absence in a future version is not a breaking change. | `consciousness_snapshots` — zero readers or writers repo-wide, zero rows |

A fifth state exists in the tree today and is deliberately **not** a classification: DDL that is declared but never executed, as in the two orphaned `migrations/` files. That is not a table class — those tables do not exist in any matrix — it is a source-tree defect, and the manifest surfaces it by having nothing to point at. Q5 disposes of it.

`format-invariant` is the only classification SPEC-008 has already fixed. SPEC-009 MUST NOT add to it: promoting a table into the format contract is a SPEC-008 amendment and a `user_version` question, not a bookkeeping decision made while writing a manifest.

### 4.4 Enforcement

A single test, run against a live matrix:

- every table in `sqlite_master` appears in the manifest → else **fail**, naming the unmanifested table;
- every manifested table appears in `sqlite_master` → else **fail**, unless classified `conditional` or `vestigial`.

This is the whole mechanism, and it is why the manifest must be static: a manifest derived from the database cannot disagree with the database, so it can never catch drift. The test is the artifact that makes Rules 1–3 more than documentation. Whether it runs in CI, at engine boot, or both is Q3.

### Schema changes

None. SPEC-009 adds no tables, no columns, no indexes, and no DDL of any kind. It is a spec about where DDL is declared, not about what DDL says.

### Behavioral changes

No change to any read or write path. Three additions:

1. Author the manifest. This is an audit, not a code change, and its output belongs in `04_Audits/` per repo convention as well as in the engine.
2. Add the § 4.4 conformance test.
3. Resolve the `entities` / `entity_*` ownership split, and give `ambassador_*` a statically-readable declaration — runtime-assembled DDL is compatible with Rule 1 but defeats Rule 2's "without executing the engine."

### Migration path

Forward-compatible; no `user_version` bump. Per [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) § 4.1's ratified bump triggers, declaring ownership changes no table, no contract, and nothing a reader can observe. Every existing matrix is conformant the moment the manifest describes it accurately — that is the manifest's job, not the file's.

## Validation rules

```python
# Pseudocode — the § 4.4 conformance check.
live      = {row.name for row in sqlite_master if row.type == "table"}
manifest  = load_manifest()                     # static, no engine import

unmanifested = live - manifest.keys()
assert not unmanifested, f"tables present but unowned: {sorted(unmanifested)}"

for name, entry in manifest.items():
    if name in live:
        continue
    assert entry.classification in ("conditional", "vestigial"), \
        f"{name} is declared {entry.classification} but absent from the file"
```

SQLite-internal shadow tables (FTS5 `_data` / `_idx` / `_docsize` / `_config`, vec0 `_info` / `_chunks` / `_rowids`) are owned by their parent virtual table, not manifested individually. Q2 covers whether the parent must declare them.

## Governance implications

- **Ledger / annotation events:** N/A. LUNM has no `annotation_ledger`.
- **Multi-axis imprint weights:** N/A. A LUNC reading concern.
- **Actor roles:** N/A at the format level.
- **Cross-cartridge traversal:** Indirect but real. SPEC-008 § 4.4's identity check depends on `profile_config`, which SPEC-009 classifies `format-invariant` while noting it is the one core table whose DDL lives outside `schema.sql` — the condition SPEC-008 Q1 requires fixing before `implemented/`.
- **Memory Matrix integration:** This spec is the precondition for every future LUNM schema statement. SPEC-011+ ratify DDL per family; `lunm.schema_fingerprint` becomes definable as a per-owner hash, so an IH schema change no longer invalidates the substrate's fingerprint. SPEC-009 specifies that shape and hands the value back to SPEC-008 Q2's deferred key.

## Alternatives considered

- **(a) Central declaration — all matrix DDL MUST live in `schema.sql`.** The cleanest invariant, and rejected. It would move DDL away from the subsystems that own it, inverting a module boundary that is otherwise sound, and it requires editing nineteen files before the first benefit arrives. It also cannot express `conditional`: a table that exists only when the Hub loads does not belong in a file that always runs.
- **(b) Registration API — DDL lives anywhere but registers through one call at boot.** Rejected as the primary mechanism. It yields a runtime inventory, which cannot be consulted by a spec author, cannot be diffed in review, and — decisively — cannot catch a table that failed to register, because an unregistered table is invisible to exactly the mechanism meant to find it. Worth adding later as a *secondary* check; not a substitute for a static manifest.
- **(c) Prefix convention as the identification mechanism.** Rejected on the evidence. Nine tables carry no prefix, and `entities` versus `entity_*` shows the same prefix spanning two owners. Prefix survives in this spec as a recommended convention for *new* tables (Q4), not as a way to determine ownership of existing ones.
- **(d) Ratify all 89 tables' DDL now.** This is SPEC-009's original scope per the 2026-07-20 ledger, and it is rejected for the same reason SPEC-008 rejected it at ~700 lines — with the evidence now materially worse than SPEC-008 believed. Ratifying DDL requires knowing which DDL is authoritative, and for at least one family that question is currently unanswerable by reading source.

## Open questions

Each Q below blocks `active → accepted`.

1. **Manifest location and format.** Candidates: a TOML/YAML file in the engine beside `schema.sql`; a Python module with a typed structure; a table *inside* the matrix. **Recommendation:** a checked-in declarative file in the engine, mirrored into `04_Audits/` as a dated snapshot when SPEC-009 is accepted. A table inside the matrix is rejected on its face — it would make the manifest subject to the very drift it exists to detect.

2. **Do virtual-table parents declare their shadow tables?** FTS5 and vec0 generate 4–5 shadow tables each; the live matrix carries at least nine. Listing them individually is noise, but a bare exclusion rule means a genuinely unowned `foo_data` table would be silently tolerated. **Recommendation:** the manifest declares parents only, and the § 4.4 check derives expected shadow names from each parent's type, so an unowned table matching no parent still fails.

3. **Where does the conformance test run?** CI needs a matrix to check against, and the only real one is a developer's live profile. **Recommendation:** CI runs it against a freshly-created matrix (which by construction excludes `conditional` families), and the engine runs it at boot in a development mode against the real file. The two together cover both directions.

4. **Is the prefix convention normative for new tables?** Making it a MUST is cheap for new work and would have prevented the `entities` / `entity_*` split. But nine existing tables violate it, and SPEC-009 should not declare a MUST that the shipped schema breaks — the mistake SPEC-008 § 4.4 had to correct. **Recommendation:** SHOULD for new tables, with the nine existing exceptions recorded in the manifest rather than grandfathered by silence.

5. **What is the status of the `migrations/` directory?** It is a second DDL location, path-loaded rather than imported, and at least two of its files are dead. Options: (a) declare it a legitimate owner and manifest its files like any other; (b) fold the live files into their owning subsystems and delete the dead ones; (c) keep it for historical record but mark every file applied-or-superseded. **Recommendation:** (b) for `004_ambassador_protocol.sql`, whose tables plainly belong to an owner, and (c) for the rest — but this needs a human who remembers whether `002` and `003` ever ran against a production matrix. Deleting DDL that silently shaped a live file is not a decision to make from the code alone.

## Dependencies

**Upstream (must be accepted):**

- **[SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md)** (accepted 2026-07-21) — supplies the `format-invariant` set that SPEC-009's classification scheme takes as fixed, and the intensional boundary rule that SPEC-009 makes checkable. SPEC-009 is the direct consequence of SPEC-008's Q5 resolution.

**Downstream:**

- **SPEC-010** — LUNM migration discipline. Its tiered fail-loud rule keys off SPEC-009's classification: a migration touching a `format-invariant` table may not fail silently. SPEC-010 cannot be implemented before SPEC-009's classification exists.
- **SPEC-011+ (future)** — per-family DDL ratification, one spec per owner, against the map SPEC-009 produces.
- **SPEC-008 Q2** — `lunm.schema_fingerprint` was deferred here. SPEC-009 specifies its shape; the key itself lands as a SPEC-008 amendment.

## Implementation notes

(Filled in when status moves to `implemented`.)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
