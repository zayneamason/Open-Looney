---
doc_type: plan
status: active
created: 2026-08-17
updated: 2026-08-17
tags:
  - projectmanager
  - cross-project-sync
  - spec-016
  - warning-cleanup
---

# PLAN: SPEC-016 Remaining Warning Cleanup

## Objective

Reduce the remaining SPEC-016 warn findings without weakening the watcher signal model.
The baseline is `ProjectManager/Reports/REPORT_2026-08-17_spec-016_noise_reduction_baseline.md`:
`31` findings total, `30 warn`, `1 info`, no native version disagreement, and no mutations.

Success means the watcher still flags canonical-ledger evidence problems, while stale
or intentionally local evidence is rewritten into durable ledger language or fixed in
the owning repo.

## Scope

In scope:

- Open-Looney ledger cleanup for local `.app`/`.dmg` artifact references.
- Engine ledger cleanup plan for missing commit/file references and untracked evidence.
- A decision table for each remaining warning class: fix reference, track evidence,
  rewrite as historical checksum/path note, or defer as Engine-owned work.
- Re-running the read-only watcher smoke after each owned cleanup.

Out of scope until explicitly requested:

- Mutating Luna Engine from the Open-Looney pass.
- Downgrading canonical-ledger findings to summary just to reduce the count.
- Network/GitHub validation.
- Process control, scheduled automation, or live `.lun` access.

## Slice 1 — Open-Looney-Owned Cleanup

Tasks:

- Inspect the two Open-Looney ledger warnings:
  - `06_Prototypes/LunmReaderPrototype/src-tauri/target/debug/bundle/macos/lunm-reader.app`
  - `06_Prototypes/ReaderPrototype/src-tauri/target/release/bundle/dmg/lun-reader_0.3.4_aarch64.dmg`
- Decide whether these should remain concrete path evidence or become historical artifact
  records using commit IDs, checksums, and build command evidence.
- If rewritten, remove concrete untracked build-output paths from canonical ledger prose
  so generated local artifacts do not stay actionable forever.

Verification:

```bash
python3 scripts/wiki_check.py
python3 scripts/cross_project_sync.py check --json > /tmp/cross-project-sync-open-looney-cleanup.json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync-open-looney-cleanup.md
```

Expected result:

- Open-Looney untracked artifact warnings drop from `2` to `0`.
- Engine warnings and Engine dirty-worktree info remain unchanged.

Result 2026-08-17:

- Completed. The two concrete generated artifact paths were rewritten as historical
  build/checksum records in the canonical Open-Looney ledger.
- Verification wrote `/tmp/cross-project-sync-open-looney-cleanup.json` and
  `/tmp/cross-project-sync-open-looney-cleanup.md`.
- Staged-doc smoke reported `30` findings total (`28 warn`, `2 info`), with
  Open-Looney untracked artifact warnings at `0`.
- `mutation_performed=false`; Open-Looney and Luna Engine `git status --short`
  snapshots were byte-identical before/after.

## Slice 2 — Engine Ledger Cleanup Design

Tasks:

- Read the Engine canonical ledger lines behind:
  - missing commit `ed8bb09e`;
  - missing scripts at ledger line `305`;
  - `config/identity_bypass.json`;
  - `Docs/VoiceSystem/luna_voice_state_surface.html`;
  - untracked report/probe/build evidence.
- For each class, mark one disposition:
  - `correct_reference`: the artifact moved or was renamed;
  - `track_evidence`: the artifact is source/report evidence that should be committed in Engine;
  - `historical_note`: the artifact was intentionally local/generated and should be rewritten
    as a dated historical note, not a live path;
  - `defer`: needs human/Engine-owner decision.

Verification:

```bash
python3 scripts/cross_project_sync.py check --json > /tmp/cross-project-sync-engine-cleanup-design.json
```

Expected result:

- No Open-Looney mutation to Engine.
- A concrete Engine-owned edit list exists before any Engine commit is attempted.

Result 2026-08-17:

- Completed as read-only design. No Luna Engine files were edited.
- Design report: `ProjectManager/Reports/REPORT_2026-08-17_spec-016_engine_warning_cleanup_design.md`.
- Current Engine branch during inspection: `docs/session-pickup-ui-followups`; dirty state
  included `config/frontend_config.json` plus unrelated untracked local files.
- Dispositions:
  - `track_evidence`: four governed Engine report/pickup files referenced by the canonical
    ledger but currently untracked.
  - `correct_reference`: `Docs/VoiceSystem/luna_voice_state_surface.html` moved to
    `Docs/Design/uiUx/prototypes/luna_voice_state_surface.html`; `config/identity_bypass.json`
    should point at the tracked quarantined sentinel or become non-path prose.
  - `historical_note`: retired transcript scripts, ignored probe data, ignored `dist/`
    build outputs, and the live DB checksum prefix.
  - `defer`: exact Engine commit/edit sequencing until explicit approval to mutate Engine.

## Slice 3 — Engine-Owned Cleanup Pass

Prerequisite:

- Explicit approval to edit Luna Engine.

Tasks:

- Apply only the `correct_reference` and `historical_note` ledger edits in Luna Engine.
- Track only evidence that is intentionally governed source/report evidence.
- Leave local build outputs, credentials, live DBs, WAL/SHM files, logs, and generated probe
  scratch data untracked unless a human explicitly changes repository policy.

Verification:

```bash
python3 scripts/cross_project_sync.py check --json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync-after-engine-cleanup.md
```

Expected result:

- Remaining warnings are limited to true unresolved canonical-ledger evidence issues and
  cross-boundary review candidates.
- Open-Looney and Luna Engine `git status --short` snapshots remain explainable before/after.
