# SPEC-015: Retrieval score comparability across result kinds

**Status:** active
**Severity:** medium
**Author:** Ahab
**Created:** 2026-08-15
**Last updated:** 2026-08-15
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

Not yet settled — see Open questions. Four candidates, with the trade-off that
distinguishes each.

**A. Make extraction a fusion leg.** Feed extraction results into `_rrf_fuse`
alongside keyword/semantic/vision instead of concatenating afterwards. Most
principled: one scale by construction, and `_rrf_fuse` already handles N legs and
normalises. Cost: it abolishes guaranteed extraction-first ordering. If that
ordering is deliberate policy, this is a product regression disguised as a
scoring fix.

**B. Add a discriminator, forbid cross-family sorting.** Emit an explicit
`rank_class` (`"extraction"` / `"fused"`) on every row and fix
`dataroom_tools.py:124` to sort within class. Cheapest and most honest about
what the numbers are. Cost: every present and future consumer must respect the
convention, and conventions are not enforceable — the failure mode returns
silently the first time someone forgets.

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

Determined by the option chosen. In all cases the audit surface is the same and
should be enumerated before implementation: `_v02_search`, `_v03_search`,
`search()`, `search_entity()`, `similar()`, `dataroom_tools.py`, the
`/api/nexus/*` routes, the MCP `aibrarian_*` wrappers, and any prompt-assembly
consumer (see Open question 2).

### Migration path

Forward- and read-compatible for cartridges — nothing on disk changes. Option C
is a breaking change for API consumers and would need a deprecation window;
options A and B are not.

## Validation rules

```python
# Regression guard, required whichever option is chosen:
# no single response may mix rank classes under one sortable key.
results = search(collection, query, "hybrid", limit=20)
classes = {classify(r) for r in results}          # by search_type / rank_class
assert len(classes) == 1 or response_is_partitioned(results)

# Scale sanity: within one class, no two rows may differ by >2 orders of
# magnitude purely by family. Catches a regression to the current state.
by_class = group(results)
for rows in by_class.values():
    if len(rows) > 1:
        assert max(r.score for r in rows) / max(min(r.score for r in rows), 1e-9) < 100
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
- **Memory Matrix integration:** unknown until Open question 2 is answered. If
  prompt assembly consumes these scores, the defect reaches what Luna actually
  says, not merely what a tool returns.

## Alternatives considered

**Do nothing; document the convention.** Rejected: the convention is already
undocumented and already violated by `dataroom_tools.py:124`, which is in the
default path rather than an edge case.

**Fix only `dataroom_tools.py`.** Rejected as treating the one known consumer
rather than the contract. The next consumer inherits the same trap.

**Fold this into SPEC-014.** Explicitly rejected by SPEC-014 itself, which
records that reconciling scale across result *kinds* "needs its own spec"
because it touches every collection and every caller.

## Open questions

These block acceptance.

1. **Is extraction-first ordering deliberate product policy, or an artefact of
   concatenation order?** This decides whether option A is available at all. If
   deliberate, the spec must preserve ordering while fixing comparability, which
   rules A out and points at B or C.
2. **Does prompt assembly consume these scores, and does it threshold or
   truncate on them?** A grep of the obvious call sites was inconclusive. If
   assembly takes a top-N or applies a cutoff, this defect silently shapes Luna's
   context window and the severity should rise from medium.
3. **Response shape (C) or score space (A/B)?** C is the only option that makes
   the failure unrepresentable, and is the only breaking one.
4. **Do the sibling paths share the defect?** `search_entity()` and the `v0.2`
   path were not measured. Assume yes until shown otherwise.
5. **Is the `score > 0.01` constant safe to leave in place** once fused scores
   are in scope, given the ranges overlap? At minimum it needs a comment naming
   the hazard.

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

(Filled in when status moves to `implemented`)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
