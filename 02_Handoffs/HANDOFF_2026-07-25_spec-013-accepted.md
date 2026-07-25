# HANDOFF: SPEC-013 accepted (figure spine)

**Date:** 2026-07-25  
**From:** Auto (Cursor agent)  
**Status:** SPEC-013 promoted `active → accepted` for **figure spine only**

## What landed

- Closed R1–R6 on paper (FTS on `figure.content`; embed default; Markdown alt-only; thumbnail optional; discourse deferred; intimacy C = north star with empty enrichment allowed).
- SPEC moved to `01_Specs/accepted/SPEC-013_searchable-figures.md`.
- Spike evidence already on file: `04_Audits/AUDIT_2026-07-24_searchable-figures-spike.md`.
- Design + research brief updated to point at accepted spine scope.

## Normative now vs later

| Now (accepted) | Later (enrichment / follow-on) |
|---|---|
| `figure` → `image` + `media_blobs` + sha256 | `media_kind`, visual_description, discourse |
| `figure.content` FTS spine (alt/caption) | OCR stack, thin OCR children |
| Embedded v1 policy; MIME allowlist; path containment | External size policy; PDF images; regions; GDAL; COG |

## Engine work (next)

1. Open/merge PR for `feat/searchable-figures-spike` → Engine `main` (or keep branch until ready).
2. On merge: fill SPEC Implementation notes with PR + SHA; `git mv` → `01_Specs/implemented/`.
3. Do **not** start Task 7 enrichment until spine is on Engine main (or explicitly requested).

## Still open on ledger

- Engine PR / merge of spike branch
- SPEC-013 → `implemented/`
- Enrichment amendment / Task 7
- LUN-FORMAT doc note for `media_blobs` / `image` role (optional format pass)
