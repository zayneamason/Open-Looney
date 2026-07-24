---
doc_type: breakdown
status: active
created: 2026-07-24
updated: 2026-07-24
description: The .lun cartridge format (LUNC) — v0.1 through v0.3, identity contract, table evolution, what's stored vs. not, the annotation ledger in detail
tags:
  - lunc
  - cartridge-format
  - breakdown
---

# The `.lun` cartridge format family (LUNC)

Second in this wiki's `sections/` — same shape as
[`lunm-runtime-matrix.md`](lunm-runtime-matrix.md): **definition →
classification → what's stored vs. not → per-item detail → citations**.

Two kinds of claim appear below. **(a)** facts already on record in a format
spec or SPEC file, cited by document and section. **(b)** facts that exist
only by querying a live sample cartridge — dated, reproducible, and specific
to that one file. §6 states exactly how to reproduce every §6 number.

## 1. Definition

A `.lun` cartridge is a portable, read-optimized knowledge unit: a single
SQLite file distributed standalone, immutable once finalized, queryable
without Luna-specific tooling. It is the sibling family to the runtime
matrix (`LUNM` — see [`lunm-runtime-matrix.md`](lunm-runtime-matrix.md)):
same extension, same SQLite container, disjoint schema, and a fundamentally
different lifecycle — a cartridge is built once and shipped; a matrix is
mutated in place for the life of a profile and never shipped. *(a: `00_README/README.md` §Scope; `LUN-FORMAT_v0.1.md` §Overview)*

**The identity contract is not uniform across versions — this is a common
misreading.** `application_id = 0x4C554E43` (`'LUNC'`, decimal
`1280659011`) is the cartridge family value, but it is **required starting
at v0.2, not present in v0.1 at all**:

> "Application ID (cartridge family): `0x4C554E43` (`'LUNC'`). Not set in
> v0.1 builds; established as a required contract in v0.2 (SPEC-006)."
> — `LUN-FORMAT_v0.1.md:29-30`

A v0.1 cartridge is a legacy pre-contract artifact, not an early example of
the identity contract — it has neither `application_id` nor `user_version`
set, and cannot be distinguished from an arbitrary SQLite file by pragma
alone. It must be migrated (`python -m luna.cartridge.migrate <path>`)
before any v0.2+ reader will open it. From v0.2 onward the family value is
stable — v0.3's spec states explicitly "Unchanged from v0.2"
(`LUN-FORMAT_v0.3.md:68`) — and only `user_version` moves between minor
versions. *(a: `LUN-FORMAT_v0.1.md:29-32`, `LUN-FORMAT_v0.2.md:36-38`,
`LUN-FORMAT_v0.3.md:68-70`)*

## 2. Version identity

| Version | `user_version` | `meta.format_version` | Status | One-line change |
|---|---|---|---|---|
| v0.1 | unset | `schema_version=1` (int, different key) | Historical | Reverse-engineered from the Lansing cartridge; six tables, no identity contract, hardcoded confidence |
| v0.2 | `2` | `'0.2'` | Historical (superseded) | `application_id` contract required; SPEC-001 anchor taxonomy; SPEC-002 ULID identity (additive, alongside integer rowid); SPEC-003 raw LLM signals replace hardcoded `confidence` |
| v0.3 | `3` | `'0.3'` | **Shipping** | SPEC-005 append-only annotation ledger with SHA-256 hash chain; SPEC-002 D5 integer-rowid removal (`extractions` → `WITHOUT ROWID` + ULID PK); C-01 `claim_sources` → `extraction_sources` rename |

*(a: version headers of all three format specs; `LUN-FORMAT_v0.3.md:39-61`
§"Three change axes")*

## 3. Table classification

LUNC has no format-invariant/engine-extension split — that's a LUNM/SPEC-009
concept for a substrate with dozens of DDL owners. A cartridge has one
owner (`luna.cartridge.builder`) and a small, fully-specified schema. The
useful axis here is **first-class vs. FTS5 shadow vs. version-scoped**:

- **First-class, present in every version, evolving shape:** `meta`,
  `doc_nodes`, `extractions`, the anchor bridge table (`claim_sources` in
  v0.1/v0.2, renamed `extraction_sources` in v0.3), `embeddings`.
- **FTS5 + shadow**, unchanged in shape across all three versions:
  `nodes_fts` plus its four shadow tables (`nodes_fts_data`,
  `nodes_fts_idx`, `nodes_fts_docsize`, `nodes_fts_config`).
- **`nexus_refs` — present from v0.2, but its meaning changes at v0.3.**
  This is not a v0.2-only table. In v0.2 it is a forward-compatibility
  placeholder, created empty: "v0.2 readers should not display this table;
  it becomes meaningful when SPEC-005 ships" (`LUN-FORMAT_v0.2.md:427-428`).
  In v0.3 it is **active** — `cartridge_imported` ledger events make
  cross-cartridge promotion observable, and `nexus_refs` is
  `promote_to_nexus()`'s destination table (`LUN-FORMAT_v0.3.md:401-405`).
  The DDL is unchanged across the boundary; only the status changes. §6
  confirms live that the reference cartridge carries the table with 0 rows
  — consistent with "active, but this cartridge was never promoted," not
  with "the feature doesn't apply yet."
- **v0.3-only, additive:** `annotation_ledger`, `annotation_actors`
  (SPEC-005); `sketches` (SPEC-007 — itself an amendment *within* v0.3, so
  cartridges built before that amendment landed have no `sketches` table at
  all; readers must tolerate its absence).

*(a: `LUN-FORMAT_v0.2.md` §"`nexus_refs`"; `LUN-FORMAT_v0.3.md` §"`nexus_refs`"; `LUN-FORMAT_v0.3.md` §"`sketches`")*

## 4. What's stored vs. not

**Confidence → raw signals (SPEC-003).** v0.1's `extractions.confidence`
was a hardcoded constant — every value was either `0.85` or `0.9`, set
unconditionally by extraction type, carrying no actual information
(`LUN-FORMAT_v0.1.md:113-115`). v0.2 drops the column entirely and replaces
it with `anchor_status` (categorical), `extraction_method` (provenance), and
`llm_logprob_sum`/`llm_token_count` (LLM-call-scope uncertainty). The
cartridge stores raw signals only — composition into a trust score is
explicitly application-layer (SPEC-004), never in the file.

**The anchor taxonomy (SPEC-001), and what it deliberately excludes.** Every
`claim`/`summary` extraction in a v0.2+ cartridge must carry a classified
`anchor_status` — `unknown` is a build failure for those two types
(`validate_anchors()` rejects it). Entities are explicitly scoped out:
"Entity rows are scoped out of SPEC-001 classification and carry
`anchor_status = 'unknown'` legitimately" (`LUN-FORMAT_v0.2.md:210-211`). §6
confirms this distinction holds exactly on a live file, not just as a
written rule.

**The C-01 naming defect, and what "fixing" it actually meant.** Despite the
v0.2 column name `claim_id`, `claim_sources` anchored both `claim` and
`summary` extractions — the "claim" naming was never accurate, just
historical. v0.3 renames the table to `extraction_sources` and the column to
`extraction_ulid` to say what it actually does. **This means any prose
(including elsewhere in this wiki) that says "`claim_sources` anchors
claims" is describing v0.1/v0.2 only — the v0.3 table anchors both kinds by
design, not as an edge case.**

**Not stored, by design — the ledger's honesty about its own limits.** The
append-only enforcement on `annotation_ledger` is a **soft covenant**, not a
hard guarantee, and the format spec is explicit about this rather than
rounding it up to "immutable":

> "These are a soft covenant: an admin with `sqlite3` CLI access can still
> bypass them via `PRAGMA writable_schema = ON`, `DROP TRIGGER`, or schema
> surgery. The covenant raises the cost and visibility of tampering; it
> does not make tampering impossible." — `LUN-FORMAT_v0.3.md:500-504`

This breakdown carries that same framing forward rather than overstating
what the hash chain proves.

*(a: `LUN-FORMAT_v0.1.md:113-115`; `LUN-FORMAT_v0.2.md:190-211`;
`LUN-FORMAT_v0.3.md:246-250`, `:500-504`; SPEC-001, SPEC-003, SPEC-004,
SPEC-005 — all in `01_Specs/implemented/`)*

## 5. Per-item detail: the annotation ledger (SPEC-005)

The ledger is v0.3's largest structural addition and the one most future
readers will need explained, so it gets the most space here.

**Hash chain.** Each row's `entry_hash` is the SHA-256 of a canonical
pipe-joined 10-field serialization (`seq`, `entry_ts`, `event_type`,
`actor_id`, `actor_role`, `target_kind`, `target_ulid`,
`target_cartridge_ulid`, `payload`, `prev_hash` — NULL fields serialize as
empty string, `payload` taken exactly as stored, no re-serialization at hash
time). `prev_hash[N] == entry_hash[N-1]` for every row after the genesis.
Algorithm is locked at genesis via `meta.ledger_hash_algorithm = 'sha256'`
and immutable for the cartridge's lifetime — a future hash-algorithm change
is a new format version, not an in-place upgrade. *(a: `LUN-FORMAT_v0.3.md:506-547`)*

**Genesis row and the system-actor sentinel.** Every ledger begins with one
`event_type='meta'`, `actor_role='system'`, `prev_hash IS NULL` row, whose
payload records the cartridge's identity at genesis. The system actor's ID
is a fixed sentinel: `'00000000000000000000000000'` (26 zero characters).
This passes the ordinary ULID format CHECK — `0` is a valid Crockford Base32
character — without needing a format exemption, and cannot collide with a
real ULID: the first 10 characters of a real ULID encode a unix-millisecond
timestamp, so an all-zero ULID encodes `1970-01-01T00:00:00Z`, a moment no
real build produces. *(a: `LUN-FORMAT_v0.3.md:565-576`)*

**Eight event types**, full contracts in `SPEC-005_payload-schemas.md`:
`claim_anchored`, `claim_disputed`, `claim_filtered`, `claim_reconciled`,
`summary_overridden`, `cartridge_reviewed`, `cartridge_imported`, `meta`
(chain-internal — genesis and migration events). Five actor roles gate which
events an actor may write: `owner`, `ambassador`, `elder`, `oracle`,
`system` — and the `system` role is CHECK-constrained to `meta` events only
(`LUN-FORMAT_v0.3.md:474`, `actor_role != 'system' OR event_type = 'meta'`).

**The FTS5 reattachment decision (Q1), because the numbers are the
argument.** v0.3 removes integer rowids everywhere *except* `doc_nodes.id`,
which survives solely because FTS5 external-content mode requires an
INTEGER `content_rowid`. Two alternatives that would have removed it too
were prototyped against the Meditations corpus and both failed a 10%
overhead threshold: a rowid-mapping table variant added `+187.3%` storage
and `+51.7%` build time; a contentless-FTS5 variant added `+41.8%` storage
and required manual snippet fallback. The chosen approach (Strategy A) costs
one vestigial integer column that application code never references — every
cross-table foreign key in v0.3 targets `doc_nodes.ulid`, never `.id`. *(a:
`LUN-FORMAT_v0.3.md:1040-1063`, §"Open questions" Q1)*

## 6. Evidence appendix — fresh, dated, reproducible

Run against `07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun` (the
canonical v0.3 reference cartridge) on **2026-07-24T05:34:06Z**. Unlike
LUNM's live, mutating matrix, this is a finalized, shipped cartridge — these
numbers are expected to be stable indefinitely, but are still stated as a
verified snapshot rather than assumed, and every one is a cross-check
against another number in the same file, not a bare count.

```
$ sqlite3 Marcus-Aurelius-Meditations.v03.lun "select * from pragma_application_id;"
1280659011
$ sqlite3 ... "select * from pragma_user_version;"
3
$ sqlite3 ... "select value from meta where key='format_version';"
0.3
```

Identity: `application_id`, `user_version`, and `meta.format_version` all
agree (`1280659011` / `3` / `'0.3'`).

**Coverage cross-check.** `doc_nodes` by type: `document` 1, `paragraph`
310, `section` 166, `sentence` 3326 — sums to 3803, matching
`meta.node_count = 3803` exactly, and matching `select count(*) from
nodes_fts` (3803) exactly. Full FTS coverage on this file.

**`extractions` structure.** `WITHOUT ROWID`, `ulid TEXT PRIMARY KEY`,
confirmed via `sqlite_master`'s stored DDL (not assumed from the spec).
2767 rows: 1204 `claim`, 1418 `entity`, 145 `summary`.

**Anchor-status cross-check, two ways.** `anchor_status` breakdown:
`anchored` 1201, `match_failed` 148, `unknown` 1418. The `unknown` count
(1418) equals the `entity` count (1418) exactly — live confirmation that
"entities legitimately carry `unknown`" is not just written policy.
`anchored` + `match_failed` = 1349 = `claim` (1204) + `summary` (145)
exactly — live confirmation that no `claim` or `summary` row ships
`unknown` on this file.

**`extraction_sources` cross-check.** 1201 rows — exactly equal to the
`anchored` count above, confirming the spec's "1:1 in practice" note holds
here, not just as a design allowance for the (unused) many-to-many shape.

**Ledger.** `annotation_ledger` has exactly 1 row: `seq=1`,
`entry_hash` length 64, `prev_hash IS NULL`. This reference cartridge has
never received a post-build annotation event — genesis only.

**Migration completeness cross-check.** `sqlite_sequence` lists exactly two
rows: `doc_nodes` and `annotation_ledger`. No `extractions` entry — live
confirmation that the integer-rowid removal (SPEC-002 D5) fully completed
on this file; per the v0.3 validation checklist, an `extractions` entry
here would indicate an incomplete migration.

**Sketches cross-check.** Four kinds present: `entity_surface`,
`extraction_ulid`, `fts_term`, `node_ulid` — matching
`meta.sketches_present = 'entity_surface,extraction_ulid,fts_term,node_ulid'`
exactly.

**`nexus_refs`, live.** Present, 0 rows — consistent with §3's classification
(active in v0.3, simply unused by this cartridge, not absent-by-version).
