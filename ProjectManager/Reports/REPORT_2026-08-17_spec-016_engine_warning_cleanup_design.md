---
doc_type: report
status: current
created: 2026-08-17
updated: 2026-08-17
tags:
  - projectmanager
  - cross-project-sync
  - spec-016
  - engine-cleanup
  - design
---

# SPEC-016 Engine Warning Cleanup Design

This is Slice 2 from `ProjectManager/Plans/PLAN_2026-08-17_spec-016_remaining-warning-cleanup.md`.
It is read-only design work. No Luna Engine files were edited.

Inspection state:

- Open-Looney: `main`, ahead of `origin/main` by the local SPEC-016 documentation commit.
- Luna Engine: `docs/session-pickup-ui-followups` with existing dirty context:
  `config/frontend_config.json` modified, plus unrelated untracked local files.
- Watcher baseline before this design pass: `29` findings (`28 warn`, `1 info`),
  with Engine warnings concentrated in the canonical ledger.

## Warning Classes

| Class | Count | Source line(s) | Disposition | Engine-owned action |
|---|---:|---|---|---|
| Checksum parsed as commit | 1 | `ProjectManager/TODO_Project_Organization_And_Cleanup_2026-07-10.md:450` | `historical_note` | The target is not a Git object; it is a live DB SHA-256 prefix. Rewrite the ledger to avoid a bare 7+ hex token in canonical prose, or move the checksum into a governed report that the watcher does not treat as a commit reference. |
| Retired transcript scripts | 5 | `:305` | `historical_note` | The ledger already says these were retired and archived by Engine PR #206. Replace concrete backticked paths with prose such as "four retired transcript exporters plus package metadata"; do not recreate or track the removed scripts. |
| `config/identity_bypass.json` | 1 | `:400` | `correct_reference` | The live bypass file is intentionally absent. The tracked current evidence is `config/identity_bypass.json.quarantined` plus `tests/audit/test_ws1_2_identity_bypass_auth.py`. Update the ledger reference to the quarantined sentinel or rewrite the missing live file as non-path prose. |
| Voice state prototype path | 1 | `:493` | `correct_reference` | The file moved to `Docs/Design/uiUx/prototypes/luna_voice_state_surface.html` and is tracked. Update the ledger path. |
| Governed Aug 15 reports | 2 | `:21` | `track_evidence` | `Docs/Reports/REPORT_Luna_Project_Audit_2026-08-15.md` and `Docs/Reports/PROJECT_ORGANIZATION_REPORT_2026-08-15.md` exist, have report content, and are not ignored. Track them in Engine if they are intended canonical evidence. |
| Declutter closeout and pickup | 2 | `:306` | `track_evidence` | `Docs/Reports/REPORT_Session_Closeout_Declutter_2026-08-14.md` and `Docs/Reports/SessionPickups/SESSION_PICKUP_2026-08-14_Declutter_CHold_Landed.md` exist with report frontmatter and are not ignored. Track them in Engine or rewrite the ledger if they were intended as local-only notes. |
| Probe and smoke artifacts | 12 | `:123`, `:150`, `:164`, `:177`, `:189`, `:192`, `:205`, `:209`, `:220`, `:224`, `:230`, `:356` | `historical_note` | These files exist under `scripts/probes/data/` and are ignored by `.gitignore:20:data/`. Preserve run IDs, PIDs, scores, and behavioral conclusions in ledger prose; do not track ignored probe dumps by default. |
| Studio diagnostic dist | 1 | `:303` | `historical_note` | `frontend/lunar-studio/diagnostic/dist` exists but is ignored by `.gitignore:7:dist/`. Keep the source/mount decision, but stop citing the generated dist directory as live canonical evidence. |
| Frontend dist | 1 | `:481` | `historical_note` | `frontend/dist` exists but is ignored by `.gitignore:7:dist/`. Keep the "rebuild after merge" warning as prose, not a backticked generated path target. |

## Proposed Engine Edit Order

1. Track the four governed report/pickup documents if Engine owner agrees they are canonical evidence.
2. Correct the two live moved/current references:
   - `Docs/VoiceSystem/luna_voice_state_surface.html` to `Docs/Design/uiUx/prototypes/luna_voice_state_surface.html`.
   - `config/identity_bypass.json` to the quarantined sentinel or non-path historical prose.
3. Rewrite generated/ignored local artifact references:
   - probe data under `scripts/probes/data/`;
   - `frontend/lunar-studio/diagnostic/dist`;
   - `frontend/dist`.
4. Rewrite retired transcript script paths as a historical archive note tied to Engine PR #206.
5. Rewrite the live DB SHA-256 note so the watcher no longer treats it as a missing Git commit.

## Expected Watcher Impact

If the Engine-owned cleanup follows the table:

- `missing_commit` should drop from `1` to `0`.
- `missing_file` should drop from `7` to `0`.
- Engine `untracked_file` warnings should drop from `18` to only any evidence the Engine owner chooses to leave as canonical path references.
- Open-Looney warnings should remain limited to the two cross-boundary review candidates until those are separately reviewed or suppressed by peer-ledger mention.

## Guardrails

- Do not mutate Engine without explicit approval.
- Do not track credentials, populated bypass files, live DBs, WAL/SHM files, logs, or generated `dist/` outputs.
- Do not recreate retired transcript exporters.
- Do not weaken the watcher by downgrading canonical-ledger findings solely to reduce counts.
