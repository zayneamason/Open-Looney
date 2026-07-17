# SPEC-006: application_id contract and v0.2 hygiene bundle

**Status:** implemented
**Severity:** high
**Author:** Ahab
**Created:** 2026-05-10
**Last updated:** 2026-05-21 (Phase 5 shipped; moved to implemented/)
**Affects format version:** v0.2

---

## Problem statement

`.lun` is a file-format family with at least two distinct members in active use: the portable knowledge cartridge (`cartridge.lun`) and the runtime memory substrate (`memory_matrix.lun`). The v0.1 builder ships files without `PRAGMA application_id`, so no external tool can distinguish family without opening the schema. This is the load-bearing problem — every other hygiene issue is secondary to it.

The same v0.1 builder also ships without `PRAGMA user_version` (no SQLite-native version pragma), with parser-artifact text leaking into `meta.title`, and with absolute builder paths in `meta.source_path`. These are real but lesser problems that fall out naturally once the contract for file identity is established.

This spec establishes `application_id` as a **required contract** for every `.lun` file, and bundles the v0.1 hygiene findings (M-01, M-02, S-03) into the same v0.2 milestone so the format gets a coherent baseline before downstream specs (SPEC-001, SPEC-002, SPEC-003) layer on top.

## Observed evidence

From `04_Audits/AUDIT_2026-04-21_priests-and-programmers.md`:

- **S-02 (load-bearing):** `PRAGMA application_id = 0` (unset). A file renamed `.sqlite → .lun` is indistinguishable from a real cartridge without opening `meta`. With the codex 2026-05-10 deep dive confirming `.lun` is now a family, no external tool can identify family membership from the magic bytes.
- **S-03 (low):** `PRAGMA user_version = 0` (unset). Format version tracked only in `meta.schema_version` (cartridges) or nowhere (matrix).
- **M-01 (medium):** `meta.title = "/. Stephen Lansing"`. Real title is *Priests and Programmers: Technologies of Power in the Engineered Landscape of Bali*. PDF parser concatenated a leading glyph with the author name.
- **M-02 (low):** `meta.source_path = "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/Docs/PRIESTS AND PROGRAMMERS_Lansing.pdf"`. Absolute developer-machine path shipped in the artifact.

Cross-reference: `08_Journal/2026-05-10.md` records the `application_id` decision (two values, one per family) and the reasoning chain (Fossil precedent, audit-principle alignment, file-is-source-of-truth principle).

## Root cause analysis

- **S-02, S-03:** Builder never sets these pragmas. SQLite defaults both to 0. The format has no contract requiring them.
- **M-01:** PDF parser concatenates leading glyphs with content. No title validation in the builder between parser output and `meta` insert.
- **M-02:** Builder writes the absolute file path to `meta.source_path` because `Path(arg).resolve()` returns absolute. Convenient during development; leaks builder environment in shipped artifacts.

The common cause across all four: the builder treats meta and pragmas as soft suggestions. There is no enforced contract about what *must* be present, what *must not* be present, or what the reader is allowed to assume. This spec establishes that contract.

## Proposed solution

### The contract (mandatory for every `.lun` file from v0.2 forward)

Every `.lun` file MUST have:

1. **`PRAGMA application_id`** set to its family's value, before any other write:
   - Cartridge family: `0x4C554E43` (`'LUNC'`)
   - Runtime matrix family: `0x4C554E4D` (`'LUNM'`)
2. **`PRAGMA user_version`** set to its format-version integer (v0.2 → `2`).
3. **Family-specific meta contents** (cartridge family only; matrix family has no `meta` table at present — see migration notes below).

Readers MUST refuse to proceed if `application_id` does not match the expected family value. This is the primary family discriminator and is checked before anything else.

### Schema changes

```sql
-- Cartridge family (cartridge.lun) — set at builder creation:
PRAGMA application_id = 0x4C554E43;  -- 'LUNC' = Luna Cartridge
PRAGMA user_version = 2;              -- v0.2 baseline

-- Cartridge meta additions / revisions:
INSERT INTO meta (key, value) VALUES
  ('format_version', '0.2'),                    -- human-readable mirror of user_version
  ('cartridge_kind', 'knowledge'),              -- subtype within cartridge family
  ('source_filename', <basename(source_path)>); -- basename only
-- source_canonical_path only inserted when --preserve-paths flag is set (see below)

-- Drop the v0.1 source_path column from meta (it shipped the absolute path).
DELETE FROM meta WHERE key = 'source_path';

-- Runtime matrix family (memory_matrix.lun) — pragmas only:
PRAGMA application_id = 0x4C554E4D;  -- 'LUNM' = Luna Matrix
PRAGMA user_version = 2;
-- NO meta inserts. The live memory_matrix.lun has no meta table.
-- Human-readable matrix marker deferred to a future spec (either via
-- profile_config or a dedicated matrix header table).
```

Cartridge `meta` is a key/value table, so all of this is INSERT/UPDATE/DELETE on existing schema — no DDL needed.

### Behavioral changes

**Builder (`src/luna/cartridge/builder.py`):**

1. Set `application_id` and `user_version` pragmas as the first DDL after connection open, before any data inserts.
2. Validate parsed title (see Validation rules below). On reject: fall back to filename stem + emit a `BuilderWarning` event. The underlying PDF parser bug remains a separate issue; this spec just ensures bad titles can't ship silently.
3. Write only `basename(source_path)` to `meta.source_filename`. Full path goes to `meta.source_canonical_path` **only when** a `--preserve-paths` CLI flag is set. Default behavior writes no path information beyond the basename.
4. Always set `meta.cartridge_kind = 'knowledge'`.
5. Always set `meta.format_version = '0.2'`.

**Builder finalization (new step, runs after all data inserts and triggers fire):**

```sql
PRAGMA optimize;                       -- ANALYZE-equivalent; populates planner statistics
PRAGMA wal_checkpoint(TRUNCATE);       -- fold any WAL frames into main file
PRAGMA journal_mode = DELETE;          -- shipping mode: no -wal/-shm sidecar files
VACUUM;                                -- reclaim space, defragment
```

Source: `05_Reference/SQLite_Research.md`, Topic 8. DELETE journal mode is critical for portability — WAL mode requires writable directory access at the recipient, which breaks the "drop a `.lun` anywhere and read it" property.

**Reader (`src/luna/cartridge/reader.py` and equivalent for matrix):**

1. On open: read `PRAGMA application_id`. Refuse to proceed if it doesn't match the expected family value (`WrongFamilyError`).
2. Read `PRAGMA user_version`. If outside supported range: `UnsupportedVersionError`.
3. **Cartridge only:** validate `meta.format_version` parses to an integer matching `user_version`. On mismatch: log warning and trust `user_version`.
4. **Cartridge only:** validate `meta.cartridge_kind` ∈ `SUPPORTED_CARTRIDGE_KINDS` (currently `{'knowledge'}`). Unknown kind: `UnsupportedCartridgeKindError`.

**Runtime read-only open pattern (for cartridges being queried by the Luna engine):**

```python
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
conn.execute("PRAGMA query_only = 1")
conn.execute("PRAGMA mmap_size = 268435456")  # 256MB
conn.execute("PRAGMA cache_size = -8000")     # 8MB
```

### Migration path

Three populations of files exist; each gets a different treatment.

**1. New cartridges (built post-v0.2):** Builder sets everything at creation. No migration logic needed.

**2. Existing cartridges** (currently just `PRIESTS_AND_PROGRAMMERS_Lansing.lun`): Rebuild from source PDF using the v0.2 builder. Cheap (cartridges are deterministic from source), and the rebuild incidentally remediates M-01 (title) and M-02 (source_path) for that file.

**3. Existing runtime matrix** (`data/user/memory_matrix.lun`): Cannot be rebuilt. Pragma patch runs in the **database open path** — specifically in `MemoryDatabase.connect()` — not at engine startup. This guarantees the contract is enforced for the API, CLI, tests, scripts, and any tooling that opens the DB directly:

```sql
-- Idempotent patch in MemoryDatabase.connect(). Runs only when application_id is unset.
PRAGMA application_id = 0x4C554E4D;
PRAGMA user_version = 2;
-- No meta operations. The live memory_matrix.lun has no meta table.
-- Human-readable substrate marker is deferred to a separate spec.
```

The migration is forward-compatible: any v0.2 reader sees the patched file correctly. Older readers (none in production) would have been opening the matrix with `application_id = 0` anyway, so the change is invisible to them.

**Compatibility classification:** read-compatible for cartridges (v0.2 reader handles both v0.1 and v0.2; v0.1 cartridges get rebuilt). Forward-compatible for matrix (old readers ignore the new pragmas).

## Validation rules

**At build time (cartridge):**

Title checks (in order; first failure triggers fallback to filename stem + warning):
- `len(title.strip()) < 3` → reject
- `re.match(r"^[/.\\\-_\s]{1,3}\s", title)` → reject (parser artifact prefix)
- `re.search(r"[A-Za-z0-9]", title) is None` → reject (no alphanumeric content)
- `title.strip().casefold() in {"untitled", "document", "document1"}` → reject (placeholder set)

The placeholder set is intentionally small. Author-name detection is **not** included — too many false positives, and author-as-title is sometimes correct for academic preprints, monographs without distinct titles, etc.

Meta keys (cartridge family):
- Required: `format_version`, `source_filename`, `source_hash`, `created_at`, `embedding_model`, `embedding_dim`, `cartridge_kind`, `word_count`, `node_count`
- Optional (only when `--preserve-paths`): `source_canonical_path`
- Forbidden: `source_path` (the v0.1 key with absolute paths)

Pragma checks (both families):
- `PRAGMA application_id` returns the expected family value
- `PRAGMA user_version` returns the integer parse of the format version

Post-finalize checks (cartridge family, build artifact ready to ship):
- `PRAGMA journal_mode` returns `'delete'`
- No `-wal` or `-shm` sidecar files exist on disk next to the cartridge

**At read time (cartridge):**

```python
SUPPORTED_CARTRIDGE_KINDS = {"knowledge"}
MIN_SUPPORTED_VERSION = 1
MAX_SUPPORTED_VERSION = 2  # bumps with each format version

def validate_cartridge_for_read(conn):
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id != 0x4C554E43:
        raise WrongFamilyError(
            f"Not a Luna cartridge: application_id=0x{app_id:08X}, expected 0x4C554E43"
        )
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if not (MIN_SUPPORTED_VERSION <= user_ver <= MAX_SUPPORTED_VERSION):
        raise UnsupportedVersionError(
            f"Cartridge user_version={user_ver}, supported range "
            f"[{MIN_SUPPORTED_VERSION}, {MAX_SUPPORTED_VERSION}]"
        )
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    try:
        meta_ver = int(meta.get("format_version", "0").split(".")[1])
    except (ValueError, IndexError):
        meta_ver = None
    if meta_ver is not None and meta_ver != user_ver:
        logger.warning(
            f"meta.format_version={meta.get('format_version')!r} disagrees with "
            f"user_version={user_ver}; trusting user_version"
        )
    kind = meta.get("cartridge_kind")
    if kind not in SUPPORTED_CARTRIDGE_KINDS:
        raise UnsupportedCartridgeKindError(
            f"cartridge_kind={kind!r} not in {SUPPORTED_CARTRIDGE_KINDS}"
        )
```

**At read time (runtime matrix):**

```python
def validate_matrix_for_read(conn):
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id != 0x4C554E4D:
        raise WrongFamilyError(
            f"Not a Luna matrix: application_id=0x{app_id:08X}, expected 0x4C554E4D"
        )
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if not (MIN_SUPPORTED_VERSION <= user_ver <= MAX_SUPPORTED_VERSION):
        raise UnsupportedVersionError(...)
    # No meta-based checks for matrix in this spec. A future spec
    # will add a human-readable substrate marker (via profile_config or a
    # dedicated header table) and corresponding validation.
```

## Governance implications

This spec is foundational, not governance. Two indirect effects worth recording:

- **Ledger / annotation events (SPEC-005):** Future ledger entries that reference a `.lun` file can record `(file_application_id, file_user_version, file_hash)` as a provenance triple. Without this spec, two of the three are unset and carry no information.
- **Cross-cartridge traversal:** Future plug-in collection logic (using `ATTACH DATABASE`, per `05_Reference/SQLite_Research.md` Topic 6) validates each attached file's `application_id` matches the cartridge family before joining. Refuses to attach a `memory_matrix.lun` as if it were a knowledge cartridge — fails fast with a clear error instead of producing nonsense joins.

## Alternatives considered

1. **Single `application_id` for the whole `.lun` family.** Rejected. See `08_Journal/2026-05-10.md` for the full reasoning: Fossil precedent (three distinct values for repo/checkout/global), audit-principle alignment, and the file-is-source-of-truth principle.
2. **Track version only in `meta.format_version`, skip `user_version`.** Rejected. `PRAGMA user_version` is the SQLite-native mechanism — one round-trip, four bytes, no schema parsing. Fast machine inspection matters for `file(1)` magic patterns and audit scripts that don't want to open a full schema.
3. **Track version only in `PRAGMA user_version`, skip `meta.format_version`.** Rejected. The `meta` table is human-readable when dumping the schema or browsing in a SQLite GUI. Carrying the version in both places is cheap and serves different audiences. Pragma binds; meta documents.
4. **Drop `source_path` entirely, no replacement.** Rejected. Provenance is useful for debugging and re-ingestion. Solution: drop the absolute path, keep the filename, allow opt-in absolute path under explicit flag.
5. **Use SHA-256 of the source file as `application_id`.** Rejected. `application_id` is 4 bytes; it identifies file format, not file content. Source hash already lives in `meta.source_hash`.
6. **Anonymize `source_canonical_path` with `~`-redaction by default.** Rejected. The `~`-redacted path still leaks directory structure and project naming. Privacy/portability should be default behavior; full path is opt-in only.
7. **Loose `cartridge_kind` validation (accept unknown values for forward compat).** Rejected. Strict validation catches typos and prevents silent semantic drift. Forward compat handled by bumping `SUPPORTED_CARTRIDGE_KINDS` per spec.
8. **Run matrix migration at engine startup only.** Rejected. The DB is opened by API, CLI, tests, scripts, and tooling beyond the engine. Migration runs in `MemoryDatabase.connect()` so it applies uniformly.
9. **Hard-reject author-like strings in title validation.** Rejected. Too many false positives — author-as-title is legitimately correct for some publication types.
10. **Add `meta.substrate_kind = 'runtime_matrix'` to memory_matrix.lun.** Rejected for this spec. The live matrix has no `meta` table; adding one is out of scope. Deferred to a future spec that decides between using existing `profile_config` or introducing a dedicated matrix header table.

## Open questions

None remaining. All four open questions from the 2026-05-10 draft were resolved the same day:

1. Title blocklist scope — regex + length + alphanumeric check + small placeholder set; no author-like rejection.
2. `source_canonical_path` default — opt-in via `--preserve-paths`.
3. Matrix migration trigger location — `MemoryDatabase.connect()` (open path), not engine startup.
4. `cartridge_kind` validation — strict, with `SUPPORTED_CARTRIDGE_KINDS` bumped per spec.

## Dependencies

None upstream. This spec is foundational for v0.2 and unblocks:
- SPEC-001 (orphan claims) — drafts against v0.2 schema
- SPEC-002 (portable IDs) — drafts against v0.2 schema
- SPEC-003 (meaningful confidence) — drafts against v0.2 schema

Downstream deferred (not blockers for this spec):
- A future spec for the runtime matrix human-readable header (whether via `profile_config` or a new header table)
- `magic.txt` upstream registration (courtesy submission to the SQLite project; format works without it)

## Implementation notes

- **Status:** Phase 1 implemented 2026-05-12 against handoff revision 2
- **Commit/PR reference:** (pending — uncommitted at evidence-paste time)
- **Implementer:** CC (Ahab reviewing)
- **Handoff:** `Docs/Handoffs/Nexus/HANDOFF_NEXUS_LUN_V02_PHASE1_HYGIENE.md` rev 2

### Resolved paths

| Var | Value |
|-----|-------|
| `MATRIX_PATH` | `/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun` (legacy single-tenant — bare python falls back here since `_current_profile` contextvar is unset) |
| `LANSING_LUN` | not found in tree — synthetic v0.1 stub used at `/tmp/v01_stub.lun` |
| `ENGINE_LOG` | no file-based logger configured; stderr capture used for smoke 10 |

### Pre-flight grep output

**Grep 1 — `builder.py` finalize path:**
```
84:  conn = sqlite3.connect(str(lun_path))
85:  conn.execute("PRAGMA journal_mode=WAL")
86:  conn.execute("PRAGMA busy_timeout=15000")
87:  conn.execute("PRAGMA foreign_keys=ON")
143: conn.commit()
```
(No `def finalize` — confirmed inline call site is correct.)

**Grep 2 — existing `application_id` / `user_version` writes:** zero hits. Clean slate.

**Grep 3 — cartridge reader open:**
```
45: conn = sqlite3.connect(f"file:{lun_path}?mode=ro", uri=True)
```
(Reader is the module-level `resolve_source_ref()` function at line 26; validator wired immediately after the line-45 connect.)

**Grep 4 — matrix database connect path:**
```
102: async def connect(self) -> None:
126: await self._connection.execute("PRAGMA journal_mode=WAL")
129: await self._connection.execute("PRAGMA busy_timeout=15000")
132: await self._connection.execute("PRAGMA foreign_keys=ON")
135: await self._connection.execute("PRAGMA cache_size=-64000")
```
(Confirms async aiosqlite idiom; pragma patch inserted between line 135 and 138.)

**Grep 5 — SQLite version:** `3.51.0` ✓ (above 3.35.0 threshold)

**Grep 6 — Lansing meta:** Lansing not in tree; synthetic v0.1 stub used instead.

### Smoke test evidence

**Note on expected pragma decimal values in handoff:** the handoff stated cartridge `application_id` = `1280265795` and matrix = `1280265821`. These were typos. Correct values:
- `0x4C554E43` (LUNC) = `1280659011`
- `0x4C554E4D` (LUNM) = `1280659021`

The implementation writes the correct hex pragmas; only the handoff's expected decimals were off.

**Smoke 1 — Pre-flight greps:** see Pre-flight grep output above.

**Smoke 2 — New cartridge pragmas (`/tmp/test_v02.lun`):**
```
PRAGMA application_id: 1280659011  (0x4C554E43 = "LUNC")
PRAGMA user_version:   2
```

**Smoke 3 — `meta` table dump:**
```
cartridge_kind|knowledge
created_at|2026-05-12T17:10:44.718923+00:00
embedding_dim|384
embedding_model|all-MiniLM-L6-v2
format_version|0.2
node_count|12
source_filename|test_v02_source.md
source_format|markdown
source_hash|a4ca54ee9c15adbf116dbe7f25f2d6846e1f4780cb8c7239902facb101e88ea9
title|Phase 1 Smoke Test Document
word_count|50
```

**Smoke 4 — `source_path` / `schema_version` absent:**
```
SELECT count(*) FROM meta WHERE key IN ('source_path', 'schema_version');
→ 0
```

**Smoke 5 — `--preserve-paths` writes `source_canonical_path`:**
```
source_canonical_path|/private/tmp/test_v02_source.md
```

**Smoke 6 — full-pipeline build journal_mode + sidecar absence:**
```
PRAGMA journal_mode → delete
ls /tmp/test_full.lun*  →  /tmp/test_full.lun (no -wal, no -shm)
```

**Smoke 7 — `WrongFamilyError` on non-cartridge file (`application_id=0x12345678`):**
```
WrongFamilyError raised as expected: Not a Luna cartridge: application_id=0x12345678, expected 0x4C554E43
```

**Smoke 8 — v0.1 legacy fallback against synthetic stub:**
```
WARNING luna.cartridge: Opening v0.1 cartridge without application_id set (legacy)
v0.1 fallback OK: no exception raised, warning logged above
```

**Smoke 9 — Matrix pragmas after first `connect()` against production matrix DB (post-quarantine-archive, see post-implementation operations below):**
```
MATRIX_PATH=/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun
Pragmas BEFORE: application_id=0, user_version=0
INFO luna.substrate.database: Matrix application_id set to LUNM (0x4C554E4D); user_version=2 at /Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/data/user/memory_matrix.lun
On-disk after Run 1:
  application_id: 1280659021
  user_version:   2
```

**Smoke 10 — Matrix idempotency (Run 2 against production matrix DB):**
```
(no 'Matrix application_id set' log line)
On-disk after Run 2 (unchanged):
  application_id: 1280659021
  user_version:   2
```

### Post-implementation operations

After the code changes landed, two follow-up actions executed per architect review:

1. **Renamed `_validate_cartridge_open` → `validate_cartridge_open`.** The underscore-plus-`__all__` combination was contradictory signaling. Three reasons the function is genuinely public: (a) smoke tests want to call it directly; (b) `lun fsck` (queued for Phase 5+) will want to validate cartridge files without going through full open machinery; (c) future phases extend the validator (Phase 2 anchor schema checks, Phase 4 logprob_attribution checks). Rename cost now is one `__all__` entry, one definition, one call site — vs N times higher after Phase 2-4 layer on top.

2. **Archived `data/user/luna_engine.db*` quarantine artifacts to `data/user/_archive_2026-05-02_quarantine/`.** Seven files from the 2026-05-02 IH tripwire event (matched DB/SHM/WAL triples for both `-1714` and `-1730` timestamps, plus the 0-byte `luna_engine.db` stub). Cleared `_maybe_rename_legacy_db()` ambiguity check on single-tenant connect path. Forensic state preserved; archive is reversible. Routine pre-migration backups from April (`*.backup.*`, `*.bak`, `*.pre-*`) untouched — different events, different artifacts.

After both operations, re-ran Smoke 9/10 against the production matrix DB. Pragmas migrated cleanly (LUNM, user_version=2); second run idempotent as designed. Production matrix is now v0.2-marked.

### Deviations from spec

None of substance. The handoff's expected decimal pragma values were corrected against the actual hex constants (see note in Smoke test evidence above).

### Open items / follow-ups identified during implementation

1. **Phase 4 readiness check — `e.confidence` column reference at `src/luna/cartridge/__init__.py`** (line shifted by the `__all__`/exception-class additions; find via `grep -n "e.confidence" src/luna/cartridge/__init__.py`). Confirmed present and untouched in Phase 1 per the explicit out-of-scope rule. SPEC-003 (Phase 4) atomic reader patch removes this.

2. **Phase 5 readiness check — v0.1 legacy fallback in `validate_cartridge_open()`** lives at `src/luna/cartridge/__init__.py` after the `__all__` block. Remove the `if app_id == 0: ... return` branch after the migration tool runs against all known v0.1 cartridges.

3. **Lansing cartridge rebuild deferred to Phase 5.** Was not in the tree at implementation time (`find` returned no matches). Phase 5 migration tool covers the rebuild.
