# HANDOFF: Post-reader v0.2 roadmap — phases 1–4

**Date:** 2026-05-22
**From:** Ahab (with Claude, research-repo session)
**To:** Claude Code (research-repo session)
**Purpose:** Convert the Tauri reader prototype's build findings into format-spec patches, clean up two pieces of stale state, and close out the v0.2 era with a clean audit doc grounded in the reference cartridge.

---

## Overview

Four phases, dependency-ordered. Each is a single session of work or less; the audit is the largest. Stop at the end of any phase if blocked; do not skip ahead.

| Phase | Subject | Scale | Output |
|---|---|---|---|
| 1 | Format-spec patch from reader findings | ~1 session | `03_Format_Spec/LUN-FORMAT_v0.2.md` edits |
| 2 | Reader SPEC.md acceptance-criterion fix | ~10 min | One paragraph edit in `06_Prototypes/ReaderPrototype/SPEC.md` |
| 3 | Housekeeping — gitignore + repo-character flag | ~20 min | `.gitignore` edits + a decision note for Ahab |
| 4 | v0.2 audit doc | ~1 session | `04_Audits/AUDIT_2026-05-22_meditations-v02.md` |

Phases 1–3 produce mechanical edits with no decisions required from Ahab. Phase 4 produces an audit doc plus a list of decisions for Ahab (do not decide them in-doc).

This roadmap is research-repo work only. The engine repo is not touched. SPEC-004 drafting and SPEC-005 engine implementation are explicitly out of scope.

---

## Required reading

In the research repo:

1. `03_Format_Spec/LUN-FORMAT_v0.2.md` — the document being patched in Phase 1 and audited against in Phase 4.
2. `06_Prototypes/ReaderPrototype/SPEC.md` — especially § "Findings produced during v1 build (2026-05-22)". This section is the source of truth for Phase 1 patches and Phase 2 fix.
3. `06_Prototypes/ReaderPrototype/REPORT_2026-05-22_reader-v0.2-tauri-document-reconstruction.md` — context for the reader's current state and what was tested.
4. `01_Specs/implemented/SPEC-001_orphan-claims.md`, `SPEC-002_portable-ids.md`, `SPEC-003_meaningful-confidence.md`, `SPEC-006_v02-hygiene-bundle.md` — the four v0.2 specs; their invariants are what the Phase 4 audit checks.
5. `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md` — the v0.1-era audit. Use only for structural template (section headers, style); the cartridge it audits is not the canonical reference any more. Do not import its findings into the v0.2 audit.
6. `08_Journal/2026-05-21.md` — status sweep that motivated this work; lists the carried-forward Phase 5 deferrals the audit should be aware of.
7. `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun` — the cartridge under audit in Phase 4. Built 2026-05-22 against engine commit `325c68b`. 3813 nodes, 1106 extractions, 459 embeddings.

## Pre-flight checks

Run each before starting Phase 1.

### Check 1 — Research repo at expected path

```bash
ls -d "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/"
```

Expected: directory exists. All paths in this roadmap are relative to it.

### Check 2 — Reference cartridge present

```bash
ls -lh "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun"
```

Expected: file exists, ~2.5MB. If missing, Phase 4 cannot proceed and Phases 1–3 should be done first while the cartridge is rebuilt.

### Check 3 — Reader SPEC findings section exists

```bash
grep -n "Findings produced during v1 build" "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/06_Prototypes/ReaderPrototype/SPEC.md"
```

Expected: one match. The section is the source of truth for Phases 1 and 2; if it's missing or has been edited away, stop and ask.

### Check 4 — `sqlite3` CLI available

```bash
sqlite3 --version
```

Expected: any 3.x version. Used in Phase 4 for the audit queries.

### Check 5 — `LUN-FORMAT_v0.2.md` last-modified date

```bash
ls -l "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/03_Format_Spec/LUN-FORMAT_v0.2.md"
```

Expected: dated 2026-05-21 or 2026-05-22 (the original draft and any subsequent patches). If it was modified later than the reader SPEC, someone else has been editing it; re-read it in full before patching.

---

## Phase 1 — Format-spec patch from reader findings

**Source of truth:** `06_Prototypes/ReaderPrototype/SPEC.md` § "Findings produced during v1 build (2026-05-22)".

The reader's v1 build surfaced four documented gaps in `LUN-FORMAT_v0.2.md`. Patch all four. Do not invent additional findings; the audit (Phase 4) is the place for new findings.

### 1.1 — `doc_nodes.content` is nullable

In `LUN-FORMAT_v0.2.md` § "Schema — knowledge cartridges" (or wherever the `doc_nodes` table is documented), update the `content` column to be explicitly nullable. The reference cartridge has `NULL` content on the `document` row and on container nodes whose text lives in sentence children.

Acceptance: column documented as `TEXT` (nullable, no `NOT NULL` constraint), with a short note that container nodes (`document`, sometimes `section`/`paragraph`) may carry `NULL` and reader implementations should treat absent content as "render from children."

### 1.2 — `doc_nodes.meta_json` per-source shapes

The format spec is currently silent on what `meta_json` actually contains per source format. Add a per-source-format subsection documenting the two shapes observed in v0.2 cartridges:

- **PDF source:** `{"page_num": N}` (integer) on every node type (`document`, `section`, `paragraph`, `sentence`). No `level`, no `title`, no source-format-specific fields beyond `page_num`.
- **Markdown source:** `{"title": "..."}`, `{"level": N}` (heading depth, integer), `{"src": "...", "language": "..."}` (code blocks) — present on the corresponding node types.

Acceptance: a subsection or table under `doc_nodes` schema describing both shapes, with a note that reader implementations should not assume cross-format key presence.

### 1.3 — `claim_sources.claim_ulid` / `node_ulid` shadow columns

The format spec's `claim_sources` schema block does not mention the ULID shadow columns even though SPEC-002 Phase 3 added them and they ship in the reference cartridge. Add them.

Acceptance: `claim_ulid TEXT` and `node_ulid TEXT` (both nullable in v0.2, with a forward reference to SPEC-002's D5 plan for `NOT NULL` + composite PK in v0.3) appear in the `claim_sources` schema documentation.

### 1.4 — `nexus_refs` table

`nexus_refs` exists in the reference cartridge (schema: `(local_node_id INTEGER, node_type TEXT) PK, nexus_node_id TEXT, promoted_at INTEGER`) but is unpopulated and undocumented in the format spec. Two valid resolutions:

- **(a)** Document the table in the format spec under a "v0.2 reserved tables, populated in v0.3" subsection. Acknowledge it's a placeholder for the cross-cartridge promotion surface that SPEC-005 / SPEC-004 will consume.
- **(b)** Note in the format spec that the builder is creating the table prematurely and a v0.2.1 builder patch should defer creation until the consumer spec lands. This is the stricter read: v0.2 cartridges should not carry schema the format spec doesn't sanction.

Pick (a). The table is harmless when empty, the builder already creates it, breaking the reference cartridge to chase strict-form purity isn't worth it, and (b) is also a builder-side decision that lives in the engine repo, not here. Document accordingly.

Acceptance: a "Reserved tables" subsection (or similar) under the schema section with the `nexus_refs` schema and a one-line description of intent.

### Phase 1 acceptance (overall)

- All four patches present in `LUN-FORMAT_v0.2.md`.
- `git diff` of the file shows only additions and clarifications — no existing v0.2 contract has been weakened or removed.
- A grep for "Priests and Programmers" or "Lansing" against the modified file returns zero matches (those names are not used as examples in any new content; Meditations is the canonical example going forward).

---

## Phase 2 — Reader SPEC.md acceptance-criterion fix

**Source:** `06_Prototypes/ReaderPrototype/SPEC.md` § Acceptance criteria item 3, and the corresponding finding ("Section nesting — NEW FINDING") in the same file's Findings section.

Acceptance criterion 3 currently says "Expanding the root document reveals 128 direct section children and 176 sections total across the full tree." The Findings section flagged this as previously incorrect (the original SPEC text said "176 sections directly under the document" or similar). Verify the wording matches the finding's resolution: "128 direct + 48 nested = 176 total."

If the criterion is already correctly worded (it may have been fixed when the finding was logged), this phase is a verify-and-skip; report "no change needed" and move on.

### Phase 2 acceptance

- Acceptance criterion 3 in `06_Prototypes/ReaderPrototype/SPEC.md` accurately reflects 128 direct children + 48 nested = 176 total.
- No other edits in this file.

---

## Phase 3 — Housekeeping

### 3.1 — `ruvector.db` gitignore

Two `ruvector.db` files exist that should not be tracked:

- `ruvector.db` at the research-repo root (1.5MB)
- `06_Prototypes/ReaderPrototype/ruvector.db`

There may also be one at `10_Builder/ruvector.db`. Find any others before patching `.gitignore`:

```bash
find "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/" \
  -name "ruvector.db" -not -path "*/.git/*" 2>/dev/null
```

Edit `.gitignore` (create at repo root if missing) to include:

```
ruvector.db
**/ruvector.db
```

If any `ruvector.db` is currently git-tracked, follow up with `git rm --cached <path>` for each so they stop appearing in future diffs. Do not delete the files from disk — they may be live working state for a tool Ahab uses.

Acceptance: `.gitignore` covers all `ruvector.db` locations; `git status` after the change does not list any `ruvector.db` as modified or untracked.

### 3.2 — Repo-character decision flag for Ahab

The reader SPEC names a real question: the repo's README declares it "specification and research only — implementation lives in the main Luna codebase," but `10_Builder/` and `06_Prototypes/ReaderPrototype/` are both implementation. The SPEC flags this as a v0.3 README-sweep concern.

**Do not decide this in Phase 3.** Instead, append a short note to `08_Journal/2026-05-22.md` (or create that file if it doesn't exist) under a "Decisions pending from Ahab" heading:

> **Repo-character question.** README still says "spec and research only." `10_Builder/` and `06_Prototypes/ReaderPrototype/` are implementation. Two options: (a) carve out `06_Prototypes/` and `10_Builder/` as explicit exceptions in the README, keeping the "spec-and-research-leaning" framing; (b) retitle the repo's character to acknowledge that it now hosts working prototypes against the format. Surfaced by the reader prototype SPEC.md § "Repo character note."

Phase 3 acceptance: gitignore patched, decision note logged.

---

## Phase 4 — v0.2 audit

The reader exists partly for this audit. The audit doc is the v0.2-era baseline against which future cartridges and specs are measured.

### Output path

```
/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/04_Audits/AUDIT_2026-05-22_meditations-v02.md
```

### Method

Mirror the v0.1 audit's structure (`04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`), but two differences:

1. **Tooling.** The v0.1 audit used SQLite Fiddle (WASM browser) to prove portability. The v0.2 audit should use *both* — `sqlite3` CLI for the structured queries (so the queries are reproducible in the audit doc) *and* a note that the Tauri reader at `06_Prototypes/ReaderPrototype/` opens and renders the same cartridge end-to-end. The reader is the v0.2-era portability proof; mention it explicitly in § "Validation thesis proof."
2. **Cartridge.** Meditations only. Do not pull comparisons against any v0.1 cartridge; the v0.1 audit stands on its own.

### Sections to produce

Mirror the v0.1 audit's section list:

1. **Purpose** — first audit of a v0.2 production cartridge using generic SQLite tooling + the standalone Tauri reader. Validates the v0.2 portability claim end-to-end.
2. **Method** — `sqlite3` CLI queries listed inline; reader app launched against the same file; cross-reference both.
3. **Meta contents** — `SELECT key, value FROM meta` dump, then findings against the v0.2 meta key contract from `LUN-FORMAT_v0.2.md` (`format_version='0.2'`, `cartridge_kind='knowledge'`, `logprob_base='e'`, `logprob_attribution='response_level'`, `source_filename` basename only, `deprecated_columns` populated, all required keys present).
4. **Document tree** — node-type counts (`SELECT type, COUNT(*) FROM doc_nodes GROUP BY type`), root structure (128 direct section children, 176 sections total per the reader SPEC finding), nullable-content audit (how many nodes carry NULL content and which types).
5. **Extractions** — counts by type (claims 512, entities 532, summaries 62), `extraction_method` distribution, raw-signals presence (`llm_logprob_sum` and `llm_token_count` paired-NULL check per SPEC-003).
6. **Claim anchoring** — `anchor_status` distribution (458 anchored / 54 match_failed / 0 synthesized / 0 filtered / 0 unknown-on-claims), entity-anchor distribution (all 532 entities `unknown` per SPEC-001 scope), `claim_sources` integrity (every anchored claim has at least one source node; every `claim_sources` row points at a valid `doc_nodes` row).
7. **Schema sanity** — `PRAGMA application_id` returns `0x4C554E43`, `PRAGMA user_version` returns 2, `PRAGMA journal_mode` returns `delete`, FTS5 `nodes_fts` table present and queryable, `nexus_refs` table present and empty, ULID columns populated on `doc_nodes` and `extractions` with valid first-char `[0-7]`.
8. **Portability proof** (renamed from v0.1's "Validation thesis proof") — explicit statement that the cartridge was opened, validated, browsed, and searched through the Tauri reader at `06_Prototypes/ReaderPrototype/` with zero Luna runtime dependencies. Reference the reader's 21 passing Rust tests as additional evidence.
9. **Summary of findings** — bullet list, severities (high/medium/low/positive), forward references to follow-up work (SPEC-004, SPEC-005 engine-impl, format-spec patches if any new ones surface, builder patches if any surface).
10. **Recommended follow-ups** — actionable items for the next session, separated into research-repo items and engine-repo items.

### Audit-specific guardrails

- **Reader findings (Phase 1) are already in scope.** The audit should not re-discover the four findings from the reader SPEC; it should reference them as "documented in `06_Prototypes/ReaderPrototype/SPEC.md` § Findings, patched into format spec on 2026-05-22 (Phase 1)." New findings only.
- **Carried-forward Phase 5 deferrals from `08_Journal/2026-05-21.md` are known and tracked.** Do not re-litigate them in the audit unless they surface as a concrete data-quality issue in the Meditations cartridge. The audit's job is to describe what the cartridge *is*, not to relitigate decisions.
- **No spec drafting in the audit.** If the audit surfaces something that warrants a new spec (e.g., a v0.3 cleanup item), add it to Recommended follow-ups and stop. Drafting belongs to a separate session.
- **No comparison cartridge.** Don't pull v0.1 numbers for contrast. The v0.1 audit stands alone; this one stands alone.

### Phase 4 acceptance

- `04_Audits/AUDIT_2026-05-22_meditations-v02.md` exists with all ten sections.
- All numeric findings (counts, distributions, etc.) are reproducible from `sqlite3` queries shown inline in the doc.
- Findings carry severities and forward references.
- No "Priests and Programmers" / "Lansing" references in any new content.
- `git diff` shows the audit file as the only new file in `04_Audits/`.

---

## What NOT to do

- **No examples using "Priests and Programmers" or "Lansing"** in any new content (format-spec patches, audit doc, journal entries, anywhere). Marcus Aurelius Meditations is the canonical example going forward. Existing historical references in already-implemented specs, prior handoffs, and the v0.1 audit are frozen as historical record — do not scrub them. This constraint applies to *new* writing only.
- **No format-spec changes outside the four findings in Phase 1.** If the audit surfaces a fifth issue, add it to the audit's Recommended follow-ups; don't sneak it into the format spec.
- **No spec drafting.** SPEC-004 (multi-axis weights) is not in scope. SPEC-005 engine-side implementation is not in scope — that's engine-repo work and lives in a future engine-side handoff.
- **No engine-repo edits.** This roadmap is research-repo work only. The engine repo at `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/` is not touched.
- **No deciding the repo-character question.** Phase 3 logs it for Ahab; Ahab decides.
- **No semantic search or annotations work.** Both are v2 reader hooks per the reader SPEC. Out of scope here.
- **No cartridge rebuild.** The Meditations cartridge at `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun` is the audit subject as-is. If something looks wrong with the cartridge during the audit, document it as a finding; do not rebuild.

---

## Reporting back

After each phase, append a short status note to `08_Journal/2026-05-22.md` (create the file if it doesn't exist). One block per completed phase:

```
## Phase N — <subject> (completed)

- What changed (one line per file touched)
- Anything unexpected
- Any decisions surfaced for Ahab
```

After Phase 4, the closing note should additionally include:

1. **Audit headline** — one-sentence summary of the cartridge's v0.2 contract health.
2. **New findings count** — by severity.
3. **Decisions for Ahab** — anything the audit surfaced that needs a decision (e.g., repo-character question from Phase 3, SPEC-004 prioritization, etc.).
4. **Cross-references** — link to the audit doc from the journal entry.

Keep the note tight; the audit doc itself is the substantive output.

---

## Cross-references

- Reader SPEC (source of Phase 1 + Phase 2): `06_Prototypes/ReaderPrototype/SPEC.md`
- Reader REPORT (context): `06_Prototypes/ReaderPrototype/REPORT_2026-05-22_reader-v0.2-tauri-document-reconstruction.md`
- Format spec (target of Phase 1): `03_Format_Spec/LUN-FORMAT_v0.2.md`
- Reference cartridge (target of Phase 4): `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun`
- Audit template (Phase 4 structure only): `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`
- Status sweep context: `08_Journal/2026-05-21.md`
- Engine commit the cartridge was built against: `325c68b8d322e8c337baa47f110de4947c2b41e0` (per `10_Builder/SOURCE_COMMIT`)
