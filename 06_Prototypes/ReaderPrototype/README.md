# `.lun` Reader (incubation)

Tauri 2 + React 19 + Rust desktop reader for `.lun` v0.3 cartridges. The
canonical document for this project is [`SPEC.md`](./SPEC.md) — current
version, contract, acceptance criteria, factoring notes.

**Status:** v0.3.4 (2026-07-26). Incubates here in `06_Prototypes/ReaderPrototype/`
until Luna's Tauri shell exists; will be absorbed as a tab / feature module
within Luna's main surface (a "Nexus"). Code is factored to migrate cleanly:
Rust modules are library-shaped (`rlib + staticlib + cdylib`); React
components are portable; `App.tsx`'s mode toggle and `lib.rs`'s command
registration are the only throwaway glue.

**Dev:**

```bash
npm install
npm run tauri dev    # opens the desktop window
```

**Tests:**

```bash
cd src-tauri && cargo test
npm run build                 # tsc + vite build (no JS tests yet)
```

See [`SPEC.md`](./SPEC.md) § Repo character note for the long-term
lifecycle rationale.
