# HANDOFF: Enrichment hits promote to figure results (SPEC-013)

**Date:** 2026-07-26
**Status:** Implemented, tested, merged — Engine PR [#173](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/173) / `b7c02cff`
**Continue in:** Claude Code
**Repo:** `_LunaEngine_BetaProject_V2.0_Root` (Engine slice — not this repo)

## Where cartridge retrieval actually lives

Worth knowing before touching this again: `.lun` cartridges are **not** read
through `luna.cartridge` at inference. That package's provenance-aware reader API
(`resolve_source_ref`, `list_extractions`) has **zero runtime callers** — tests
only. The live path is a parallel implementation in
`src/luna/substrate/aibrarian_engine.py` (`_v03_*` family), reached from
`AiBrarianConnection._connect_lunc()`. RRF lives there too (`_rrf_fuse`).

The Reader prototype has neither an assembler nor RRF, so "assembler/RRF" work is
Engine work.

## What landed

`_ensure_v03_extraction_fts` indexes every extraction type, and
`_v03_extraction_search` returned each hit as itself. But `figure_discourse.content`
is by construction `"before: <prev paragraph>\n\nafter: <next paragraph>"` — a
verbatim copy of text `nodes_fts` already indexes.

Measured on `07_Sample_Cartridges/The-Nature-Of-Art-And-Creativity.lun`:
**29,590 chars** of `figure_discourse` content against **44,503 chars** of all
sentence nodes. So a query near a figure returned the same sentence twice — once
as a node, once inside an unattributed blob occupying one of the `limit // 2`
slots reserved for extractions.

Enrichment rows now stay matchable, but a hit **resolves through
`extraction_sources` to the figure it anchors** and is emitted as a figure result:

```
[figure] "Fig 7. Colour wheel [kind: diagram] [shows: …] [context: <neighbour prose>]"
```

| Function (`aibrarian_engine.py`) | Role |
|---|---|
| `_v03_promote_figure_extraction` | enrichment hit → figure result; deduped by figure ULID, best score wins |
| `_v03_figure_snippet` | caption + kind + description + context; returns `None` when the figure has no enrichment |
| `_v03_figure_neighbours` | **first runtime read of `extraction_context_nodes`** anywhere in the Engine |
| `_v03_attach_figure_context` | same treatment for figures matched directly by caption, applied post-fusion |
| `_v03_search` | cross-path dedupe — a figure found both ways appears once |

`FIGURE_ENRICHMENT_TYPES` in `luna/cartridge/media.py` is the shared reference
point, with a drift test pinning it to what the writers actually write.

## Why promotion, not exclusion — read this before "simplifying" it

The first design excluded these types from the extraction FTS. Review caught that
this removes neighbour prose from retrieval entirely: a figure could then only be
*decorated* after being found by its own caption, never *found* by its context.
It also deletes the only searchable handle on `media_classification`, whose kind
is derived partly from `src_hint`, and so is often the sole route to a figure
whose caption never says "diagram".

The problem was never that enrichment rows are matchable. It is that they were
returned **as themselves**. Two tests encode this and will fail if anyone
reintroduces exclusion:

- `test_discourse_only_query_surfaces_the_figure`
- `test_classification_only_query_surfaces_the_figure`

## Honest numbers

Neighbour prose is capped at 200 chars/side. On that cartridge **21 of 26**
neighbours exceed the cap, cutting carried context **29,408 → 4,943 chars (−83%)**.

But the reduction is **not uniform per query**. On the queries probed, residual
overlap was *unchanged at 120 chars* — those happened to hit one of the 5 short
neighbours. A promoted figure still restates part of a paragraph that may be
returned separately, because that context is what makes the figure intelligible.
Bounded, not eliminated.

## Verification

- 92 targeted tests, 449 in the wider cartridge/aibrarian sweep. One failure,
  `test_streaming_seam_parity`, verified pre-existing on unmodified `src`.
- **Every new test mutation-checked.** 7 of 12 fail against the unmodified
  engine. The three that pass either way are deliberate non-regression controls.
  The two whose value that did not prove — dedupe and score-ordering — were
  verified by mutating those specific blocks.

Probe (read-only, drives `AiBrarianEngine` directly) kept at
`scratchpad/probe_figctx.py`; re-create against any cartridge with figures.

## Gotchas

- **`score` is `abs(bm25 rank)` — larger is better.** bm25 returns negative and
  more-negative-is-better, so the `abs()` inverts the intuition. Getting a
  comparison backwards here is *silent*: the row still appears, just mis-ranked.
  Cost one real bug in review; `test_collapsed_figure_keeps_the_best_match_score`
  now guards it.
- `_v03_figure_neighbours` falls back to a **full `doc_nodes` load** when passed
  no node index. Both call paths must thread one through or you get an N+1 table
  scan per promoted figure.
- Page-image figures (PR #172) carry zero enrichment by design, so they are never
  promoted and pass through with their snippet untouched.
- `category="figure"` is a new result-vocabulary value. `_canonical_nexus_type`
  passes unmapped types through as non-promotable, so figures correctly stay out
  of Nexus promotion — do not "fix" that by adding a mapping.

## Named follow-ups

1. **Extractions never enter RRF.** `limit // 2` is reserved unconditionally and
   results are concatenated, so `claim`/`summary` are ranked against each other
   but never against nodes. Promoted figures stay inside that reservation by
   decision; reworking it affects every cartridge and every extraction type.
2. Residual context overlap, bounded by the cap (see Honest numbers).
3. `_v03_ext_fts_built` caches the TEMP index per connection — a long-lived
   connection keeps a stale index until restart.
4. Semantic results silently dropped when all scores ≤ 0.01, with no log.
5. Two RRF implementations disagree: unweighted in `aibrarian_engine` vs weighted
   0.6/0.4 in `memory.py`.
6. `_v03_vec_search` loads every embedding into Python for pure-Python cosine.
7. `luna.cartridge`'s provenance-aware reader API is unreachable at inference;
   `anchor_status` and logprob fields ride through result dicts and are dropped.

## Still deferred (agreed order)

1. **Vision embeddings / richer `visual_description`; regions; style tags; GDAL /
   COG RFC** — next up.
2. SPEC-012 Engine WP0+ (LUNM entity unification) — parallel track.

## Suggested next session opener

> Pick up vision embeddings for figures. Note there is currently **no cartridge →
> vision-turn path at all**: `PromptRequest` has no image field, and the existing
> vision path (`engine.py`, forced-delegated on `images=`) is for user-uploaded
> images only. Figures reach the prompt as text today and nothing else.
