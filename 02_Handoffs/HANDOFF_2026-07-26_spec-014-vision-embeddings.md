# HANDOFF: SPEC-014 vision embeddings — implemented, NOT merged

**Date:** 2026-07-26
**Status:** Implemented and reviewed on a branch. **Merge is an open decision.**
**Continue in:** Claude Code
**Repo:** `_LunaEngine_BetaProject_V2.0_Root`, branch `feat/spec-014-vision-embeddings`
**Size:** 22 commits, 11 files, +1845 / −120
**Spec:** `01_Specs/active/SPEC-014_vision-embeddings.md`
**Plan:** `Docs/Plans/2026-07-26-spec-014-vision-embeddings.md` (Engine repo)
**SDD ledger:** `.superpowers/sdd/2026-07-26-spec-014-vision-embeddings/progress.md` (Engine repo, git-ignored — every fix round and deferred minor is in it)

## Start here

The branch is complete and reviewed but **unmerged**, with **zero human review**.
Ten tasks each passed an agent review; a final whole-branch agent review found two
merge-blockers that per-task review could not see, both since fixed and
re-reviewed clean. That is not the same as a person having read it.

Two things are owed at or before merge — see "Owed" below. Neither blocks the code.

## What it does

Figures become retrievable by what they depict. Storage is a **second, separate
table**, additive, so `user_version` stays 3 and old readers are untouched:

```sql
CREATE TABLE image_embeddings (
    node_ulid TEXT NOT NULL PRIMARY KEY,   -- the `image` node
    vector    BLOB NOT NULL,
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid)
) WITHOUT ROWID;
```

with its own `image_embedding_model` / `image_embedding_dim` meta pair. Builder
gains opt-in `--figure-embed` (local CLIP, no API cost). The reader gains a
vision leg inside the **default** `hybrid` search, fused through a now-variadic,
leg-count-normalised `_rrf_fuse`. `similar()` is implemented for `figure`/`image`
ULIDs only.

**The fork the ledger recorded as blocking did not require a governance step.**
`LUN-FORMAT v0.3:317` reads "`embeddings.vector`" and sits under the `embeddings`
heading — its scope was always that one table. A second table is *outside* the
invariant, not a workaround for it. No relaxation, no wiki MAJOR, no v0.4.

## Proof it works

Acceptance test, real 37 MB PDF, real CLIP, query `"currency over a map"`:

```
vision leg top hit: ulid=01KYGRXXA5NQ4THX4Z4GN6CXKX score=0.2637 search_type=vision
hybrid surfaces the SAME ulid with vision_score=0.2637
```

Same node on both paths, so the leg works *and* is wired to the public surface.
26/26 vectors on the `--figure-embed` build, 0 on the control.

## The two blockers the whole-branch review caught

Both lived **between** tasks. Ten green task reviews and a green suite said
nothing about either. This is the strongest argument in the arc for treating the
whole-branch pass as a real step rather than a formality.

1. **A ~60x score inflation on the default search path.** A `len(legs) > 1`
   guard meant a lone surviving semantic leg was returned *unfused* — raw cosine
   `0.99` labelled `"semantic"` where it should have been `0.0164` labelled
   `"hybrid"`. Reachable on all three live cartridges whenever FTS missed, and it
   re-created the exact cross-collection distortion the normalisation was added
   to prevent. Fix: fuse whenever `sem_alive or vis`. No test exercised
   `kw == []`, which is why it survived eight rounds; there is one now.

2. **A missing `.[vision]` install produced a *successful* build with zero
   vectors.** The per-item `except Exception` that isolates one corrupt raster
   (correct) swallowed the `ImportError` that the deliberately unwrapped builder
   block (also correct) existed to surface. Two right decisions cancelling at the
   seam. Fix: `_preflight_vision_dependencies()` runs once before the batch and
   fails the build naming `pip install '.[vision]'`; the per-item catch is
   retained for genuinely corrupt rasters.

## Gotchas that cost real time

- **`sentence-transformers` is NOT a core dependency.** It lives in the `hub`
  extra; the design rationale "adds no new heavy dependency" was false. Ruling:
  it is now also in the **`vision`** extra beside Pillow, so one
  `pip install '.[vision]'` covers the feature. The spec body and implementation
  notes now record this.
- **No pre-SPEC-014 cartridge has an `image_embeddings` table.** All four in
  `data/user/cartridges/` lack it, including the three mounted live. Reads must
  check `sqlite_master` first; an absent table is a normal old cartridge, not a
  fault. The test fixture always *created* the table and left it empty — a shape
  no real cartridge has — so the empty case was tested and the absent case, the
  only one that occurs today, was not.
- **`doc_nodes` enforces `CHECK (length(ulid) = 26 AND ulid GLOB
  '[0-9A-HJKMNP-TV-Z]*')`.** A short placeholder like `'U1'` can never insert.
  This defect appeared three separate times in the plan.
- **The sample's figure captions are all literally `"Image (page N)"`.** There is
  no "Plate 4" despite what an earlier handoff's example implies.
- **`figure_discourse` is a promoted enrichment type** (`media.py:54-58`, PR
  #173). The Nature-of-Art sample contains an artwork *made of sewn US currency*,
  whose label lands there — so a "currency" query reaches that figure lexically.
  The acceptance test was restructured to call the vision leg directly rather
  than hunt for a lexically pure query; moving the seam beats sanitising input.
- **`_rrf_fuse` relabels every fused row `"hybrid"`**, so `vision_score` must be
  captured from the raw leg *before* fusion. Filtering for `search_type ==
  "vision"` afterwards matches nothing.

## Owed

**Closed in Open-Looney docs at `419a31d` + working-tree correction: record 3
deviations in the spec body and Implementation notes.**

1. `sentence-transformers` is in the `vision` extra, not core.
2. The spec's `similar()` branch "declared model is not loadable → warn and
   return `[]`" is **unreachable by design** — image-to-image uses the stored
   vector as the query vector, so no model is ever loaded. Better than spec, but
   the original spec asserted a branch the code cannot have.
3. Build-time loud failure is now delivered via `_preflight_vision_dependencies()`
   rather than the spec's literal `BuildError` sketch.

**Follow-ups, triaged by the final review** (full list in the SDD ledger). The two
worth doing soon:

- **`_score_embedding_row`'s error message prints TEXT-path values on an IMAGE
  fault** — `meta.embedding_dim`, `config dim`, `model` are all MiniLM/384 even
  when a 512-dim image vector is the problem. Re-rated from cosmetic to
  misleading. Fix: pass a `space` label through.
- **Closed at Engine `83d8c20`: slow tests are excluded from the default run.**
  `pyproject.toml` now has `addopts = ["-m", "not slow"]` and declares the
  `slow` marker.

Lower priority: `vision_score` is stamped only on enrichment-promoted rows, so a
fused-but-unpromoted figure loses the visible signal; missing meta is loud in the
vision leg but silent in `similar()`; a tautological RRF ordering test that
passes with the divisor deleted; `similar()` builds the full node index before
its own guards.

## Suggested next session opener

> SPEC-014 is implemented on `feat/spec-014-vision-embeddings` and unmerged, with
> no human review. Read this handoff's two blockers first — both were seam
> defects invisible to per-task review. Decide on merge, then record the three
> spec deviations and pick up the two named follow-ups.
