# HANDOFF: Cartridge vector reads fail loud on a dimension mismatch

**Date:** 2026-07-26
**Status:** Implemented, tested, smoked — Engine branch `fix/vec-dim-mismatch-guard`
**Continue in:** Claude Code
**Repo:** `_LunaEngine_BetaProject_V2.0_Root` (Engine slice — not this repo)
**Discharges:** SPEC-014 prerequisite, named as follow-up 3 in
`HANDOFF_2026-07-26_figure-vision.md`

## What landed

Both cartridge readers scored embeddings with a local `zip(a, b)` cosine, duplicated
verbatim in `_v02_vec_search` and `_v03_vec_search`. They now share one strict helper that
refuses ragged input.

The defect was **worse than truncation**. `dot` ran over the zipped prefix while `norm_a`
and `norm_b` ran over the *full* vectors — so a 384-dim query against a 512-dim stored
vector produced a dot product over 384 components divided by the 512-vector's complete
norm. Not a partial cosine: an arbitrary depressed float, indistinguishable from a weak
match, and accepted as evidence by everything downstream (`_rrf_fuse`, the `score > 0.01`
liveness filter).

It was also **not purely latent**, contrary to the note that carried it forward. The query
vector is sized by *config* `embedding_dim` (YAML, default 384); stored vectors carry the
*cartridge's* own width from its `meta`. Those two were never cross-checked, so a non-384
cartridge already truncated silently today, with no image vectors anywhere.

## Three faults, not one

Each is fatal on its own and none implies the others — which is why there are three
separate checks and three separate mutation-checked tests:

1. **Stored vs query length.** The cosine is meaningless. This is the original report.
2. **Stored vs `meta.embedding_dim`.** Stored and query can agree with each other while
   both contradict the cartridge's declared width. Comparing only stored-to-query passes
   this, yet the cartridge violates its own LUN-FORMAT MUST and any other reader trusting
   `meta` would mis-slice the same blob.
3. **Malformed blob.** `_blob_to_vector` floor-divides by 4 and lets `struct.error` reach
   the caller — and `struct.error` is **not** a `ValueError` subclass
   (`struct.error.__mro__` is `[error, Exception, BaseException, object]`). Any handler
   catching `ValueError` to report a data fault missed it entirely. New
   `_blob_to_vector_strict` normalises every malformed shape to `ValueError`.

Faults 2 and 3 were found in review of the original plan, which would have shipped with
only fault 1 covered.

## Gotchas that cost real time

- **`struct.error` is not a `ValueError`.** Verified directly, not assumed. Mutation 3
  below reproduces the escape end-to-end.
- **`meta.embedding_dim` cannot be made mandatory.** All three sample cartridges carry it
  and `builder.py:404` always writes it, but hand-built and pre-SPEC-006 cartridges omit
  it — including the fixture in `tests/unit/test_aibrarian_figure_context.py`. Raising on
  absence would be a regression, so absence warns and falls back to the stored-vs-query
  check. Losing one *extra* integrity check is stated out loud rather than assumed.
- **Resolve `meta.embedding_dim` once per search, never per row.** `_cartridge_meta` is a
  SQL round-trip; per-row it would be one query per embedding.
- **Check order determines the error message.** The meta check fires before the query
  check, so a fixture with meta=384 / stored=512 / query=384 reports the *meta* fault. Two
  tests initially asserted the wrong message for that reason — the fixtures, not the code,
  were wrong. Isolating each fault needs a fixture where the other two are satisfied.
- **`ORDER BY rowid` fails on the real `embeddings` table** (`no such column: rowid`).

## Why the ERROR log is load-bearing

`tools/dataroom_tools.py:120` catches every per-collection search failure and logs it at
`logger.debug`. That is left alone deliberately (follow-up 1), which makes the guard's own
`logger.error` at the raise site the only thing keeping a corrupt cartridge visible in the
path that actually runs. Smoked against a copy of the Nature-of-Art cartridge with one
vector re-packed to 512 dims, at an INFO logging floor, covering both search types:

```
hybrid   (default)  -> 1 results  [keyword survives]
semantic (explicit) -> 0 results  [nothing to fall back to]
ERROR records at INFO floor: 2 (naming the node: 2)
WARNING 'semantic leg dropped': 1
```

Both halves matter. Hybrid keeps the keyword hit instead of losing the whole collection;
explicit semantic still returns nothing, because there is nothing honest to return. Both
are audible despite the DEBUG swallow.

## Verification

- **23 targeted tests** (6 added after review); 242 in the substrate/figure sweep; 414 in
  the cartridge/aibrarian/nexus/dataroom sweep. One failure, `test_streaming_seam_parity`,
  re-verified pre-existing by stashing the change and re-running.
- **All five guard clauses mutation-checked** — each reverted in turn, each caught by
  exactly its own tests, nothing else:

  | mutation | failing tests |
  |---|---|
  | drop the length raise (restore `zip`) | `cosine_refuses_ragged`, `cosine_refuses_a_strict_prefix`, `v03_stored_query_mismatch` |
  | drop the `meta.embedding_dim` check | `v03_conflicts_with_meta_embedding_dim` |
  | strict decoder → `_blob_to_vector` | `v03_malformed_blob` (as `struct.error`, uncaught) |
  | remove the hybrid fallback | the 4 hybrid tests |
  | route explicit semantic through the fallback | `explicit_semantic_still_raises`, `v03_mismatch_surfaces_through_search` |

- **Behavioural parity on real cartridges.** 90 scored rows — 3 queries × 10 results ×
  {Meditations v0.3, Meditations v0.2, Nature-of-Art} — byte-identical before and after.
  ULIDs, ordering and scores unchanged. The guard is invisible on valid data, which is the
  only way it can be correct. Real blobs, real meta, real node index, real cosine; only
  the query *vector* is a deterministic stub, and that sits entirely upstream of the
  changed code.
- No live backend restart, no plist change, no flag. Pure reader-side correctness.

## What review caught: the guard's blast radius

The first cut failed the whole collection on a bad row. Both hybrid branches
compute `extraction_results` and `kw` *before* calling the vec search, and
nothing inside `_v0X_search` caught the guard's `ValueError` — so one bad row
discarded the already-computed keyword and extraction results too.
`dataroom_search` defaults to `search_type="hybrid"`, so in practice a single bad
row silently removed an entire collection from every default search.

Git history made the case sharper than the code alone. The `sem_alive` filter in
those branches came from `c6834321` with the comment *"Filter dead semantics
(score <= 0.01 = noise from bad embeddings)"* — that branch exists precisely so
bad embedding signal degrades rather than fails. The guard had repurposed it into
a hard failure without noticing.

`_hybrid_semantic_leg` (shared by both readers) now catches the guard's
`ValueError`, logs a WARNING that the *query* degraded — a separate fact from the
ERROR naming the node — and returns an empty semantic list so the existing
fallback takes over. **Only hybrid degrades.** An explicit
`search_type="semantic"` still raises: there is nothing to fall back to, and
returning `[]` would be exactly the silent failure this guard exists to remove.

Two things about this are worth carrying forward:

- **The original smoke used `search_type="semantic"`** and so never exercised the
  path real callers take. It was correct and still under-measured the thing it
  was testing. None of the 17 original tests referenced `hybrid` at all.
- **"Fail loud" and "discard good results" are separable.** Refusing to return an
  invented cosine was the actual goal; keyword hits are not invented and had no
  reason to die with it.

## Named follow-ups

1. **`dataroom_tools.py:120` downgrades every per-collection search failure to DEBUG.**
   The fan-out cannot report *which* collections failed or *how many* — a corrupt
   cartridge silently shrinks the result set. Fix is `warning` plus surfacing a failed-
   collection count to the caller. Left alone here: it is about fan-out observability, not
   vector dimensions, and it changes log volume for every pre-existing failure mode.
2. **`validate_cartridge_open` never inspects `embeddings`.** It checks `application_id`,
   `user_version`, `logprob_base`, `logprob_attribution`, `ledger_hash_algorithm` —
   nothing about vector byte length. So this guard is **search-time only**: a corrupt
   cartridge still opens cleanly and only fails when someone runs a semantic query. If the
   goal becomes "the reader enforces the format MUST", it belongs in open-time validation
   or an fsck path as well. Query-time was a deliberate choice (open-time costs a table
   scan per connection), not an oversight.
3. **Nothing reconciles config `embedding_dim` with `meta.embedding_dim`.** The guard
   turns the divergence into a hard error instead of silent truncation, which is strictly
   better, but a cartridge whose width differs from the collection config is now
   unsearchable rather than wrongly searchable. The real fix is to size the query
   generator from the cartridge's declared width.
4. **The `level` filter is now SPEC-014's to decide** — `_v03_vec_search` still scans
   `embeddings` with no `level` filter. Deliberately untouched: one level population
   exists today, so an allow-list would be dead code written against a schema SPEC-014 has
   not defined.

## Still deferred

Unchanged from `HANDOFF_2026-07-26_figure-vision.md`: SPEC-014 itself (the format fork —
separate table vs new meta keys plus relaxing the MUST vs v0.4), regions, and the parked
GDAL / COG media-family RFC. Vision-only search remains **rejected** as a sole strategy.

## Suggested next session opener

> The SPEC-014 prerequisite is discharged — the readers now refuse a dimension mismatch
> instead of inventing a score. SPEC-014 itself is still paper-first: resolve the
> `embedding_dim` format fork before any code. Follow-up 3 (config vs cartridge width) is
> worth folding into that decision rather than fixing separately.
