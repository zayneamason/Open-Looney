---
doc_type: plan
status: active
created: 2026-08-17
updated: 2026-08-17
tags:
  - projectmanager
  - cross-project-sync
  - spec-016
  - watcher
---

# PLAN: SPEC-016 Read-only Cross-project Sync Watcher

## Objective

Implement SPEC-016 as a local, read-only drift detector for Open-Looney and Luna
Engine. The tool reports cross-project ledger/version/evidence drift and never
mutates either project's governance state.

Success means a developer can run:

```bash
python3 scripts/cross_project_sync.py check
python3 scripts/cross_project_sync.py check --json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync.md
```

and receive deterministic findings with exact owners and evidence.

## Scope

In scope:

- manifest-driven project configuration;
- local git/file snapshot collection;
- native version-surface comparison;
- missing/untracked cross-reference detection;
- dirty-worktree context;
- conservative cross-boundary review candidates;
- Markdown and JSON output;
- unit tests with fixture repos/directories;
- one live read-only smoke against the two local repos.

Out of scope:

- editing ledgers, specs, version files, changelogs, pass trackers, or wiki
  homes;
- auto-bumping any native project version;
- creating commits, branches, PRs, or pushes;
- starting/stopping Luna;
- opening or mutating live `.lun`, WAL, or SHM files;
- GitHub/network verification;
- scheduled automation.

## Acceptance Decisions

Resolve these before implementation. Defaults are recommended.

1. **Manifest location:** `ProjectManager/cross_project_sync.json`.
   It keeps v1 inside Open-Looney's project-management home, avoids creating an
   ungoverned neutral root, and keeps the watcher standard-library only.
2. **Report destination:** stdout by default; explicit `--out` may write
   anywhere the caller names. Do not auto-write under `Docs/Reports/`.
3. **Scheduling:** manual only for v1.
4. **Baseline memory:** stateless for v1. Every finding derives from current
   checkouts.
5. **Severity floor:** missing configured project/ledger/version surface is
   `fail`; version disagreement, untracked referenced evidence, and dirty
   worktree are `warn`/`info`; cross-boundary candidates are `warn`.
6. **Network:** local-only for v1.

Acceptance of SPEC-016 should either adopt these defaults or edit the spec to
record different decisions.

## Implementation Slices

### Slice 0 — Preflight and Fixtures

Tasks:

- Add `ProjectManager/cross_project_sync.json`.
- Add `scripts/cross_project_sync.py`.
- Add self-contained stdlib tests under `scripts/tests/`.
- Confirm standard-library only: `argparse`, `dataclasses`, `json`, `pathlib`,
  `re`, `subprocess`, `tempfile`, `datetime`.

Verification:

```bash
python3 scripts/cross_project_sync.py --help
python3 -m py_compile scripts/cross_project_sync.py
```

Stop point:

- No detector logic beyond config load and help output.

### Slice 1 — Snapshot Collector

Tasks:

- Parse the manifest.
- For each project, collect:
  - path exists;
  - branch;
  - HEAD SHA;
  - upstream, if configured/available;
  - `git status --porcelain`;
  - dirty boolean plus modified/untracked file lists;
  - canonical ledger existence and frontmatter `updated:`;
  - configured version surface values.
- Implement robust semver extraction per surface using field-specific parsers
  rather than first-match scans.

Verification:

- Fixture repo tests for clean and dirty states.
- Fixture file tests for frontmatter date and version parsing.
- Live smoke prints both project baselines.

Stop point:

- CLI can output snapshots but no findings yet.

### Slice 2 — Native Version Detector

Tasks:

- Compare configured version surfaces within each project.
- Emit `native_version_disagreement` when parsed values disagree.
- Emit `missing_version_surface` when a configured surface is absent or unparsable.

Verification:

- Fixture test: Engine-style `WIKI_VERSIONING.md=v2.23.2`,
  `WIKI_HOME.md=v2.23.0` produces `warn`.
- Fixture test: missing configured version file produces `fail`.
- Live smoke should detect the current Engine version disagreement while it
  remains present.

Stop point:

- No cross-reference parsing yet.

### Slice 3 — Cross-reference Durability Detector

Tasks:

- Extract likely references from ledgers and handoffs:
  - backticked relative paths;
  - repo-relative Markdown links;
  - `SPEC-NNN`;
  - 7+ character hex commit tokens;
  - PR references only as text for v1, no network validation.
- Resolve references against the owning repo and peer repo where paths clearly
  identify one.
- Classify:
  - `missing_file`;
  - `untracked_file`;
  - `missing_commit`;
  - `missing_spec`;
  - `ok`.
- Keep false positives low: unknown/freeform references should be skipped unless
  they look like concrete repo paths or spec IDs.

Verification:

- Fixture tests for missing file, untracked file, missing spec, and present
  tracked file.
- Live smoke should flag Engine audit reports referenced by the Engine ledger as
  untracked if they remain untracked.

Stop point:

- Do not inspect recent git history for semantic ownership yet.

### Slice 4 — Cross-boundary Review Candidates

Tasks:

- Add a conservative path/term rule set from the manifest:
  - Engine paths touching cartridge builders/readers;
  - `src/luna/substrate/aibrarian*`;
  - Nexus hydration;
  - `src/luna_mcp/tools/aibrarian.py`;
  - Open-Looney spec lifecycle changes.
- Compare recent changed files and ledger terms to peer ledger/spec mentions.
- Emit `cross_boundary_review_candidate`, not a hard failure.
- Include why it was raised and which owner should review it.

Verification:

- Fixture test: Engine commit touching `src/luna_mcp/tools/aibrarian.py` with no
  peer mention raises a `warn`.
- Fixture test: same commit with peer ledger mention suppresses the warning.

Stop point:

- No scheduling, no writes.

### Slice 5 — Output Contracts

Tasks:

- Implement stable JSON output.
- Implement Markdown report output.
- Include:
  - generated timestamp;
  - watcher schema version;
  - baseline table;
  - findings grouped by severity and owner;
  - cross-reference table;
  - "no mutation performed" footer.
- Implement exit codes:
  - `0` no findings above `info`;
  - `1` warn findings;
  - `2` fail findings;
  - `3` tool/config error.

Verification:

- Golden-output-style tests for JSON fields, not full timestamp text.
- Markdown smoke writes only to an explicit temp path.

Stop point:

- No repo files are written except the explicit `--out` target requested by the
  user.

### Slice 6 — No-mutation Guard and Live Smoke

Tasks:

- Add a test that forbids mutation commands:
  - `git add`;
  - `git commit`;
  - `git push`;
  - `git reset`;
  - `git checkout`;
  - `git switch`;
  - `rm`;
  - process-control commands.
- Run live read-only smoke:
  - capture `git status --short` in both repos before;
  - run `check --json`;
  - run `report --out /tmp/cross-project-sync.md`;
  - capture `git status --short` in both repos after;
  - assert before/after status is byte-identical.

Verification commands:

```bash
python3 -m py_compile scripts/cross_project_sync.py
python3 scripts/cross_project_sync.py check --json
python3 scripts/cross_project_sync.py report --out /tmp/cross-project-sync.md
```

```bash
python3 -m unittest scripts.tests.test_cross_project_sync
```

Stop point:

- Human review before any hook or scheduled run is proposed.

## File Plan

New files:

- `ProjectManager/cross_project_sync.json`
- `scripts/cross_project_sync.py`
- `scripts/tests/test_cross_project_sync.py`

Existing files to update:

- `ProjectManager/TODO_LUN_Development_2026-07-20.md` — check off implementation
  only after live smoke passes.
- `01_Specs/active/SPEC-016_cross-project-sync-watcher.md` — fill
  Implementation notes and promote only after acceptance/implementation.
- `ProjectManager/Looney-WIKI/*` — native version pass only if spec lifecycle or
  governed docs change.

Do not edit Luna Engine files in this implementation slice. The watcher may read
Luna Engine, but Open-Looney v1 owns the script and manifest.

## Risk Register

| Risk | Mitigation |
|---|---|
| False positives from naive text/path extraction | Start with concrete backticked paths, Markdown links, `SPEC-NNN`, and commit-looking hex only |
| Tool accidentally becomes a second source of truth | No mutation commands, no `--apply`, report-only output |
| Dirty Engine checkout makes findings confusing | Always include dirty context and branch/HEAD in baseline |
| Version parsing repeats the drift problem | Use surface-specific parsers; test WIKI_HOME vs WIKI_VERSIONING disagreement |
| Cross-boundary detector too noisy | Make it conservative and `warn` only; keep path set in manifest |
| Neutral manifest outside repo becomes invisible | Put v1 manifest under Open-Looney ProjectManager |

## Acceptance Checklist

- [x] SPEC-016 open questions resolved or accepted as v1 defaults.
- [x] Manifest added and parsed.
- [x] Snapshot includes both repos.
- [x] Native version disagreement detector implemented.
- [x] Cross-reference durability detector implemented.
- [x] Dirty-worktree context emitted.
- [x] Cross-boundary review candidate detector implemented conservatively.
- [x] JSON output implemented.
- [x] Markdown report output implemented.
- [x] No-mutation test passes.
- [x] Live read-only smoke shows identical before/after git status in both repos.
- [x] ProjectManager ledger updated with implementation result.

Implementation result:

- Manifest format changed from YAML to JSON to preserve standard-library-only v1.
- Verification passed with `python3 -m py_compile scripts/cross_project_sync.py`
  and `python3 -m unittest scripts.tests.test_cross_project_sync`.
- Live smoke wrote `/tmp/cross-project-sync.json` and
  `/tmp/cross-project-sync.md`; both repos' before/after `git status --short`
  snapshots were byte-identical.
- Live findings were warn-level only: 349 findings total, including the expected
  Engine wiki version disagreement, dirty-worktree context for both repos,
  cross-reference durability warnings, and two cross-boundary review candidates.

## Human Gate

Stop after Slice 6 and present:

- command outputs summarized;
- JSON/Markdown report path;
- before/after git status comparison;
- detector findings;
- false-positive notes;
- recommendation on whether to accept SPEC-016 and whether to add any scheduled
  warning-only run later.
