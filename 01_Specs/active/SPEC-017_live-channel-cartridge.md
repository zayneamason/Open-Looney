# SPEC-017: Live channel cartridge (LUNL)

**Status:** active
**Severity:** medium
**Author:** Ahab
**Created:** 2026-09-24
**Last updated:** 2026-09-24
**Affects format version:** new family `LUNL` (`application_id` `0x4C554E4C`). Does not change LUNC v0.3 or LUNM.

---

## Problem statement

Discord and WhatsApp conversations are a record Luna must be able to search, and they are not a sealed knowledge cartridge. A v0.3 LUNC file is built once, finalized (`journal_mode=DELETE`, `VACUUM`), and opened `immutable=1`. A chat log grows all month. Putting that log in a LUNC file makes Nexus freeze the first commit.

## Observed evidence

- `CartridgeBuilder.build` ends in `finalize_for_shipping` (`src/luna/cartridge/builder.py`).
- `discover_cartridge` probes with `mode=ro&immutable=1` (`src/luna/substrate/aibrarian_engine.py`).
- `CartridgeDropboxHandler` ignores later writes (`src/luna/substrate/forge_watcher.py`).
- A live Discord DM is already stored, turn by turn, in `external_surface_turns` inside the runtime matrix. That table is the conversation Luna replies from. It is not a portable file the Hub can open on its own.

## Root cause analysis

LUNC and a chat log have opposite lifetimes. The family tag `application_id` exists so a reader can refuse the wrong kind of `.lun` before it trusts the schema. A growing channel file needs its own tag.

## Proposed solution

A **live channel cartridge** is one SQLite file per platform per calendar month:

`discord-YYYY-MM.lun`

It lives in `data/user/channel_live/`, outside `cartridges_dir()`, while the month is open. `PRAGMA journal_mode=WAL`. `PRAGMA user_version=1`. `PRAGMA application_id=0x4C554E4C` (`LUNL`).

The gateway is the only writer. The engine and the Hub open it read-only.

### Schema changes

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('thread', 'channel_day', 'dm_day')),
    platform TEXT NOT NULL,
    account TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, platform, account, conversation_id, thread_id, day)
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    platform TEXT NOT NULL,
    account TEXT NOT NULL,
    platform_message_id TEXT NOT NULL,
    sender_platform_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    body TEXT NOT NULL,
    lane TEXT NOT NULL CHECK (lane IN ('talk', 'listen', 'automation')),
    sent_at TEXT,
    UNIQUE (platform, account, platform_message_id)
);
```

`meta.format` is `lunl-channel-0.1`.

### Document cuts

All three exist. They apply in different places:

- `thread_id` set → one `thread` document for the life of that thread inside the month file. `day` is empty.
- group message, no thread → `channel_day` for that channel and UTC date.
- DM → `dm_day` for that DM channel and UTC date.

Empty string, not NULL, for the unused key, so the unique constraint holds.

### Behavioral changes

- A repeated `(platform, account, platform_message_id)` is stored once.
- `lane=talk` is a DM Luna answered. `lane=listen` is a recorded server message she did not answer. `lane=automation` is reserved.
- When the calendar month changes, the writer opens `discord-YYYY-MM.lun` for the new month. The previous file stays in `channel_live/` until a reader accepts `LUNL`. It is not moved into the Nexus dropbox by this spec.
- Hub keyword search reads `messages.body` from every `discord-*.lun` whose `application_id` is `LUNL` and returns those rows as Discord message events. It does not query `ih_events` for this record.

### Migration path

New family. No LUNC or LUNM migration. Old readers that require `LUNC` refuse the file at `application_id`.

## Validation rules

On open, a reader checks:

1. SQLite header.
2. `PRAGMA application_id = 0x4C554E4C`.
3. `PRAGMA user_version = 1`.
4. `messages` and `documents` exist.

A failed check skips that file. It does not fail the Hub search.

## Governance implications

N/A for the LUNC annotation ledger. This file is not sealed, so it has no hash-chained ledger. Promotion of a message into `memory_matrix.lun` is a separate governed write and does not happen because a row exists here.

## Alternatives considered

Storing the log as LUNC v0.3 and appending under WAL was rejected. The dropbox probe opens `immutable=1` and would freeze or reject the file.

Keeping the record only in `external_surface_turns` was rejected for the Hub. That table is the live reply memory. The cartridge is the portable month file.

## Open questions

1. When a `LUNL` reader exists, the closed month is sealed (`journal_mode=DELETE`, `VACUUM`) and moved into `cartridges_dir()`. Until then it stays in `channel_live/`.
2. Keyword search is `LIKE` over `body`. An FTS5 index is a later addition, not required to open the file.

## Dependencies

None. Does not modify SPEC-001 through SPEC-016.

## Implementation notes

- Writer: Luna Engine `channel_gateway/cartridge.py`
- Hub reader: Luna Engine `intergalactic_hub/query/channel_cartridge.py`
- Engine commit: `1ebec2e6` (`feat: record Discord in a live LUNL cartridge`)
