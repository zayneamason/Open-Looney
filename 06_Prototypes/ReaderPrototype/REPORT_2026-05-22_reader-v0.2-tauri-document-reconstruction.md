# REPORT: `.lun` Reader Prototype v0.2.0

**Date:** 2026-05-22  
**Scope:** Tauri reader prototype development, document reconstruction, launch readiness, and version update  
**App:** `06_Prototypes/ReaderPrototype`  
**Reader app version:** `0.2.0`  
**Target cartridge format:** `.lun` cartridge v0.2 (`application_id = 0x4C554E43`, `PRAGMA user_version = 2`)  
**Reference cartridge:** `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun`

---

## Executive Summary

The `.lun` reader prototype is now a runnable Tauri desktop application and has
been advanced from a tree/inspection tool into a structural document reader.

The key change is the new `Document` view. It reads the complete `doc_nodes`
table, rebuilds the parent-child hierarchy from `parent_id`, sorts siblings by
`position`, and renders the cartridge back into readable document order. This
does not attempt pixel-perfect PDF reproduction. It is semantic reconstruction:
sections, page boundaries, paragraphs, blockquotes, lists, figures, and tables
are rendered from the node tree stored in the cartridge.

This matters because the first user-facing complaint was correct at the product
level: the reader was not presenting the cartridge as a document. The cartridge
format already contained enough hierarchy for reconstruction, but the v0.1
reader exposed it mainly as a left-rail tree plus selected-node inspector.

Reader app version was bumped to `0.2.0`. The `.lun` file-format version was not
changed. The format remains v0.2.

---

## System Meaning

### What changed in the system

- The reader is now a real Tauri desktop app, not only a browser-hosted
  prototype.
- The app can be launched with `npm run tauri -- dev`.
- Tauri uses Vite at `http://localhost:1420/` during development and runs the
  Rust binary from `src-tauri/target`.
- The reader remains independent from Luna runtime code. It reads cartridges
  directly through SQLite via Rust `rusqlite`.
- The reader now has a full-document surface that validates the portability
  thesis from the user side, not only from CLI SQL inspection.

### What did not change

- The `.lun` cartridge format did not change.
- `PRAGMA user_version = 2` remains the required cartridge format version.
- `meta.format_version = '0.2'` remains the human-readable format marker.
- No builder/parser behavior was changed in this slice.
- No annotations, writes, ledger events, semantic search, or multi-cartridge
  library behavior was added.

### Why this matters

The `.lun` format's core claim is that the file itself is portable and useful
without Luna-specific runtime code. Before this work, the prototype could prove
that claim technically: it could open the SQLite file, inspect nodes,
extractions, provenance, and FTS5 search. After this work, it can also prove a
more important product claim: a user can open a cartridge and read the source
structure as a document.

This moves the reader from "debugging cockpit" toward "actual cartridge
reader."

---

## Original Finding

The initial assessment was:

- `doc_nodes` stores a hierarchy.
- `DocTree` displays that hierarchy lazily.
- `NodeView` only displays one selected node at a time.
- Many container nodes have `content = NULL`.
- Paragraph text is often stored in sentence children rather than directly on
  the paragraph row.
- The reader therefore felt sparse even when the cartridge contained useful
  structure.

Deeper inspection found one more issue: the reader's Rust and TypeScript
`NodeType` models only allowed:

```text
document
section
paragraph
sentence
```

But the builder can emit richer Markdown/PDF nodes:

```text
list
list_item
figure
table
row
cell
```

That made the reader less robust than the builder. The fix had to address both
the rendering gap and the type-contract gap.

---

## Development Work Completed

### 1. Full document reconstruction

Added:

- `src/components/DocumentView.tsx`
- new Tauri command `list_all_nodes`
- frontend API wrapper `api.listAllNodes`

Behavior:

1. Load every row from `doc_nodes`.
2. Group rows by `parent_id`.
3. Sort each sibling group by `position`, with `id` as a stable tie-breaker.
4. Render recursively.
5. Reconstruct paragraph text from child sentence nodes when the paragraph row
   has no direct content.

Supported render shapes:

- `document`: transparent root container
- `section`: heading or page divider
- `paragraph`: paragraph or blockquote
- `sentence`: fallback paragraph
- `list`: unordered list
- `list_item`: list item
- `figure`: image/caption when source metadata exists
- `table`: table wrapper
- `row`: table row
- `cell`: table cell

The `Document` tab is now the default view.

### 2. Reader node-type contract widened

Updated Rust and TypeScript node type definitions to match the current builder
surface:

```text
document
section
paragraph
sentence
list
list_item
figure
table
row
cell
```

Files:

- `src-tauri/src/types.rs`
- `src/types.ts`

This prevents the reader from failing or misrepresenting cartridges that contain
lists, tables, figures, or cells.

### 3. Shared node display helpers

Added:

- `src/nodeDisplay.ts`

Centralized:

- node type labels
- node type colors
- metadata helpers
- page badge handling
- preview text fallback logic
- leaf-node detection

This keeps `DocTree` and `DocumentView` aligned on labels and fallback behavior.

### 4. Tree labels improved

`DocTree` now uses shared preview helpers. A section with `meta_json.page_num`
can display as `Page N`; titled sections prefer real content or `meta_json`
title before falling back to generic `Section N`.

This directly addresses the earlier "Section 12" style problem where the tree
felt mechanically correct but semantically thin.

### 5. Tauri app verified

The app was launched with:

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/Research/Code for .lun Development/06_Prototypes/ReaderPrototype"
npm run tauri -- dev
```

Observed runtime:

- Vite ready at `http://localhost:1420/`
- Cargo compiled the Rust app
- Tauri binary ran as `target/debug/lun-reader`

There is a Node version warning:

```text
Node.js 20.17.0
Vite requires Node.js 20.19+ or 22.12+
```

This did not block compilation or launch. It is a development-toolchain warning,
not a cartridge/runtime issue. Packaged Tauri apps do not require Node at
runtime.

---

## Version Decision

### Updated

Reader app version:

```text
0.1.0 -> 0.2.0
```

Files updated:

- `package.json`
- `package-lock.json`
- `src-tauri/Cargo.toml`
- `src-tauri/Cargo.lock`
- `src-tauri/tauri.conf.json`

### Why this deserved a version bump

This is not just a bug fix. It changes the user-facing product capability:

- v0.1.0: open cartridge, inspect tree/search/extractions/provenance.
- v0.2.0: open cartridge and render it as a reconstructed document.

That is a minor app-version bump.

### Format version unchanged

The `.lun` format remains v0.2. No schema migration was introduced.

Do not confuse:

- **Reader app version:** `0.2.0`
- **Cartridge format version:** `0.2`
- **SQLite user_version:** `2`

---

## Files Changed

### Reader runtime

- `src/components/DocumentView.tsx`
  - new full-document renderer
  - groups all nodes by parent
  - renders document structure recursively

- `src/nodeDisplay.ts`
  - new shared node-label and preview helper module

- `src/components/DocTree.tsx`
  - now uses shared preview helpers
  - supports all builder node types

- `src/App.tsx`
  - adds `DocumentView`

- `src/components/TabBar.tsx`
  - adds `Document` tab

- `src/store.ts`
  - adds `document` view state
  - defaults to `document`

- `src/api.ts`
  - adds `listAllNodes`

- `src/types.ts`
  - widens `NodeType`

### Tauri backend

- `src-tauri/src/types.rs`
  - widens `NodeType`

- `src-tauri/src/queries.rs`
  - adds `list_all_nodes`
  - adds tests for full-tree loading and rich node types

- `src-tauri/src/lib.rs`
  - exposes `list_all_nodes` as a Tauri command

### Version and docs

- `package.json`
- `package-lock.json`
- `src-tauri/Cargo.toml`
- `src-tauri/Cargo.lock`
- `src-tauri/tauri.conf.json`
- `SPEC.md`
- this report

---

## Verification

### Rust tests

Command:

```bash
cd "06_Prototypes/ReaderPrototype/src-tauri"
cargo test
```

Result:

```text
21 passed
0 failed
```

Coverage added/confirmed:

- cartridge open validation
- bad-file rejection paths
- LUNM family rejection
- v0.1 rejection with migrate hint
- metadata reading
- node listing
- full-tree listing
- rich Markdown node types
- parent-chain behavior
- extraction counts
- claim source provenance
- FTS5 search
- safe HTML escaping for search snippets

### Frontend build

Command:

```bash
cd "06_Prototypes/ReaderPrototype"
npm run build
```

Result:

```text
tsc passed
vite build completed
```

Warning:

```text
Node.js 20.17.0 is below Vite's preferred 20.19+ or 22.12+
```

This should be cleaned up, but it did not block build.

### Tauri launch

Command:

```bash
npm run tauri -- dev
```

Result:

```text
Vite ready at http://localhost:1420/
Cargo compiled
target/debug/lun-reader running
```

---

## Known Limits

### Structural, not visual, reconstruction

The new `Document` tab reconstructs semantic document order. It does not
preserve original PDF pagination, font metrics, margins, headers, footers,
sidebars, or exact line breaks.

This is acceptable for v0.2.0 because the cartridge schema stores a semantic
tree, not full layout instructions.

### Paragraph text is reconstructed from sentences

Current builder behavior often stores paragraph text in sentence children rather
than on the paragraph container. The reader handles that by gathering child
text. This is a practical reader fix, not a format change.

### PDF section quality depends on parser quality

For PDF sources, page sections may have `content = NULL` and `meta_json.page_num`
instead of true headings. The reader displays page dividers in that case.

Actual high-fidelity chapter/heading reconstruction still depends on better PDF
parsing upstream.

### Node warning should be resolved

Recommended local toolchain cleanup:

```bash
node --version
```

Upgrade to Node `20.19+` or `22.12+` to remove the Vite warning.

---

## Recommended Next Work

1. Smoke-test the app manually against `Marcus-Aurelius-Meditations.lun`.
   Confirm the `Document` tab feels readable, not just technically correct.

2. Add a small sample Markdown source that includes headings, lists, a table,
   an image, and a blockquote. Build it into a `.lun` and verify every rich node
   type renders in the Tauri app.

3. Add a reader-side empty/error state for cartridges with no `doc_nodes` rows
   or malformed trees.

4. Decide whether `DocumentView` should support a compact "page outline" rail
   for PDF cartridges where page sections are the main structural anchors.

5. Add a packaging pass:

```bash
npm run tauri -- build
```

Then smoke-test the bundled `.app`.

6. Update `03_Format_Spec/LUN-FORMAT_v0.2.md` with the practical reader findings:

- `doc_nodes.content` is nullable.
- `doc_nodes.meta_json` has source-specific shapes.
- builder-emitted node vocabulary includes list/table/figure nodes.
- paragraph text may be represented by sentence children.

---

## Bottom Line

The reader is now good enough to validate the product-facing question:

> Can a user open a `.lun` cartridge as a standalone object and read it back as
> a document?

For semantic structure, yes. The remaining work is fidelity and polish, not
basic viability.

