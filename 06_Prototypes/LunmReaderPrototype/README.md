# LUNM Inspector Prototype

Read-only Tauri 2 + React inspector for `.lun` runtime matrix files
(`application_id = 0x4C554E4D`, `LUNM`).

The canonical project document is [`SPEC.md`](./SPEC.md).

**Status:** v0.1.0 MVP (2026-07-27). Opens LUNM files read-only, validates the
family/version boundary, reports SPEC-008/SPEC-011 health, and exposes the eight
format-invariant tables through inspection tabs.

**Dev:**

```bash
npm install
npm run tauri dev
```

**Tests:**

```bash
cd src-tauri && cargo test
npm run build
```

**App bundle:**

```bash
npm run tauri build
```

The default bundle target is the macOS `.app`. DMG packaging is intentionally
left as an explicit follow-up for the MVP.

The app can open matrices through the native file picker, by dropping a `.lun`
file on the window, or by pasting an absolute path into the header.

The Conversations tab shows computed `actual_turns` from `conversation_turns`
instead of trusting the stale `sessions.turns_count` column. Click a session row
to read the stored turns in chronological order.

The MVP has no mutation commands, no migration/repair flow, and no live Engine
attachment. It is an inspector, not an editor.
