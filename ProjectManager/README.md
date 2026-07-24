---
doc_type: runbook
status: active
created: 2026-07-20
updated: 2026-07-24
tags:
  - projectmanager
  - ledger
  - protocol
---

# ProjectManager

Repo-local project-management home for `.lun` development. Holds the durable task
ledger that Codex and Claude should treat as the source of truth for format,
reader, audit, and project-organization follow-up.

## Canonical ledger

Declared in root `project_organization.json` as:

`canonical_ledger = "ProjectManager/TODO_LUN_Development_2026-07-20.md"`

There should be exactly one active `TODO_*.md` ledger in this folder. That path is
the machine-readable `canonical_ledger` value.

`project_organization.json` is now at version 2 and carries more than the ledger
pointer: a `wiki` block declaring governed scope, exclusions, the control-plane
paths, and the lifecycle vocabulary. It is the authoritative source for what the
verifier governs — `WIKI_VERSIONING.md` points at it rather than restating it.

## Wiki control plane

- `WIKI_VERSIONING.md` — versioning policy, current version, promotion criteria
- `WIKI_CHANGELOG.md` — what changed each pass, and why the bump was that size
- `WIKI_PASS_TRACKER.md` — pass execution table and gates

### Closing a pass

Run the verifier and read its output before marking any pass `done`:

```bash
python3 scripts/wiki_check.py          # exits 0 under policy "warning-only"
python3 scripts/wiki_check.py --strict # exits 1 on any gating finding
```

It reports spec-status/folder disagreement, README lifecycle claims that
contradict the tree, broken relative links, version skew across the three
control-plane files, incomplete changelog entries, malformed pass rows, and
governed docs changed since the last bump — including uncommitted ones, which is
the state a pass is actually closed in. A pass gate reads `pass` only when the
run is clean at the closing commit.

## Add a task

1. Put it under the most specific existing `##` section.
2. If none fits, add a new `##` section titled with a plain noun phrase.
3. Use `- [ ]` checkboxes for actionable work. Use prose notes only for context
   that stops a fact from being rediscovered later.
4. For destructive work or live database writes, put **approval required** in the
   task text.
5. Bump the ledger `updated:` date whenever task content changes.

Chat is not durable state. Any `.lun` development item raised in chat should land
on this ledger, not stay only in the transcript.

## Check off / retire a task

- Mark done with `- [x]`. Do not delete completed items immediately; keep them
  until an explicit ledger-pruning pass so history survives.
- Blocked or deferred items stay in place with a short `blocked:` / `deferred:`
  note explaining why.

## Related surfaces

- `01_Specs/` holds lifecycle specs.
- `03_Format_Spec/` holds canonical versioned format specs.
- `04_Audits/` holds cartridge audit findings.
- `06_Prototypes/ReaderPrototype/` holds the independent reader prototype.
- `08_Journal/` holds dated development notes and current open queues.
- `00_README/README.md` is the wiki nav hub; its lifecycle claims are checked
  against the tree.
- `scripts/wiki_check.py` is the drift verifier.
