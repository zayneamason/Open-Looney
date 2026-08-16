# HANDOFF: SPEC-014 closeout — merged three weeks ago, ledger never noticed

**Date:** 2026-08-15
**Status:** Closeout only. No format change, no Engine change.
**Continue in:** Claude Code
**Repo:** `Apps/lun Development` (Open-Looney) — the Engine side was already done
**Closes:** the `[~] SPEC-014 — IMPLEMENTED, NOT MERGED` ledger line

## What was actually wrong

Nothing in the code. SPEC-014 vision embeddings merged to Engine `main` on
**2026-07-27** via merge commit `ad3be249` ("Merge SPEC-014 vision embeddings"),
11 files, +1844/−120. This repo went on saying it was unmerged until today,
and published that claim — the spec file sat in `01_Specs/active/` and the
public ledger read `IMPLEMENTED, NOT MERGED`.

**Root cause: the merge skipped the PR.** This repo's working rhythm is
*Engine PR → merge → update spec + ledger + handoff here*. `ad3be249` is a plain
local merge commit with no pull request behind it, so `gh pr list --head
feat/spec-014-vision-embeddings` returns `[]`. There was no PR event to key the
closeout off, and the doc-drift the two-repo split exists to prevent happened
anyway.

**Watch for this.** Any future no-PR merge has the same blind spot. The rhythm
needs a second trigger — a periodic `git merge-base --is-ancestor` sweep of every
open spec's branch against Engine `main` would catch it mechanically.

## How the merge was verified

Two independent checks, because the ledger's own claim could not be trusted:

1. `git merge-base --is-ancestor 83d8c20 main` → true. The branch tip named in
   the ledger is an ancestor of `main`. `git branch --merged main` lists the
   branch.
2. A cartridge built fresh today from a 62-page PDF carries an
   `image_embeddings` table. Per the SPEC-014 record, no pre-SPEC-014 cartridge
   has one.

## What changed here

- `01_Specs/active/SPEC-014_vision-embeddings.md` → `01_Specs/implemented/`.
  `active/` is now empty: no spec in flight.
- Spec header `Status: active` → `implemented`, with the merge commit and the
  no-PR cause recorded.
- Ledger line `[~]` → `[x]` with merge evidence and the no-PR warning.
- Ledger frontmatter `updated: 2026-07-26` → `2026-08-15`.
- **LUNM Inspector MVP committed.** The 2026-07-27 session built, bundled, and
  smoke-tested it, wrote four `[x]` ledger lines, and never committed the code.
  The ledger had been documenting a prototype the repo did not contain. 47 files,
  0.5 MB of a 1.9 GB tree; its own `.gitignore` already excluded `src-tauri/target`,
  `node_modules`, and `*.db`/`*.sqlite`/`*.lun`.
- **Source PDFs gitignored.** This repo is public and `09_Sample_Sources/` holds
  in-copyright books. Untracked PDFs are now ignored; already-tracked ones are
  deliberately untouched.

## Still open, unchanged by this handoff

- **SPEC-014 has had zero human review.** Merging did not change that. The two
  merge-blockers found before merge were both seam defects that ten green
  per-task reviews missed and only a whole-branch review caught.
- Already-published book content on the public remote —
  `09_Sample_Sources/Marcus-Aurelius-Meditations.pdf` and three sample
  cartridges including a full extracted `The-Nature-Of-Art-And-Creativity.lun`.
  Removing those is a history rewrite and a separate decision.
- `Regions` (`region` node under `image`) — reserved, still needs a producer and
  a consumer.
- GDAL/COG media-family RFC — parked on its recorded trigger. Its source survey
  scrape was rescued to `Research/gisgeography-free-gis-software.md` when the
  duplicate clone was deleted (see below).

## Two Engine defects found while verifying — logged in the Engine ledger

Both live in `_LunaEngine_BetaProject_V2.0_Root/ProjectManager/TODO_Project_Organization_And_Cleanup_2026-07-10.md`,
not here, because they are Engine runtime bugs rather than format issues.

1. **MCP `aibrarian_*` runs a cartridge-blind fallback instance.** `set_engine()`
   never fires, so `_get_engine()` builds a standalone `AiBrarianEngine` with no
   `nexus_registry`, and cartridges are structurally invisible to every MCP tool.
   Silent. Evidence: MCP `aibrarian_list` returned 2 collections while
   `GET /api/nexus/list` on the same machine returned those 2 plus 5 connected
   cartridges.
2. **`_v03_search` returns two incomparable score scales in one list.**
   `return extraction_results + node_results` concatenates raw FTS5 scores
   (observed 5.47–13.29) with RRF-normalised ones (observed 0.0082, identical on
   every row). Scoped as an investigation, with the `_rrf_fuse` docstring's own
   note about a cross-collection re-sorter in `tools/dataroom_tools.py` as the
   first thing to check.

## Repo hygiene done in the same pass

`Research/Open-Looney` was a **second clone of this same remote**, 15 commits
behind, zero unique commits, frozen at `e4b2b7d` since 2026-07-25. Deleted after
verifying no unique commits, no stashes, and one branch. Its only untracked file
— a GIS software scrape that is the source for the parked GDAL/COG survey — was
preserved to `Research/gisgeography-free-gis-software.md`.

The risk it carried: a session could have committed on that stale base and
force-pushed 15 commits of spec history off a public repo.
