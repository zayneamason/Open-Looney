# HANDOFF: Build v0.2 reference cartridge from Meditations PDF

**Date:** 2026-05-22
**From:** Ahab (with Claude, research-repo session)
**To:** Claude Code (engine-repo session)
**Purpose:** Produce the project's first v0.2-era reference cartridge from a clean source PDF, deliver it into the research repo's sample cartridges folder, and verify it satisfies every v0.2 contract before reporting back.

---

## Objective

Produce:

```
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun
```

Built from:

```
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/09_Sample_Sources/Marcus-Aurelius-Meditations.pdf
```

Using the v0.2 builder at its last known-good state (the Phase 5 closeout). Full pipeline: parse → extract (Haiku) → embed (MiniLM). No flags that disable extraction or embedding.

This cartridge becomes the new canonical reference artifact for the research repo. It replaces the prior v0.1-era reference cartridge in that role for all forward-looking spec illustrations, audit doc work, and example payloads. The prior artifact's name is intentionally omitted from this handoff and from the resulting cartridge.

## Required reading

In the research repo (`../Research/Code for .lun Development/` relative to the engine repo):

1. `03_Format_Spec/LUN-FORMAT_v0.2.md` — the canonical v0.2 contract. Especially the **Validation checklist** section. Every box on that checklist must tick green before the cartridge is considered shippable.
2. `01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md` — `application_id`, `user_version`, meta-key contract, finalize pragma stack, title validation blocklist.
3. `01_Specs/implemented/SPEC-001_orphan-claims.md` — anchor classification taxonomy + `validate_anchors()` invariants the build must satisfy.
4. `01_Specs/implemented/SPEC-002_portable-ids.md` — ULID identity + canonical generator + `validate_ulids()` first-char `[0-7]` invariant.
5. `01_Specs/implemented/SPEC-003_meaningful-confidence.md` — raw signals, `meta.logprob_base = 'e'`, `meta.logprob_attribution = 'response_level'`.
6. `08_Journal/2026-05-21.md` — Phase 5 closeout context, including the Phase-5-deferred 1-section structural parse failure documented under "Carried-forward Phase 5 deferrals" item 12. The reproduction guardrail in this handoff is built around that failure mode.

In the engine repo:

7. `src/luna/cartridge/builder.py` — the builder entry point. Confirm the `validate_extractions`, `validate_ulids`, `validate_anchors` calls run before finalize.
8. `src/luna/cartridge/validation.py` — centralized validators per Phase 5 Step 5. The functions this handoff relies on.
9. `src/luna/cartridge/migrate.py` — not invoked by this handoff (no migration), but confirm it exists at HEAD as a sanity check that you're on a post-Phase-5 commit.

## Pre-flight checks (run before any build)

Run each check; if any fails, stop and report rather than improvising.

### Check 1 — Engine repo at expected path

```bash
ls -d /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/
```

Expected: directory exists. If not, the build cannot proceed; report path and stop.

### Check 2 — Research repo source PDF present

```bash
ls -lh "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/09_Sample_Sources/Marcus-Aurelius-Meditations.pdf"
```

Expected: file exists, ~1.9MB, PDF version 1.6. If the file is missing or zero-byte, stop and report.

### Check 3 — Research repo target folder exists

```bash
mkdir -p "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/"
ls -la "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/"
```

Expected: directory exists (per README naming, this is `Test .lun files for validation` — the correct home for a sample cartridge).

### Check 4 — Engine repo on a post-Phase-5 commit

```bash
cd /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/
git log --oneline | head -10
```

Expected to see (in some recent order): `325c68b refactor: Phase 5 Step 5`, `cb6d13a feat: Phase 5 Step 4`, `80690e5 feat: Phase 5 Step 1-2`, `6775822 chore(docs): track .lun v0.2 Phase 4+5`. If HEAD is ahead of `325c68b` (e.g., into v0.3 / SPEC-005 work), **stop and report** — this build must use the v0.2-frozen builder, not a v0.3 in-progress builder.

If HEAD is ahead, the safest move is to build from a worktree pinned at `325c68b`:

```bash
git worktree add -d /tmp/luna-v02-build 325c68b
cd /tmp/luna-v02-build
```

…and run the build from there. Clean up the worktree after.

### Check 5 — venv exists and is usable

```bash
cd /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/   # or the worktree from Check 4
ls -d .venv && .venv/bin/python --version
```

Expected: `.venv/` exists, Python 3.x. If the venv is missing or broken, recreate via the engine repo's standard recipe (`uv sync` or `pip install -e .` per the project's bootstrap docs). Do not improvise dep installs piecemeal.

### Check 6 — SQLite version ≥ 3.35

```bash
.venv/bin/python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Expected: 3.35.0 or higher (per SPEC-002 / SPEC-003 ALTER TABLE DROP COLUMN requirement). The Phase 1 evidence shows 3.51.0 was in use; anything ≥3.35 is fine.

### Check 7 — Builder importable

```bash
.venv/bin/python -c "from luna.cartridge.builder import build_cartridge; print('ok')"
```

Expected: `ok`. ImportError likely means the venv is stale; rerun the project's install step.

### Check 8 — Anthropic API key present

```bash
echo "${ANTHROPIC_API_KEY:0:8}..."
```

Expected: prefix of a real key. If unset, the Haiku extraction pass cannot run. Source the key from the engine repo's standard env-loading mechanism (or ask Ahab to export it for this session) before proceeding.

### Check 9 — MiniLM model available (or downloadable)

```bash
.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); print('ok')"
```

Expected: `ok`. First-run will download ~80MB if the model isn't cached. If the network is restricted, report rather than fall back to a different model — the cartridge's `meta.embedding_model` is a contract.

## Build procedure

After all pre-flight checks pass:

```bash
cd /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/   # or the worktree
.venv/bin/python -m luna.cartridge.builder \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/09_Sample_Sources/Marcus-Aurelius-Meditations.pdf" \
  "/tmp/Marcus-Aurelius-Meditations.lun"
```

Notes on the command:

- Output goes to `/tmp/` first. After validation passes, the file is moved (not copied — single canonical location) to the research repo target. This avoids leaving a half-built cartridge in the deliverable path if validation fails mid-build.
- No `--no-extract` or `--no-embed`. The full pipeline runs.
- No `--preserve-paths`. The cartridge ships with `source_filename` (basename only) and no `source_canonical_path`, per SPEC-006's default.

Expected duration: a few minutes. Haiku extraction dominates wall-clock time. Embedding pass is CPU-bound; MiniLM at 384 dims on ~80K words should be under a minute on a modern machine.

If the build raises a `BuildError` at finalize time, the cartridge was rejected by a validator. Capture the full error and the validator name (`validate_extractions`, `validate_ulids`, `validate_anchors`); do not edit the validator to make the error go away. Report in the completion notes and stop.

## Reproduction guardrail: confirm structural parse

The Phase 5 v0.1-era rebuild produced a cartridge with `node_count = 5576` distributed across **one section** because the source had been flattened (the parser didn't see `#` heading markers). The cartridge passed every validator but the extraction was structurally meaningless — only the first 8000 chars of a single section reached Haiku.

The Meditations PDF is original-format, not a reconstruction, so the parser should produce multiple sections. The guardrail check, run immediately after the build succeeds:

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun \
  "SELECT type, COUNT(*) FROM doc_nodes GROUP BY type ORDER BY type;"
```

Expected: more than one `section` row. Meditations is organized in 12 books; a healthy parse should produce many sections (likely 12+ depending on how the PDF marks book/chapter boundaries). If `section = 1`, **stop, do not deliver the cartridge** — the structural parse failed and the build is misleading-by-omission. Report and leave the file in `/tmp/` for inspection.

If `section` count is in the expected range, also capture:

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun \
  "SELECT meta.value, (SELECT COUNT(*) FROM extractions WHERE type='claim') AS claims,
   (SELECT COUNT(*) FROM extractions WHERE type='claim' AND anchor_status='anchored') AS anchored,
   (SELECT COUNT(*) FROM extractions WHERE type='claim' AND anchor_status='match_failed') AS match_failed
   FROM meta WHERE meta.key='word_count';"
```

Report these numbers in the completion notes.

## Verification (every box must tick)

Run all checks against `/tmp/Marcus-Aurelius-Meditations.lun` before delivery. The full set comes from `LUN-FORMAT_v0.2.md` "Validation checklist."

### Pragma layer

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun \
  "PRAGMA application_id; PRAGMA user_version; PRAGMA journal_mode;"
```

Expected:
- `application_id = 1280659011` (decimal for `0x4C554E43`, `LUNC`)
- `user_version = 2`
- `journal_mode = delete`
- No `-wal` or `-shm` sidecar files in `/tmp/Marcus-Aurelius-Meditations.lun*`

### Meta layer

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun \
  "SELECT key, value FROM meta ORDER BY key;"
```

Expected keys present: `cartridge_kind`, `created_at`, `deprecated_columns`, `embedding_dim`, `embedding_model`, `format_version`, `logprob_attribution`, `logprob_base`, `node_count`, `source_filename`, `source_format`, `source_hash`, `title`, `word_count`.

Required values:
- `format_version = '0.2'`
- `cartridge_kind = 'knowledge'`
- `logprob_base = 'e'`
- `logprob_attribution = 'response_level'`
- `deprecated_columns = 'doc_nodes.id,extractions.id'`
- `source_filename = 'Marcus-Aurelius-Meditations.pdf'` (basename, not absolute path)

Forbidden keys (must NOT appear): `source_path`, `schema_version`.

Title check: the title must pass SPEC-006's parser-artifact blocklist. The Meditations PDF should produce a clean title; if you see anything like `"/. Aurelius"` or a single-character prefix, that's the M-01 failure mode and the build should be rebuilt with title-debug logging on. Report it.

### Schema layer

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun ".schema extractions"
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun ".schema claim_sources"
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun ".schema doc_nodes"
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun ".tables claim_context_nodes"
```

Expected:
- `extractions` has columns `anchor_status`, `anchor_reason`, `ulid`, `llm_logprob_sum`, `llm_token_count`, `extraction_method`; does NOT have `confidence`.
- `claim_sources` has columns `anchor_method`, `anchored_by`, `anchored_at`, `event_id`.
- `doc_nodes` has column `ulid` with `uq_doc_nodes_ulid` UNIQUE INDEX.
- `claim_context_nodes` table exists.

### Validators

```bash
.venv/bin/python -c "
import sqlite3
from luna.cartridge.validation import (
    validate_cartridge_open, validate_extractions, validate_ulids, validate_anchors,
)
conn = sqlite3.connect('/tmp/Marcus-Aurelius-Meditations.lun')
validate_cartridge_open(conn)
validate_extractions(conn)
validate_ulids(conn)
validate_anchors(conn)
conn.close()
print('all validators pass')
"
```

Expected: `all validators pass`. Any exception means the cartridge is non-compliant. Capture the full traceback and report.

### Data-layer spot checks

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun "
SELECT 'no_unknown_claims', COUNT(*) FROM extractions WHERE type='claim' AND anchor_status='unknown';
SELECT 'anchor_status_dist', anchor_status, type, COUNT(*) FROM extractions GROUP BY anchor_status, type;
SELECT 'ulid_first_chars', substr(ulid, 1, 1), COUNT(*) FROM doc_nodes GROUP BY substr(ulid, 1, 1);
SELECT 'extraction_method_dist', extraction_method, COUNT(*) FROM extractions GROUP BY extraction_method;
SELECT 'logprob_paired_null', SUM(CASE WHEN (llm_logprob_sum IS NULL) = (llm_token_count IS NULL) THEN 0 ELSE 1 END) AS mismatches FROM extractions;
"
```

Expected:
- `no_unknown_claims` count: **0**. Any non-zero is a SPEC-001 violation; the build should be rejected.
- `ulid_first_chars` distribution: every observed first char must be in `[0-7]` (canonical SPEC-002 ULID generator). Anything in `[8-9A-Z]` is the Phase 3.5 regression and is a hard failure.
- `logprob_paired_null` mismatches: **0**. SPEC-003 paired-NULL invariant.
- Other distributions: capture and report; not pass/fail but useful baseline numbers for the eventual audit doc.

### SQLite integrity

```bash
sqlite3 /tmp/Marcus-Aurelius-Meditations.lun "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

Expected: `ok` from the first; empty result from the second.

## Deliverable

After every verification check passes:

```bash
mv /tmp/Marcus-Aurelius-Meditations.lun \
   "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun"

ls -lh "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/"
```

The cartridge lives only at the research-repo target path. Do not commit it to the engine repo. The research repo's git-tracking is the user's call; this handoff doesn't make that decision.

If you created a worktree in Check 4, clean it up now:

```bash
cd /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/
git worktree remove /tmp/luna-v02-build
```

## What NOT to do

- **No v0.3 work.** This handoff is a v0.2 build. SPEC-005 implementation is queued for the engine repo but is not in scope here. If HEAD has moved past `325c68b` into v0.3 territory, build from a worktree pinned to `325c68b` (Check 4). Do not run SPEC-005 migration against the resulting cartridge.
- **No source modifications.** Do not edit `builder.py`, `validation.py`, `migrate.py`, or any other engine source to make the build pass. If a validator rejects the cartridge, report the error and stop; the answer is upstream fixes through proper specs, not a one-off patch.
- **No fallbacks on parser failure.** If the structural-parse guardrail fails (1-section parse), do not ship the cartridge with an apologetic note. Report and stop.
- **No `--no-extract` / `--no-embed`.** A structure-only cartridge cannot substantiate a v0.2 audit. Full pipeline only.
- **No alternate embedding model.** `meta.embedding_model = 'all-MiniLM-L6-v2'` is a contract. If MiniLM isn't available, the build cannot proceed.
- **No new specs or audits in the research repo.** The audit doc is downstream research-side work; this handoff stops at "cartridge delivered, verified, stats captured."

## Reporting back

Write a completion note (paste into the engine-repo session's chat or commit a brief markdown note to the engine repo's handoff folder — wherever the engine-side process lives) covering:

1. **Pre-flight check results.** One line per check (1–9) — pass/fail and any deviation from expected.
2. **Commit hash actually built against.** Either current HEAD or the worktree commit if you used Check 4's fallback.
3. **Build command exit status and wall-clock time.**
4. **Structural parse result.** Section count + node count + word count from the guardrail query.
5. **Validator results.** One line per validator (`validate_cartridge_open`, `validate_extractions`, `validate_ulids`, `validate_anchors`).
6. **Data-layer spot-check numbers.** Anchor status distribution, ULID first-char distribution, extraction method distribution, logprob paired-NULL mismatches.
7. **Final cartridge stats.** Size on disk, total extractions count broken down by type, claim anchor breakdown.
8. **Anything anomalous.** Title parsing oddities, unexpectedly high `match_failed` rates, slow extraction passes, retried Haiku calls — anything that would inform the eventual v0.2 audit doc.
9. **Deliverable path confirmation.** `ls -lh` output for the final location.

Keep the note tight; the goal is enough evidence that the cartridge can be trusted as the v0.2 reference artifact going forward.

## Cross-references

- Research repo: `/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/`
- Engine repo: `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`
- Format contract: `03_Format_Spec/LUN-FORMAT_v0.2.md`
- Phase 5 closeout commits referenced in pre-flight Check 4: `6775822`, `80690e5`, `cb6d13a`, `325c68b`
- Phase 5 1-section parse gotcha that motivates the structural-parse guardrail: SPEC-001 and SPEC-003 "Phase 5 closeout" sections, Item 9
