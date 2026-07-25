# AUDIT: Searchable figures Markdown+PNG spike

**Date:** 2026-07-24  
**Engine:** `ce57d2d4` (`feat/searchable-figures-spike` worktree)  
**Fixture:** `09_Sample_Sources/searchable_figures/ambassador_protocol.md`  
**Output:** `/tmp/ambassador_protocol.lun` (built read-only for audit)  
**Purpose:** Evidence for SPEC-013 spike close — figure/image nodes, embedded bytes, FTS caption hit.

---

## Build command

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/.worktrees/searchable-figures-spike"
PYTHONPATH=src .venv/bin/python3 -m luna.cartridge.builder \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Open-Looney/09_Sample_Sources/searchable_figures/ambassador_protocol.md" \
  "/tmp/ambassador_protocol.lun"
```

Build result: **9 nodes**, **28 words**. Embeddings skipped (network/proxy); non-fatal.

---

## Node counts

| type | count |
| --- | --- |
| document | 1 |
| section | 2 |
| paragraph | 2 |
| sentence | 2 |
| figure | 1 |
| image | 1 |
| region | 0 |
| extractions | 0 |

---

## figure / image nodes

```
type    content                      ulid
------  ---------------------------  --------------------------
figure  Ambassador protocol diagram  01KYBV9FB8J9CF17079WBQFZX5
image                                01KYBV9FB8J9CF17079WBQFZX6
```

Figure wraps image child; figure `content` carries alt/caption text for FTS.

---

## media_blobs

```
node_ulid                   storage   length(sha256)  length(bytes)
--------------------------  --------  --------------  -------------
01KYBV9FB8J9CF17079WBQFZX6  embedded  64              1165
```

One embedded blob; SHA-256 length 64; bytes length matches fixture PNG (1165 B).

---

## FTS — `Ambassador` match

```
The following diagram summarizes the >>>Ambassador<<< protocol handshake.

>>>Ambassador<<< protocol diagram

Surrounding prose mentions **>>>Ambassador<<< protocol** so discourse tests can find…
```

FTS hits figure caption and surrounding prose. Linguistic spine searchable without OCR or enrichment passes.

---

## Spike scope verified

- Markdown `![alt](png)` → `figure` + child `image` + `media_blobs` row
- Embedded PNG bytes + SHA-256 persisted
- Caption/alt on figure node indexed in `nodes_fts`
- Builder validation (`validate_media_blobs`) runs on build (Task 5)

## Non-goals still deferred (not exercised)

| Area | Status |
| --- | --- |
| OCR / scanned-page fallback | not run |
| Taxonomy enrichment (`media_classification`) | 0 extractions |
| Visual description / discourse extractions | 0 extractions |
| `region` nodes / bbox | 0 regions |
| PDF image blocks | not in fixture |
| External `storage` path | fixture uses embedded only |
| COG / tile pyramids / GeoTIFF | out of scope |
| Chat `images.db` | unchanged |

---

## Conclusion

Markdown+PNG spike **passes** intimate-figure minimum: locus (figure→image), bytes+hash, FTS caption. SPEC-013 remains **active** — acceptance blocked on open questions (OCR stack, embed/external thresholds, enrichment type strings, regions UX, PDF images, vision embeddings).
