# SPEC-016: Read-only cross-project sync watcher

**Status:** active
**Severity:** medium
**Author:** Ahab
**Created:** 2026-08-17
**Last updated:** 2026-08-17
**Affects format version:** none — project governance/tooling only, no cartridge schema change

---

## Problem statement

Open-Looney and Luna Engine share a real contract boundary but keep separate
authorities. Open-Looney owns `.lun` format/spec/Reader governance; Luna Engine
owns runtime behavior, MCP tools, UI/admin surfaces, live DB/process operations,
and implementation state. Today that boundary is maintained by handoffs, ledger
cross-references, and human memory. When one repo changes a cross-boundary fact,
the other repo can lag silently: SPEC-014 was merged in Engine for three weeks
before Open-Looney's ledger noticed, and Engine's wiki currently carries a
version disagreement between control-plane files. The missing component is not
another source of truth, but a read-only watcher that detects drift and tells the
right owner what needs review.

## Observed evidence

**Open-Looney has a canonical ledger.** `project_organization.json` declares
`ProjectManager/TODO_LUN_Development_2026-07-20.md` as `canonical_ledger`. The
ProjectManager runbook says chat is not durable state and `.lun` development
items raised in chat should land on that ledger.

**Luna Engine has a separate canonical ledger.** Its `project_organization.json`,
`ProjectManager/README.md`, `AGENTS.md`, and `CLAUDE.md` all point at
`ProjectManager/TODO_Project_Organization_And_Cleanup_2026-07-10.md` as the
active project-management ledger.

**The repos already cross-reference each other, but only manually.**
Open-Looney's SPEC-014 closeout handoff states that two Engine runtime defects
found during verification were logged in the Engine ledger, not Open-Looney:

1. MCP `aibrarian_*` uses a cartridge-blind fallback `AiBrarianEngine`.
2. v0.3 search returns incomparable score scales in one response.

Those entries do exist in the Engine ledger. The scoring defect is further
cross-referenced back to Open-Looney SPEC-015, with Engine action intentionally
deferred until SPEC-015 is accepted.

**Manual tracking has already missed a merge.** Open-Looney's ledger records
that SPEC-014 vision embeddings merged to Engine `main` on 2026-07-27, but the
Open-Looney ledger still said "NOT MERGED" until 2026-08-15 because the Engine
merge skipped the normal PR-triggered closeout rhythm.

**Version-control drift is observable now.** Luna Engine's
`Docs/Design/SystemsArchitecture/WIKI_VERSIONING.md` says current wiki version
`v2.23.2`, while `Docs/Design/SystemsArchitecture/WIKI_HOME.md` says
`v2.23.0`. Open-Looney's own versioning policy names this exact Engine-style
duplicated-version drift as a failure mode.

**Ledger evidence can be local but not durable.** The Engine ledger references
`Docs/Reports/REPORT_Luna_Project_Audit_2026-08-15.md` and
`Docs/Reports/PROJECT_ORGANIZATION_REPORT_2026-08-15.md`; both files exist in
the current checkout but are untracked. That may be intentional, but the state
is precisely the sort of "referenced but not durable" condition a watcher should
surface.

## Root cause analysis

The root cause is **split authority without a mechanical boundary check**.

The split is correct. A single combined backlog would blur format governance,
runtime defects, live-process work, Reader work, and UI/admin work into one
undifferentiated queue. Each repo should keep owning its own versioning and task
state.

The missing piece is a mechanically repeatable answer to four questions:

1. Did one repo change a cross-boundary surface after the other repo's last
   recorded sync point?
2. Does every cross-reference in a ledger point to a real, durable target?
3. Do each repo's own version-control files agree internally?
4. If drift exists, which repo owns the next human/agent pass?

Without that check, the project depends on conversational continuity. That is
the same failure class the ProjectManager ledgers were created to avoid.

## Proposed solution

Add a read-only cross-project sync watcher. The watcher inspects both repos,
builds a current snapshot, runs detectors, and emits a report. It MUST NOT edit
ledgers, specs, version files, changelogs, pass trackers, source files, git
state, or live databases.

### 1. Ownership model

Each project remains authoritative for its own state.

| Project | Owns | Does not own |
|---|---|---|
| Open-Looney | `.lun` format versions, LUNC/LUNM specs, Reader prototype contract, cartridge governance, `.lun` wiki versioning | Engine runtime bug backlog, live process state, UI/admin implementation details |
| Luna Engine | Runtime behavior, MCP tools, routes, Nexus/AiBrarian implementation, UI/admin surfaces, live DB/process tasks, Engine wiki versioning | `.lun` format law, Open-Looney spec lifecycle, Reader release governance |
| Watcher | Drift detection, evidence, suggested owner, suggested severity, suggested native bump type | Any authoritative mutation |

### 2. Cross-project manifest

The watcher reads a small manifest that declares projects and surfaces. The
manifest is configuration, not governance state. It may live in Open-Looney
under `ProjectManager/cross_project_sync.yaml` or in a neutral root under
`/Users/zayneamason/_HeyLuna_BETA/ProjectSync/`; acceptance must choose one.

Sketch:

```yaml
sync_report_version: v0.1.0
projects:
  open_looney:
    path: /Users/zayneamason/_HeyLuna_BETA/Apps/lun Development
    canonical_ledger: ProjectManager/TODO_LUN_Development_2026-07-20.md
    version_surfaces:
      wiki_versioning: ProjectManager/Looney-WIKI/WIKI_VERSIONING.md
      wiki_home: ProjectManager/Looney-WIKI/WIKI_HOME.md
      pass_tracker: ProjectManager/Looney-WIKI/WIKI_PASS_TRACKER.md
      changelog: ProjectManager/Looney-WIKI/WIKI_CHANGELOG.md
      format_readme: 00_README/README.md
    watched_terms:
      - SPEC-
      - Engine PR
      - LUNM
      - LUNC
      - cartridge
      - Reader
      - image_embeddings
      - aibrarian
      - MCP
      - Nexus
  luna_engine:
    path: /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root
    canonical_ledger: ProjectManager/TODO_Project_Organization_And_Cleanup_2026-07-10.md
    version_surfaces:
      wiki_versioning: Docs/Design/SystemsArchitecture/WIKI_VERSIONING.md
      wiki_home: Docs/Design/SystemsArchitecture/WIKI_HOME.md
      pass_tracker: Docs/Design/SystemsArchitecture/WIKI_PASS_TRACKER.md
      changelog: Docs/Design/SystemsArchitecture/WIKI_CHANGELOG.md
    watched_terms:
      - cartridge
      - image_embeddings
      - aibrarian
      - MCP
      - Nexus
      - Reader
      - LUNM
      - LUNC
      - SPEC-
```

The watcher may support additional projects later, but v1 is exactly two
projects: Open-Looney and Luna Engine.

### 3. Snapshot model

For each project, collect:

```yaml
project:
  name: open_looney
  path: ...
  branch: main
  head: e85e973
  upstream: origin/main
  dirty: false
  canonical_ledger:
    path: ProjectManager/TODO_LUN_Development_2026-07-20.md
    frontmatter_updated: 2026-08-15
    exists: true
  version_surfaces:
    wiki_versioning: v0.6.1
    wiki_home: v0.6.1
    pass_tracker: v0.6.1
  open_items:
    count: 12
    sampled_refs:
      - SPEC-015
      - GDAL / COG media-family RFC
  tracked_refs:
    - path: 01_Specs/active/SPEC-015_retrieval-score-comparability.md
      kind: spec
      status: active
```

Snapshot collection is read-only and must use stable local commands or parsers:
`git status --porcelain`, `git rev-parse`, `git branch --show-current`, and
plain file parsing. It must not require a running backend.

### 4. Detector classes

#### 4.1 Native version disagreement

A project fails this detector when its own declared version surfaces disagree.

Example finding:

```yaml
id: engine.version_disagreement
severity: warn
owner: luna_engine
evidence:
  - Docs/Design/SystemsArchitecture/WIKI_VERSIONING.md: v2.23.2
  - Docs/Design/SystemsArchitecture/WIKI_HOME.md: v2.23.0
suggested_action: Run an Engine wiki version resync pass.
suggested_bump: PATCH if only resyncing duplicated version text.
```

#### 4.2 Cross-reference target missing

A project warns when its ledger or handoff names another repo path, spec, report,
PR, or commit that cannot be found locally.

This detector distinguishes:

- `missing_file` — referenced path absent.
- `untracked_file` — referenced path exists but is not in `git ls-files`.
- `missing_commit` — referenced SHA not present in local git object database.
- `missing_spec` — referenced `SPEC-NNN` absent from the expected lifecycle tree.

#### 4.3 Cross-boundary change without peer mention

A project warns when recent git history or ledger updates mention watched terms
that are likely owned by the other project and the peer ledger has no mention
after its last sync-relevant date.

Examples:

- Engine commit touches `src/luna/cartridge/`, `src/luna/substrate/aibrarian*`,
  `src/luna_mcp/tools/aibrarian.py`, or Nexus hydration code; Open-Looney has no
  matching spec/ledger/handoff mention.
- Open-Looney promotes a spec to `accepted` or `implemented`; Engine ledger has
  no implementation, no "not needed" note, and no explicit deferral.

This detector must be conservative. It raises `review` findings, not failures,
unless a required path or version surface is missing.

#### 4.4 Ledger stale-date suspicion

A ledger warns when:

- Its frontmatter `updated:` date predates a referenced current-state claim.
- It references a generated report that is untracked.
- It says an item is complete but the cited file/commit is absent.

This detector does not decide truth. It says "the evidence chain is not durable
enough to trust without review."

#### 4.5 Dirty-worktree context

A project warns when it is dirty. Dirty state is not an error; the report must
record it so any drift claim is interpreted correctly.

Example:

```yaml
id: engine.dirty_worktree
severity: info
owner: luna_engine
evidence:
  branch: feat/studio-glareshield
  modified:
    - config/frontend_config.json
  untracked:
    - Docs/Reports/REPORT_Luna_Project_Audit_2026-08-15.md
meaning: Findings may depend on local state not present on the remote.
```

### 5. CLI contract

The initial implementation exposes only read/report commands:

```bash
python3 scripts/cross_project_sync.py check
python3 scripts/cross_project_sync.py check --json
python3 scripts/cross_project_sync.py report --out /path/to/report.md
```

There is deliberately no `--apply`, `--bump`, `--commit`, or `--fix` flag.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | No findings above `info` |
| 1 | One or more `warn` findings |
| 2 | One or more `fail` findings |
| 3 | Tool/config error, such as missing manifest or unreadable repo |

### 6. Report contract

Markdown reports should be concise but evidence-complete:

```markdown
# Cross-Project Sync Report

Generated: 2026-08-17T...
Watcher schema: v0.1.0

## Baselines

| Project | Branch | HEAD | Dirty | Ledger | Ledger updated | Wiki/docs version |
|---|---|---|---|---|---|---|

## Findings

### WARN engine.version_disagreement

Owner: Luna Engine
Evidence:
- `WIKI_VERSIONING.md`: `v2.23.2`
- `WIKI_HOME.md`: `v2.23.0`

Suggested action: run native Engine wiki resync pass.
Suggested bump: PATCH if only version text changes.

## Cross-References

| Ref | Source | Target | Status |
|---|---|---|---|
```

JSON reports should preserve the same fields for future automation.

### 7. Versioning contract

The watcher has its own schema/rules version, independent from both projects.

```yaml
sync_report_version: v0.1.0
```

Watcher version bump rules:

| Bump | Trigger |
|---|---|
| MAJOR | Incompatible report schema change; ownership model changes; any future mode that can mutate files |
| MINOR | New detector; new watched project; new manifest key; new output section |
| PATCH | Wording change; path correction; parser bug fix that does not change rule meaning |

The watcher MUST NOT bump Open-Looney or Luna Engine versions. It may emit a
`suggested_bump` field, but the native owner must perform the bump through that
repo's existing versioning process.

For example:

- Open-Looney new active spec ⇒ watcher may suggest `MINOR` under
  `ProjectManager/Looney-WIKI/WIKI_VERSIONING.md`, but must not edit it.
- Engine duplicated version text repaired only in `WIKI_HOME.md` ⇒ watcher may
  suggest `PATCH`, but must not edit it.
- `.lun` `user_version` / `meta.format_version` changes ⇒ watcher must point to
  Open-Looney format governance; it must not infer a format bump by itself.

### 8. Safety boundaries

The watcher is prohibited from:

- editing files;
- moving specs between lifecycle folders;
- checking off ledger items;
- writing changelog entries;
- changing `WIKI_VERSIONING.md`;
- changing `WIKI_PASS_TRACKER.md`;
- changing `WIKI_HOME.md`;
- running `git add`, `git commit`, `git push`, `git reset`, or `git checkout`;
- starting or stopping Luna;
- opening or mutating live `.lun` / WAL / SHM files;
- calling network APIs.

If a future workflow wants patch generation, it must be a separate tool or a
separate command that emits a patch to stdout or a temp path for human review.
That future mode would be a watcher MAJOR bump and would require its own spec
amendment.

## Schema changes

None to `.lun` cartridges or LUNM matrices.

Optional project file in implementation:

```yaml
# ProjectManager/cross_project_sync.yaml
sync_report_version: v0.1.0
projects: ...
detectors:
  native_version_disagreement: true
  cross_reference_target_missing: true
  cross_boundary_change_without_peer_mention: true
  ledger_stale_date_suspicion: true
  dirty_worktree_context: true
```

## Behavioral changes

No runtime behavior changes. The only behavior added is a local developer
command that reads repository metadata and produces findings.

The watcher must be deterministic for a fixed checkout. It may include current
time in reports, but detector outcomes should derive from repository state.

## Migration path

Read-compatible and forward-compatible for all existing cartridges and runtime
matrices. No file format migration.

Project-management migration is optional:

1. Add the manifest.
2. Add the read-only script.
3. Run it manually.
4. If useful, add a warning-only scheduled job or pre-commit hook later.

## Validation rules

Implementation must include tests for each detector using temporary fixture
repos or fixture directories. Tests must not require real Luna Engine or
Open-Looney paths.

Required validation cases:

```python
def test_version_disagreement_flags_peer_files():
    snapshot = project_with_versions("v2.23.2", home="v2.23.0")
    findings = run_detectors(snapshot)
    assert finding("native_version_disagreement", severity="warn") in findings


def test_cross_reference_to_untracked_file_warns():
    repo = fixture_repo_with_untracked("Docs/Reports/local.md")
    ledger_mentions(repo, "Docs/Reports/local.md")
    findings = run_detectors(repo)
    assert finding("cross_reference_target_missing", subtype="untracked_file")


def test_dirty_worktree_is_context_not_failure():
    repo = dirty_fixture_repo()
    finding = run_detectors(repo)["dirty_worktree_context"]
    assert finding.severity == "info"


def test_no_mutation_commands_are_called(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", recorder(calls))
    run_check()
    assert not any(call.argv[:2] in [["git", "add"], ["git", "commit"],
                                     ["git", "push"], ["git", "reset"]]
                   for call in calls)
```

Acceptance requires one live read-only smoke against both local repos proving:

- Open-Looney snapshot includes branch, HEAD, dirty state, ledger path,
  `updated:`, and wiki version.
- Luna Engine snapshot includes the same.
- Current Engine wiki version disagreement is detected while it still exists.
- No files are modified by the run (`git status --short` before and after is
  byte-identical in both repos).

## Governance implications

- **Ledger / annotation events:** The watcher does not write cartridge ledgers or
  ProjectManager ledgers. It may recommend ledger updates.
- **Multi-axis imprint weights:** N/A.
- **Actor roles:** N/A.
- **Cross-cartridge traversal:** N/A directly; the watcher can flag when Engine
  runtime retrieval changes imply Open-Looney spec review.
- **Memory Matrix integration:** None. It must not open or mutate live
  `memory_matrix.lun`.
- **Project versioning:** The watcher may suggest native bump type but must not
  apply one.
- **Cross-repo authority:** The watcher formalizes the split: Open-Looney owns
  format/spec state, Engine owns runtime implementation state.

## Alternatives considered

**A shared combined ledger.** Rejected. It would collapse two legitimate
authorities into one queue and make ownership less clear.

**Automatic ledger edits.** Rejected. The first version must not turn detection
into mutation. A false positive should create a review finding, not rewrite
project governance.

**Automatic version bumps.** Rejected. Version bumps are semantic governance
acts. The watcher can identify the probable bump class, but only the owning
repo's native pass should change version files.

**GitHub Actions only.** Rejected for v1. Local checkout state matters here:
dirty files, untracked reports, live branch, and local-only handoffs are part of
the risk model. GitHub Actions may be added later for remote-only checks.

**Runtime health watcher.** Rejected for this spec. This is not a process
monitor and should not start Luna, hit live APIs, or inspect DB contents. Runtime
health belongs to Engine diagnostics.

## Open questions

These block acceptance.

1. **Manifest location.** Should the manifest live in Open-Looney, Luna Engine,
   or a neutral `_HeyLuna_BETA/ProjectSync/` home? Neutral is cleanest, but may
   fall outside both repos' existing governance.
2. **Report destination.** Should reports be generated only to stdout/temp paths,
   or may a human run write them under `Docs/Reports/` / `ProjectManager/`?
3. **Scheduling.** Should scheduled runs use local `launchd`, a repo pre-commit
   warning hook, GitHub Actions, or remain manual until detector quality is
   proven?
4. **Baseline memory.** Should the watcher store the previous run's baseline, or
   remain stateless and derive all findings from the current checkouts?
5. **Severity thresholds.** Which findings are `warn` versus `fail`? Proposed:
   missing ledger/config/version surface is `fail`; disagreement, dirty state,
   untracked referenced files, and peer-review candidates are `warn` or `info`.
6. **Cross-boundary path set.** Which Engine paths are strong enough to imply
   Open-Looney review? Start conservative: cartridge builders/readers,
   `aibrarian`, Nexus hydration, MCP `aibrarian_*`, Reader-facing contract
   routes.
7. **No-network rule.** Should the watcher ever verify GitHub PR state, or is
   local git state sufficient? v1 should be local-only.

## Dependencies

- Open-Looney ProjectManager canonical ledger and wiki control plane.
- Luna Engine ProjectManager canonical ledger.
- Local git metadata for both repos.
- Python 3 standard library only for v1, unless acceptance explicitly approves a
  dependency.

## Non-goals

1. No `.lun` format change.
2. No cartridge rebuild or backfill.
3. No live backend requirement.
4. No live DB reads or writes.
5. No automatic ledger edits.
6. No automatic version bumps.
7. No automatic commits, branches, PRs, or pushes.
8. No replacement for either ProjectManager ledger.

## Implementation notes

(Filled in when status moves to `implemented`)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
