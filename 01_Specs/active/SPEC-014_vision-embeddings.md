# SPEC-014: Vision embeddings

**Status:** active
**Severity:** medium
**Author:** Ahab
**Created:** 2026-07-26
**Last updated:** 2026-07-26
**Affects format version:** v0.3 (additive — no version bump)

---

## Problem statement

A figure in a cartridge is reachable only through words someone wrote about it —
its caption, its OCR text, its neighbour prose, or the `visual_description`
SPEC-013 generates. Nothing in the format describes what the figure *looks like*
in a form a query can be compared against. A reader cannot ask "which plate shows
currency laid over a map" and have the pixels answer; it can only hope somebody's
prose happened to use those words. Figures are therefore searchable by
description and not by content, and no amount of better description closes that
gap — the description is a lossy human-language projection of an image, produced
once, against no particular query.

## Observed evidence

- SPEC-013 shipped `visual_description` via `--figure-vision` (Engine #174,
  `c70d8937`). Its own handoff is explicit that the gain is **lexical**, not
  semantic: "a figure becomes findable by words describing its *content* rather
  than only its *caption*. It is **not** semantic or cross-modal figure search."
- Measured on the Nature-of-Art cartridge: `embeddings` holds 42 paragraph + 30
  section rows, **zero** anchored to any figure and **zero** for any
  `visual_description` ULID. The embedder covers `paragraph` and `section` nodes
  only.
- The retrieval path suppresses `visual_description` as redundant when it merely
  restates the caption, which was the original defect #174 fixed — evidence that
  description quality is a treadmill, not a solution.

## Root cause analysis

Two distinct causes, only one of which is about vectors.

**1. No image vectors exist.** The builder never encodes a raster. This is a
missing capability, not a bug.

**2. The format cannot hold a second embedding space.** `meta.embedding_model`
and `meta.embedding_dim` (`LUN-FORMAT_v0.3.md:113-114`) are **cartridge-wide
singletons**, and the consumer contract at `LUN-FORMAT_v0.3.md:317` is a MUST
against that single width: `length(vector) == embedding_dim * 4`. An image vector
of a different width makes a cartridge fail its own validation checklist
(`v0.3:1003`).

This second cause is why SPEC-014 was recorded as format-blocked. Note that the
blocking constraint is a **(model, dim) pair**, not a scalar — the ledger's
framing ("relaxing that MUST") understated it, because a vision model differs
from MiniLM in both dimensions and identity.

**Prerequisite, now discharged.** Before this spec could be implemented safely,
`_v03_vec_search` cosined with `zip(a, b)`, which truncated rather than raising —
a 512-dim vector would have scored against its first 384 components and returned
a plausible float. Fixed in Engine [#175](https://github.com/zayneamason/LunaEngineBetaV2.0/pull/175)
/ `c4d60b6a`. See `02_Handoffs/HANDOFF_2026-07-26_vec-dim-guard.md`.

## Proposed solution

A **second, separate table**, additive, with its own (model, dim) declaration.
Old readers never scan it; `user_version` stays 3.

### Schema changes

```sql
CREATE TABLE image_embeddings (
    node_ulid TEXT NOT NULL PRIMARY KEY,   -- the `image` node this vector describes
    vector    BLOB NOT NULL,               -- raw float32, image_embedding_dim * 4 bytes
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;
```

`PRIMARY KEY (node_ulid)` alone — deliberately **not** `(node_ulid, level)` as in
`embeddings`. The text table needs `level` because one node yields both a
paragraph and a section vector; one `image` node yields exactly one image vector.
No `level` column and no index on it.

Anchors to the `image` node, not `figure`: a vector describes one raster, and a
`figure` may wrap several. Retrieval resolves upward to the figure through
SPEC-013's existing promotion path, so a vision hit and an enrichment hit are
indistinguishable in shape to a consumer.

New `meta` keys — both required when the table is non-empty, both absent when it
is empty:

| key | type | example |
|---|---|---|
| `image_embedding_model` | string | `clip-ViT-B-32` |
| `image_embedding_dim` | integer | `512` |

**The v0.3 MUST at line 317 is not modified.** It reads "`embeddings.vector`" and
sits under the `embeddings` heading; its scope was always that one table. The new
table carries a parallel MUST against `image_embedding_dim`. No relaxation, no
wiki MAJOR, no v0.4. This is the single most consequential finding of the design
pass: the fork was believed to require a governance step that it does not.

### Behavioral changes

**Builder — `--figure-embed`, default off.**

Joins the existing `wants_bytes` condition at `builder.py:263`
(`do_figure_ocr or do_figure_vision`) rather than adding a raster path. Sits
alongside `--figure-ocr` / `--figure-vision` in the CLI at `builder.py:576`.

The opt-in rationale **differs from its neighbours** and the difference is
normative context, not trivia: `--figure-vision` is opt-in because it bills one
Claude API call per figure. `--figure-embed` is local inference with no API cost;
it is opt-in only for a model download and build time. Anyone later asking why
this is not default-on should get the right answer.

Page images are skipped, reusing #172's `page_image` marker and #174's ordering
lesson — **filter before the encode, not at write time**. The rationale differs
from #174's: there it saved an API call; here CLIP over a page of body text
produces near-noise, so the row would be cost without signal.

No downscaling. `--figure-vision` needs it because Anthropic caps the base64
payload at 5 MB (3.75 MB raw); CLIP resizes to 224x224 internally. The adjacent
code has a downscale path and copying it would be cargo-culting.

Pillow is needed only to decode pixels for the encoder and rides the existing
optional `vision` extra (`pyproject.toml:89`) rather than adding one.
`sentence-transformers` (5.5.1) is already a core dependency and ships
`clip-ViT-B-32`, so this adds **no new heavy dependency**.

Meta keys are written from the encoder actually loaded, never hardcoded, at the
time the first row is written. Zero rows produced means neither key is written.

**Reader — a vision leg inside `hybrid`.**

The leg runs as part of the default `hybrid` search rather than behind its own
`search_type`. A dedicated type would mean only callers who already knew about
the feature could benefit, and `dataroom_search` — the default entry point —
would never improve.

The query vector is encoded with the **cartridge's** declared
`image_embedding_model`, never from local config. This is deliberately the
opposite of the text path, where `_get_generator(conn.config)` uses config and
ignores what the cartridge declares (#175 follow-up 3). The vision side is built
correctly from the start rather than inheriting that bug; reconciling the text
side remains a named follow-up because it touches every existing collection.

CLIP is lazy-loaded on the first vision-eligible query, never at connect, so
text-only cartridges pay nothing.

`_rrf_fuse` (`aibrarian_engine.py:2365`) becomes variadic over result lists. RRF
generalises to N lists for free; fusing pairwise twice would introduce an
ordering artefact. The two-list signature was never a design decision — just the
number of legs that existed.

Missing-model handling reuses `_hybrid_semantic_leg`, generalised from "the
semantic leg" to "an optional leg" taking the leg's name. That helper already
encodes the required contract: degrade the optional leg, keep everything else,
announce it, never take the collection down.

**Dedup gains a third arrival.** Since #173 a figure can arrive twice (promoted
enrichment hit, plus direct caption match), handled by the `promoted_ulids`
filter at `aibrarian_engine.py:1846`. Vision adds a third. Rule: collapse to one
result, keep the **promoted** row (it carries kind, description and context), and
carry the vision signal onto it as an explicit `vision_score` field on the result
dict — not folded into `score`, which `_rrf_fuse` owns. Ranking still reflects
that two independent signals agreed because the vision leg contributed its own
rank to the fusion; `vision_score` exists so a consumer can *see* that it did.

**Image-to-image similarity.** `similar()` currently refuses v0.2/v0.3 outright
(`aibrarian_engine.py:2397`). SPEC-014 implements it for v0.3 **only when the
ULID names a `figure` or `image` node** — cosine against `image_embeddings`,
resolve up, same figure-result shape. Text-node `similar()` stays unimplemented.

### Migration path

**Forward-compatible.** Old readers handle new files by ignoring the addition:
they never `SELECT` from `image_embeddings`, so a vision-embedded cartridge is an
ordinary v0.3 cartridge to them. No migration, no `user_version` change, no split
in the reader fleet.

Existing cartridges gain image vectors only by being rebuilt with
`--figure-embed`. There is no backfill path and none is needed — the table's
absence is a legitimate state.

This forward-compatibility is *why* the separate table was chosen over reusing
`embeddings` with a new `level` value. That alternative would place wide vectors
in a table every existing reader scans: a pre-#175 reader would silently truncate
them, and a post-#175 reader would raise. Neither is acceptable for a change
billed as additive.

## Validation rules

Build time and read time deliberately differ. At build the user asked for
something and must be told it did not happen. At read the capability is optional
and a missing model must not cost them their other results.

```python
# --- Build time: fail loud, the user asked for this explicitly ---
if figure_embed_requested:
    if not pillow_available or not encoder_loadable:
        raise BuildError(...)          # never silently emit a cartridge without vectors

# --- Cartridge validation (added to the v0.3:1003 checklist) ---
for row in image_embeddings:
    assert len(row.vector) == meta.image_embedding_dim * 4
    assert doc_nodes[row.node_ulid].type == "image"
if image_embeddings:
    assert meta.image_embedding_model and meta.image_embedding_dim   # both parseable

# --- Read time ---
# width mismatch -> raise, reusing #175's guard with image_embedding_dim as meta_dim
score = _score_embedding_row(conn, qvec, blob,
                             node_label=ulid, meta_dim=meta_image_dim)
# declared model not loadable -> skip leg, WARN ONCE per connection
# table empty or absent      -> silent no-op (a cartridge without vectors is not defective)
```

Coverage is **best-effort**, matching the S-01 text policy: readers LEFT JOIN,
never INNER JOIN, and MUST NOT assume every `image` node has a vector.

**Note on the skip path.** Skipping the vision leg with a WARNING is deliberate
graceful degradation and does **not** contradict the project's
no-silent-degradation rule. That rule governs paths downstream of a committed
decision; this is an optional capability whose absence is announced. The
distinction is recorded here so a future reader does not "fix" it into a raise.

## Governance implications

- **Ledger / annotation events:** N/A. Image vectors are derived artefacts, not
  assertions, and generate no annotation events.
- **Multi-axis imprint weights:** N/A.
- **Actor roles:** N/A.
- **Cross-cartridge traversal:** a vision leg is only comparable across
  cartridges that declare the *same* `image_embedding_model`. Cross-cartridge
  vision ranking is therefore **out of scope** for this spec; per-cartridge
  results fuse as they do today.
- **Memory Matrix integration:** N/A. No promotion of image vectors to LUNM.

## Alternatives considered

**Reuse `embeddings` with a new `level` value, plus a per-level
`embedding_spaces` table.** Attractive because `PRIMARY KEY (node_ulid, level)`
already discriminates and `level` is already indexed — no new table, no new FK.
Rejected on forward-compatibility: it puts wide vectors in the one table every
existing reader scans (silent truncation before #175, a raise after).

**Generalised embedding-space registry** — `embedding_spaces(space, model, dim)`
plus `space_embeddings(space, node_ulid, vector)`. One mechanism for every future
modality. Rejected as YAGNI: it costs a join and a level of indirection on the
hot read path to serve modalities that do not exist, and the one candidate third
space (GDAL / COG media family) is explicitly parked with its trigger unmet
(`08_Journal/2026-07-24_research-searchable-figures.md:246`). Cheap to adopt
later — one table plus a backfill.

**Bump to v0.4 and make `embeddings` multi-space by design.** Cleanest end state,
rejected as disproportionate: it forces a migration and a reader-fleet upgrade to
deliver a capability that a purely additive change delivers with neither.

**Mandate one blessed encoder in the spec.** Guarantees interoperability and
needs no negotiation, but welds the format to one model's lifetime and makes
model deprecation a format event. Rejected in favour of per-cartridge
declaration with a skip path.

**Vision-only search** (no OCR, no linguistic spine) was **already rejected** as a
sole strategy on 2026-07-24
(`08_Journal/2026-07-24_research-searchable-figures.md:88`, option D). SPEC-014
adds a leg to the existing hybrid; it does not replace the linguistic spine. Do
not re-propose it.

## Open questions

None blocking acceptance. Design decisions settled during the 2026-07-26
brainstorm: purpose (cross-modal **and** image-to-image), compatibility (additive,
no version bump), model pinning (declared per cartridge, reader skips if absent),
and scope (format + reader + builder in one arc).

## Dependencies

- **SPEC-013** (searchable figures) — implemented. Supplies `figure` / `image`
  node types, the promotion path that turns a hit into a figure result, and the
  `page_image` marker this spec's skip filter reuses.
- **Engine #175** (`c4d60b6a`) — merged. Supplies `_score_embedding_row`, which
  this spec reuses verbatim for width validation, and `_hybrid_semantic_leg`,
  which it generalises.

## Non-goals

Stated explicitly so scope does not drift during implementation:

1. **No region-level vectors.** SPEC-013's `region` node is still producer-less.
2. **No text-node `similar()`.** Only `figure` / `image` ULIDs are supported.
3. **No reconciliation of the text path's config-vs-cartridge width** (#175
   follow-up 3). This spec builds the vision side correctly; changing the text
   side touches every existing collection.
4. **No second embedding space beyond images.**
5. **No cross-cartridge vision ranking.**

## Acceptance test

Build the Nature-of-Art cartridge with `--figure-embed` and **without**
`--figure-vision`, so the cartridge contains image vectors and no generated
descriptions. Query `"currency over a map"`. Plate 4 must be returned.

Omitting `--figure-vision` is the entire point, and it is why the test specifies
a build configuration rather than a query filter. #174 already makes that plate
findable by those words through its generated description; an acceptance test run
against a cartridge that *had* the description would pass on the lexical path
alone and prove nothing about cross-modal retrieval. Building without it means
the only text about that figure is the caption `"Plate 4"`, so a hit can only
have come from the pixels.

A second assertion guards the converse: the same query against the same cartridge
built **without** `--figure-embed` must return nothing for Plate 4. Otherwise the
first assertion could pass for a reason nobody checked.

## Implementation notes

(Filled in when status moves to `implemented`)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
