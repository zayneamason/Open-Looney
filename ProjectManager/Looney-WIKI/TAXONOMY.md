---
doc_type: reference
status: active
created: 2026-07-24
updated: 2026-07-24
tags:
  - wiki
  - taxonomy
  - classification
---

# Taxonomy

Every classification vocabulary already live in this repo, stated once. This
document does not invent new categories — it names what the repo already
does, so the names stop being tribal knowledge.

## Doc kind

The kind of thing a wiki-governed document is:

| Kind | Meaning | Example |
|---|---|---|
| `format-spec` | Canonical `.lun` file format, one per major version | `03_Format_Spec/LUN-FORMAT_v0.3.md` |
| `spec` | A numbered proposal/decision under the spec lifecycle below | `01_Specs/implemented/SPEC-009_lunm-schema-ownership.md` |
| `audit` | Findings from a real cartridge or matrix, not a proposal | `04_Audits/AUDIT_2026-07-24_lunm-table-manifest.toml` |
| `breakdown` | Authored, hand-written explanation of a subsystem | `sections/lunm-runtime-matrix.md` |
| `reference` | Definitions or vocabulary, not a decision | this file, `GLOSSARY.md` |
| `control-plane` | Wiki governance itself — policy, changelog, tracker | `WIKI_VERSIONING.md` |
| `nav` | Orientation / index | `WIKI_HOME.md`, `00_README/README.md` |

## Spec lifecycle

Defined in [`00_README/README.md`](../../00_README/README.md) §Spec
lifecycle, cited here rather than restated:

```
draft → active → accepted → implemented
              ↘
                rejected
```

- **draft** — rough thinking; lives in journal or conversation, not a file
  under `01_Specs/`.
- **active** — formal spec written, under discussion, in `01_Specs/active/`.
- **accepted** — agreed to implement, in `01_Specs/accepted/`.
- **implemented** — shipped in the Luna codebase, in `01_Specs/implemented/`,
  with a reference to the implementing commit or PR.
- **rejected** — considered and declined, in `01_Specs/rejected/`, with
  reasoning recorded at the top of the file.

`wiki_check.py` check 1 enforces that a spec's header `**Status:**` word
matches the lifecycle folder it sits in; check 2 enforces that
`00_README/README.md`'s prose claims about a spec's state agree with the same
folder.

## Family (`.lun` file family)

From [SPEC-006](../../01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md):

| Family | `application_id` | Carries |
|---|---|---|
| Cartridge (`LUNC`) | `0x4C554E43` | Portable knowledge: doc trees, extractions, claim anchors, embeddings, FTS5 index |
| Runtime matrix (`LUNM`) | `0x4C554E4D` | Luna's live substrate: memory nodes, graph edges, conversation turns, Nexus pointer-graph |

## LUNM table classification

From [SPEC-009](../../01_Specs/implemented/SPEC-009_lunm-schema-ownership.md)
§4.3 — every table in a LUNM file carries exactly one of these four:

| Classification | Meaning | Example |
|---|---|---|
| `format-invariant` | A LUNM file is not a LUNM file without it. Exactly the eight tables named in [SPEC-008](../../01_Specs/implemented/SPEC-008_lunm-family-foundation.md) §4.3, and only those, until that spec is amended. | `memory_nodes`, `nexus_registry`, `profile_config` |
| `engine-extension` | Present in every normal install; carries no LUNM format guarantee. A reader must not assume it. | `threads`, `entities`, `quests` |
| `conditional` | Present only when its owning subsystem is loaded. Absence is not a defect. | the `ih_*` family, gated on the Intergalactic Hub preload path |
| `vestigial` | Declared but unused, scheduled for removal. Absence in a future version is not a breaking change. | `consciousness_snapshots` — zero readers or writers repo-wide |

`format-invariant` is the only classification SPEC-008 has already fixed;
promoting a table into it is a SPEC-008 amendment and a `user_version`
question, not a bookkeeping decision.
