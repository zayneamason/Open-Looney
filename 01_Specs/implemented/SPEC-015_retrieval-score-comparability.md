# SPEC-015: Retrieval score comparability across result kinds

**Status:** implemented
**Severity:** high
**Author:** Ahab
**Created:** 2026-08-15
**Last updated:** 2026-08-19
**Affects format version:** none — reader/retrieval contract only, no schema change
**Promoted from:** SPEC-014 Named follow-up 1
(`01_Specs/implemented/SPEC-014_vision-embeddings.md:371-391`)

---

## Problem statement

A cartridge search response is a single flat list in which the `score` key is
populated from **two unrelated numeric scales**. Extraction rows carry
`abs(bm25)` magnitudes in the single digits; node rows carry reciprocal-rank
fusion magnitudes in the hundredths. The two are concatenated into one list and
sorted together by downstream consumers, so extraction rows structurally dominate
any ranking that re-sorts, regardless of relevance. Nothing in the response tells
a consumer that the numbers are not commensurable.

## Observed evidence

Two independent measurements, three weeks and two cartridges apart, both against
the live backend.

**2026-07-26**, `cartridge.Marcus-Aurelius-Meditations.v03`, recorded in SPEC-014:

```
extraction:entity  5.1933542272
hybrid             0.0163934426
```

**2026-08-15**, `cartridge.Dragon-Hatchling.v03`, via `POST /api/nexus/search`,
query `"Hebbian learning synaptic plasticity"`:

```
1. [entity]     13.2932   extraction:entity   Synaptic plasticity [concept]
2. [entity]     13.2932   extraction:entity   synaptic plasticity [concept]
3. [cell]        0.0082   hybrid              Brain Models | synaptic state ...
4. [paragraph]   0.0082   hybrid              8.2 Implications for brain science
```

The two measurements are consistent rather than merely similar. `1/(k+1)` at
`k=60` is `0.0163934`; SPEC-014's figure is a document matching **both** legs
(`0.0328 / 2`), and the 2026-08-15 figure is a document matching **one of two**
(`0.0164 / 2 = 0.0082`). The scale gap is structural, not query-dependent.

Three of four top slots in the second sample are extraction rows, and slots 1–2
are the same concept differing only in capitalisation.

## Root cause analysis

**Primary cause: concatenation presented as ranking.** `_v03_search` ends

```python
return extraction_results + node_results
```

(`src/luna/substrate/aibrarian_engine.py:2036`). `node_results` have passed
through `_rrf_fuse`, which divides summed reciprocal ranks by the non-empty leg
count and relabels rows `"hybrid"`. `extraction_results` come from
`_v03_extraction_search`, which sets `"score": abs(row["rank"])` directly from
the FTS5 bm25 rank. No transform reconciles them. `_v02_search` has the same
shape.

The ordering may well be deliberate — extraction-first is a defensible product
choice. **The defect is not the order, it is the emitted number.** Because both
families publish under one `score` key with no discriminator, the response cannot
be thresholded, weighted, blended, or re-sorted by any consumer without silently
privileging one family.

**Amplifying cause: a cross-collection re-sorter already exists.**
`src/luna/tools/dataroom_tools.py:124`:

```python
all_results.sort(key=lambda r: r.get("score", 0), reverse=True)
```

This is the default fan-out path. It sorts merged results from multiple
collections on the raw `score`, so in any multi-collection search every
extraction row from every cartridge outranks every prose row from every
cartridge. This is the same class of distortion `_rrf_fuse`'s leg-count
normalisation was introduced to prevent, one layer up and unaddressed.

**Not caused by SPEC-014.** SPEC-014 normalised across *legs* because its own
change would otherwise have added a second distortion. It explicitly recorded
this one as pre-existing and out of scope, precisely so its normalisation would
not be read as having fixed global scoring.

**Latent hazard worth recording now.** Three sites filter a leg with
`score > 0.01` (`aibrarian_engine.py:1161`, `:1603`, `:1998`). Each currently
runs on the semantic leg **before** fusion, against raw cosine, where the
constant is sound. But fused scores are `0.0082`–`0.0164` — *below or barely
above* that threshold. Any refactor that moves such a filter after fusion, or
reuses the constant in a new post-fusion path, would discard most or all hybrid
results while extraction rows sailed through. The constant and the fused range
overlap by accident, and nothing marks the boundary.

## Proposed solution

Adopt **Option B+** for the first implementation: keep the flat response and
legacy `score` field for compatibility, but add explicit score-domain metadata
and fix known consumers so they never compare extraction BM25 and fused node
RRF as one numeric domain.

**A. Make extraction a fusion leg.** Feed extraction results into `_rrf_fuse`
alongside keyword/semantic/vision instead of concatenating afterwards. Most
principled: one scale by construction, and `_rrf_fuse` already handles N legs and
normalises. Cost: it abolishes guaranteed extraction-first ordering. If that
ordering is deliberate policy, this is a product regression disguised as a
scoring fix.

**B+. Add score-domain metadata, forbid cross-rank sorting.** Emit explicit
metadata on every result row:

- `rank_class`: `"extraction"` or `"node"`.
- `score_basis`: `"fts5_bm25_abs"`, `"rrf_normalized"`, `"cosine"`, or
  `"unknown"`.
- `score_comparable_scope`: `"within_rank_class"`.

Then update `dataroom_tools.py:124` and prompt-assembly aperture normalization
so they respect `rank_class` before sorting, thresholding, or max-scaling.
Extraction-first ordering remains the compatibility policy; the fix is to make
the score domain explicit and stop downstream code from treating all `score`
values as comparable.

**C. Change the response shape.** Return `{"extractions": [...], "nodes": [...]}`
rather than a flat list. Follows the house preference for making invalid states
unrepresentable rather than checking for them: a consumer *cannot* accidentally
sort across families because they are never in the same list. Cost: a breaking
change to every caller of `search()` and `/api/nexus/search`.

**D. Per-query min-max normalisation of each family into [0,1].** Superficially
simplest. **Recommend against**, and the reason should be recorded so it is not
re-proposed: per-query normalisation is unstable across a fan-out. A collection
returning one weak result normalises that result to `1.0`, so the worst hit in a
thin collection ties the best hit in a rich one. It converts a visible scale
mismatch into an invisible relevance inversion.

### Schema changes

None. This is a reader/retrieval contract change; no cartridge is rewritten and
no `user_version` moves.

### Behavioral changes

The implementation must touch `_v02_search`, `_v03_search`, the standard search
path, `_rrf_fuse`, `dataroom_tools.py`, and prompt-assembly aperture
normalization. `/api/nexus/search` and MCP `aibrarian_search` remain
backward-compatible because they return or render the same flat rows with
additive metadata. `search_entity()` is not in scope: it queries the YAML
`documents` table and does not mix extraction rows with fused node rows.

### Migration path

Forward- and read-compatible for cartridges — nothing on disk changes. Option C
is a breaking change for API consumers and would need a deprecation window;
options A and B are not.

## Validation rules

```python
# Regression guard:
# mixed rank classes may coexist only when rows declare score-domain metadata.
results = search(collection, query, "hybrid", limit=20)
classes = {r["rank_class"] for r in results}
assert classes <= {"extraction", "node"}
assert all(r["score_comparable_scope"] == "within_rank_class" for r in results)

# Consumer guard:
# no downstream consumer globally sorts or normalizes extraction and node rows
# together on legacy `score`.
assert not global_score_sort_across_rank_classes(results)
```

A live-backend reproduction against a mounted cartridge is required before and
after. Per SPEC-014's own history, the seam defects in this area were invisible
to green unit tests and surfaced only in whole-branch review and live probes.

## Governance implications

- **Ledger / annotation events:** N/A. Scoring is derived, not asserted.
- **Multi-axis imprint weights:** N/A.
- **Actor roles:** N/A.
- **Cross-cartridge traversal:** directly implicated. The defect's largest blast
  radius is the multi-collection fan-out in `dataroom_tools.py`, which is the
  default path.
- **Memory Matrix integration:** prompt assembly does consume these scores when
  `LUNA_APERTURE_SCORE_NORMALIZE=1`. Engine live evidence from 2026-08-19 showed
  aperture Pass 2 max-scaling extraction BM25 scores with hybrid RRF scores, so
  hybrid prose can fail the chunk floor solely because an extraction row set
  `turn_max`.

## Alternatives considered

**Do nothing; document the convention.** Rejected: the convention is already
undocumented and already violated by `dataroom_tools.py:124`, which is in the
default path rather than an edge case.

**Fix only `dataroom_tools.py`.** Rejected as treating the one known consumer
rather than the contract. The next consumer inherits the same trap.

**Fold this into SPEC-014.** Explicitly rejected by SPEC-014 itself, which
records that reconciling scale across result *kinds* "needs its own spec"
because it touches every collection and every caller.

## Acceptance decisions

1. **Extraction-first ordering is preserved for compatibility.** The first fix
   must not make extraction a fusion leg.
2. **Prompt assembly consumes these scores.** Severity is high because the defect
   can shape Luna's context window, not only a tool result list.
3. **Use B+ now, defer partitioned responses.** A partitioned v2 response may be
   designed later, but this slice keeps the existing flat shape.
4. **Sibling paths:** `_v02_search`, `_v03_search`, and the standard search path
   all need score-domain metadata. `search_entity()` is clean for this defect.
5. **The `score > 0.01` semantic liveness filters may remain only as pre-fusion
   cosine filters.** They need comments or tests that prevent reuse as
   post-fusion thresholds.

## Dependencies

- **SPEC-014** — implemented. Establishes leg-count normalisation inside
  `_rrf_fuse` and records this defect as its Named follow-up 1.

## Non-goals

1. **No change to `_rrf_fuse`'s leg normalisation.** SPEC-014 settled that and it
   is correct.
2. **No new ranking model.** This spec makes existing scores comparable; it does
   not propose better relevance.
3. **The extraction-leg case-fold dedup miss is out of scope.** Observed
   2026-08-15: `Synaptic plasticity [concept]` and `synaptic plasticity [concept]`
   returned as separate rows at an identical `13.2932`. Real, but a dedup defect
   rather than a scoring one — same family as the known `entities` id-space
   fragmentation, where the rule is to join by lowercased name rather than id.
   Fix it separately; do not let it enlarge this spec.
4. **No cartridge rebuild or backfill.**

## Implementation notes

Implemented in Luna Engine commit `ae3a3c6` (`fix(retrieval): declare score
domains across search consumers`) on 2026-08-19.

- Added backward-compatible `rank_class`, `score_basis`, and
  `score_comparable_scope` metadata to v0.2, v0.3, vision, standard, and RRF
  search rows while preserving flat ordering, `search_type`, and legacy `score`.
- Updated dataroom fan-out to preserve extraction-first grouping and sort only
  within compatible rank classes.
- Updated aperture normalization to compute `turn_max` from node rows only;
  extraction rows remain renderable context and cannot suppress RRF chunks.
- Added contract, API-shape, dataroom, RRF, v0.2/v0.3, and aperture regression
  tests. Focused verification passed: 160 tests.
- Live after-probe against `data/user/cartridges/Dragon-Hatchling.v03.lun`
  preserved the observed legacy scores (`13.2932` extraction and `0.0082`
  hybrid) while exposing the correct score domains on every row.

Deviations and follow-ups: the flat B+ response remains intentionally
non-breaking; partitioned response shape, extraction case-fold dedup, region
nodes, SPEC-016 warning cleanup, cartridge rebuilds, and new ranking models
remain out of scope.

- Commit/PR reference: Luna Engine `ae3a3c6`; no PR created in this local closeout.
- Implementation date: 2026-08-19.
- Deviations from spec: none; B+ preserves the flat response and legacy fields.
- Follow-up issues created: extraction case-fold dedup remains independent and
  partitioned response shape is deferred.
