---
doc_type: report
status: current
created: 2026-08-17
updated: 2026-08-17
tags:
  - projectmanager
  - cross-project-sync
  - spec-016
  - watcher
  - baseline
---

# SPEC-016 Noise Reduction Baseline

Generated from the committed watcher after `4ec6350` (`fix: reduce cross-project watcher noise`).

Live commands:

```bash
python3 scripts/cross_project_sync.py check --json > /tmp/cross-project-sync-current.json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync-current.md
```

Both commands exited `1` because warn-level findings remain. The watcher reported
`mutation_performed=false`, and Open-Looney plus Luna Engine `git status --short`
snapshots were byte-identical before and after the smoke pass.

## Baseline

- Schema: `v0.1.0`
- Generated: `2026-08-17T13:59:48+00:00`
- Open-Looney: `main` at `4ec6350cf17c`, clean.
- Luna Engine: `feat/console-last-tab` at `caf9cde79cff`, dirty.
- Native wiki/version disagreement: absent.
- Findings: `31` total (`30 warn`, `1 info`).
- Reference summary rows: `22`.

## Remaining Warn Findings

- Luna Engine canonical ledger: `1` missing commit reference (`ed8bb09e`).
- Luna Engine canonical ledger: `7` missing file references:
  `scripts/automated_export.py`, `scripts/extract_claude_transcripts.js`,
  `scripts/fetch_claude_conversations.py`, `scripts/organize_transcripts.py`,
  `scripts/package.json`, `config/identity_bypass.json`, and
  `Docs/VoiceSystem/luna_voice_state_surface.html`.
- Luna Engine canonical ledger: `18` untracked file references, mostly report/probe/build
  evidence under `Docs/Reports/`, `Docs/Reports/SessionPickups/`,
  `scripts/probes/data/`, `frontend/lunar-studio/diagnostic/dist`, and `frontend/dist`.
- Open-Looney canonical ledger: `2` untracked local build artifact references:
  `06_Prototypes/LunmReaderPrototype/src-tauri/target/debug/bundle/macos/lunm-reader.app`
  and `06_Prototypes/ReaderPrototype/src-tauri/target/release/bundle/dmg/lun-reader_0.3.4_aarch64.dmg`.
- Open-Looney recent history: `2` cross-boundary review candidates.

## Summary-Only Downgrades

The ledger-first policy preserved historical references in `reference_summary` without
emitting warning findings from old handoffs/reports:

- Engine handoff/report/current-plan references include missing commits, external paths,
  local artifacts, missing files, and untracked files as summary data.
- Open-Looney historical handoff references include external paths, missing files,
  moved SPEC lifecycle paths, and untracked local artifacts as summary data.
- Moved SPEC lifecycle paths are classified as `moved_spec_path` instead of actionable
  `missing_file` when the same `SPEC-NNN` exists elsewhere.

## Next Cleanup Targets

1. Engine-owned ledger cleanup: decide whether stale missing file/commit references should
   be corrected, archived, or left as known historical ledger context.
2. Evidence policy: decide which Engine report/probe artifacts referenced from the canonical
   ledger should be tracked, moved to a governed report location, or rewritten as ephemeral
   local evidence.
3. Open-Looney ledger cleanup: decide whether local `.app`/`.dmg` build artifact references
   should stay as historical artifact records or be rewritten to checksum-only evidence.

## Slice 1 Result

Open-Looney-owned cleanup completed on 2026-08-17. The canonical ledger no longer
uses concrete generated `.app` or `.dmg` build-output paths as live evidence targets;
those entries now preserve historical build/checksum evidence without pointing at
untracked local artifacts.

Verification commands:

```bash
python3 scripts/cross_project_sync.py check --json > /tmp/cross-project-sync-open-looney-cleanup.json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync-open-looney-cleanup.md
```

Staged-doc smoke result:

- Findings: `30` total (`28 warn`, `2 info`).
- Open-Looney untracked artifact warnings: `0` (down from `2`).
- Remaining Open-Looney warnings: `2` cross-boundary review candidates.
- Remaining Engine warnings: `26` canonical-ledger reference findings.
- `mutation_performed=false`.
- Open-Looney and Luna Engine `git status --short` snapshots were byte-identical before/after.
