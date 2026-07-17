# SPEC-005: Append-Only Annotation Ledger

**Status:** implemented (2026-05-22; engine commit `407122f`; coupled with companion `SPEC-005_payload-schemas.md`)
**Severity:** high
**Author:** Ahab (with Claude)
**Created:** 2026-05-21
**Last updated:** 2026-05-22 (implemented in Luna Engine commit `407122f`; v0.3 migration event amended to `event_type = 'meta'` for the system actor)
**Affects format version:** v0.3
**Companion spec:** `01_Specs/implemented/SPEC-005_payload-schemas.md` — payload structure per `event_type`; both specs implemented together on 2026-05-22.

---

## Problem statement

A v0.2 cartridge is still a read artifact. Once built, nothing accumulates against
it — no record of an ambassador upgrading a `match_failed` claim to `anchored`, no
record of an elder filtering a contested claim, no record of an oracle's review
result, no record of one community importing another's cartridge and laying down
their own provenance. The format has the columns where these events should land
(SPEC-001 added `claim_sources.event_id` as a forward reference, intentionally
left NULL until this spec exists) but no table where the events themselves live.

Without an event log, the governance arc cannot proceed. Annotations need a home,
trust signals from non-builder actors need a place to attach, and any claim about
"who did what to this cartridge when" needs a structure that resists casual
tampering. The "soft covenant" property (Topic 3 — admin can still drop tables,
but accidental modification is impossible and tampering leaves detectable
evidence) is the right protection level for a research/governance use case where
external verification (publishing the chain root) is a separate concern.

This spec establishes the ledger table, the hash-chain integrity primitive, the
append-only enforcement mechanism, the actor model, the annotation event
taxonomy, and the integration with SPEC-001's forward-referenced provenance
column.

## Observed evidence

The forward references are already in the schema and the prose:

- `01_Specs/implemented/SPEC-001_orphan-claims.md` adds
  `claim_sources.event_id TEXT` and explicitly notes: *"forward ref to ledger
  event (SPEC-005); NULL until ledger exists."* The ambassador upgrade flow
  (lines 164–167) describes the operation but has nowhere to record it as an
  event.
- `01_Specs/implemented/SPEC-003_meaningful-confidence.md` notes:
  *"Annotation events (SPEC-005) will eventually carry their own trust
  signals (ambassador confidence, ledger-event verification scores)."*
- `01_Specs/implemented/SPEC-006_v02-hygiene-bundle.md` lists as a governance
  implication: *"Future ledger entries that reference a `.lun` file can record
  `(file_application_id, file_user_version, file_hash)` as a provenance
  triple."*
- `00_README/README.md` "Active concerns" item 5 (governance arc): *"No ledger,
  no annotations, no contracts."*

The SQLite research has already done the hard work on the implementation
primitive:

- `05_Reference/SQLite_Research.md`, Topic 3 — hash-chain pattern, BEFORE
  trigger semantics, `RAISE(ABORT)` vs `RAISE(ROLLBACK)`, soft-covenant
  honesty, prior art (Fossil, Certificate Transparency).
- `05_Reference/SQLite_CodeMap.md` — `src/trigger.c:104,1382` for trigger
  codegen, `ext/misc/shathree.c` for SHA-3 functions if needed (but the spec
  computes hashes at the application layer per Topic 3's recommendation).

## Root cause analysis

The v0.1 format was designed as a build-once read-many artifact. Builder writes,
reader reads, no third party participates. The governance arc reframes
cartridges as *living substrates* — surfaces against which a community accretes
provenance, dispute, refinement. That reframing requires three things the
current format lacks:

1. **A surface for non-builder writes.** Today, every write to a cartridge
   happens at build time, by a single actor (the builder). The format has no
   notion of additional actors, no place to record their actions, no way to
   distinguish "this was claimed by the source author" from "this was confirmed
   by an ambassador" from "this was disputed by an elder."

2. **An ordering and integrity primitive.** Annotations have temporal order
   (the ambassador upgraded the claim before the elder reviewed it) and
   integrity needs (you cannot silently rewrite the elder's review without
   leaving evidence). The format has neither.

3. **A bridge to existing tables.** SPEC-001's `claim_sources.event_id` is the
   first such bridge: when an ambassador upgrades an anchor, the new
   `claim_sources` row should carry an `event_id` pointing into the ledger so
   the upgrade is auditable. Other tables will gain similar bridges over time
   (claim disputes, summary overrides, embedding corrections), but the ledger
   has to exist first.

## Proposed solution

**Core principle:** append-only event log with application-computed hash chain,
trigger-enforced write semantics, and explicit provenance for every actor
action. The ledger is the source of truth for "what happened to this cartridge
after build"; existing tables gain `event_id` columns as needed to anchor
specific rows to specific events.

### Schema changes

```sql
-- The ledger itself: every annotation event, in insert order.
CREATE TABLE annotation_ledger (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotone sequence within this cartridge
    ulid        TEXT NOT NULL UNIQUE                -- portable cross-cartridge identity
                CHECK (length(ulid) = 26 AND ulid GLOB '[0-9A-HJKMNP-TV-Z]*'),
    entry_ts    INTEGER NOT NULL,                   -- unix ms; monotone non-decreasing with seq
    event_type  TEXT NOT NULL                       -- controlled vocabulary
                CHECK (event_type IN (
                    'claim_anchored',         -- ambassador upgraded match_failed → anchored
                    'claim_disputed',         -- elder flagged a claim for reconciliation
                    'claim_filtered',         -- post-build filtering decision recorded
                    'claim_reconciled',       -- elder resolved a dispute
                    'summary_overridden',     -- elder replaced or supplemented a generated summary
                    'cartridge_reviewed',     -- oracle review of the whole cartridge
                    'cartridge_imported',     -- another community imported this cartridge into theirs
                    'meta'                    -- ledger meta-events (e.g., chain genesis marker)
                )),
    actor_id    TEXT NOT NULL,                      -- ULID identifying the actor
    actor_role  TEXT NOT NULL                       -- role at the time of the event
                CHECK (actor_role IN ('owner', 'ambassador', 'elder', 'oracle', 'system')),
    target_kind           TEXT,                     -- which table the event acts on; NULL for cartridge-wide events
    target_ulid           TEXT,                     -- which row's ULID; NULL for cartridge-wide events
    target_cartridge_ulid TEXT,                     -- which cartridge the target lives in; NULL = current cartridge; only valid for cross-cartridge event types
    payload               TEXT NOT NULL,            -- JSON; event-specific structure (see SPEC-005_payload-schemas.md)
    prev_hash             TEXT,                     -- NULL only for the genesis row
    entry_hash            TEXT NOT NULL UNIQUE      -- SHA-256 of canonical serialization
                          CHECK (length(entry_hash) = 64),
    -- Cross-column invariants:
    CHECK ((target_kind IS NULL) = (target_ulid IS NULL)),
    CHECK (target_cartridge_ulid IS NULL
           OR event_type IN ('cartridge_imported', 'cartridge_reviewed')),
    CHECK (prev_hash IS NULL OR length(prev_hash) = 64),
    CHECK (actor_role != 'system' OR event_type = 'meta')  -- system actor reserved for chain meta-events
);

CREATE INDEX idx_ledger_target ON annotation_ledger(target_kind, target_ulid);
CREATE INDEX idx_ledger_actor ON annotation_ledger(actor_id);
CREATE INDEX idx_ledger_type ON annotation_ledger(event_type);
CREATE INDEX idx_ledger_ts ON annotation_ledger(entry_ts);

-- Append-only enforcement (per Topic 3 / src/trigger.c:1382).
-- These triggers convert any UPDATE or DELETE into a SQLITE_CONSTRAINT abort.
-- This is a soft covenant: an admin with the sqlite3 CLI can still bypass it
-- via PRAGMA writable_schema=ON, DROP TABLE, or trigger disable. See the
-- "Soft-covenant honesty" subsection of Governance implications.
CREATE TRIGGER annotation_ledger_no_update
BEFORE UPDATE ON annotation_ledger
BEGIN
    SELECT RAISE(ABORT, 'annotation_ledger is append-only: updates forbidden');
END;

CREATE TRIGGER annotation_ledger_no_delete
BEFORE DELETE ON annotation_ledger
BEGIN
    SELECT RAISE(ABORT, 'annotation_ledger is append-only: deletes forbidden');
END;

-- Actor registry: per-cartridge identity for each actor that has touched the ledger.
-- Separate from the ledger itself so actor metadata can be inspected without
-- traversing every event; also so a key rotation or display-name change is
-- a single-row UPDATE rather than a ledger event.
CREATE TABLE annotation_actors (
    actor_id     TEXT PRIMARY KEY                   -- ULID
                 CHECK (length(actor_id) = 26 AND actor_id GLOB '[0-9A-HJKMNP-TV-Z]*'),
    display_name TEXT NOT NULL,
    first_seen   INTEGER NOT NULL,                  -- unix ms; first ledger event's entry_ts
    last_seen    INTEGER NOT NULL,                  -- unix ms; most recent ledger event's entry_ts
    primary_role TEXT NOT NULL                      -- most-common role across this actor's events
                 CHECK (primary_role IN ('owner', 'ambassador', 'elder', 'oracle', 'system')),
    public_key   TEXT                               -- optional Ed25519 public key for signed events; NULL allowed
) WITHOUT ROWID;

-- Meta keys added by this spec:
-- 'ledger_hash_algorithm' = 'sha256'         -- documents the hash function for the chain
-- 'ledger_genesis_ulid'   = <ULID of genesis row>  -- shortcut for chain verification
-- 'ledger_head_seq'       = <max seq>        -- updated on each event; lets readers fast-check "is there new activity"
-- 'ledger_head_hash'      = <head entry_hash>  -- the published chain root for external verification
```

The genesis row is inserted at cartridge build time with
`event_type = 'meta'`, `actor_role = 'system'`, `prev_hash = NULL`, and a
`payload` describing the cartridge identity at genesis (the
`(application_id, user_version, source_hash)` provenance triple from SPEC-006).
Every subsequent event chains back through `prev_hash`.

**System actor sentinel.** The system actor's identity is a fixed sentinel
ULID: `SYSTEM_ACTOR_ULID = '00000000000000000000000000'` (26 zero characters).
The sentinel passes the standard ULID format CHECK (`length(actor_id) = 26`
and `actor_id GLOB '[0-9A-HJKMNP-TV-Z]*'` — `0` is in the Crockford alphabet)
without needing a CHECK exemption. Real ULIDs cannot collide with it: the
first 10 characters encode a unix-ms timestamp, so all-zeros encodes
`1970-01-01T00:00:00Z`, which no real cartridge build will ever produce.
The sentinel is recorded once in `annotation_actors` at genesis with
`display_name = 'system'`, `primary_role = 'system'`.

### The hash chain

The canonical serialization that goes into the hash:

```
canonical = "|".join([
    str(seq),
    str(entry_ts),
    event_type,
    actor_id,
    actor_role,
    target_kind or "",
    target_ulid or "",
    target_cartridge_ulid or "",
    payload,                   # exact JSON bytes as stored, not re-serialized at hash time
    prev_hash or ""
])
entry_hash = sha256(canonical.encode("utf-8")).hexdigest()
```

Ten fields, pipe-separated, hashed under SHA-256. The 8th field
(`target_cartridge_ulid`) participates in the hash even when NULL (as `""`),
which is why it was added in this spec rather than a follow-up — every
chain in existence must have been built knowing about this field, otherwise
hashes computed before its introduction would diverge from hashes computed
after, and chain verification would fragment by cartridge build date.

The pipe (`|`) is the field separator. Empty string is used for NULL fields
so the serialization is unambiguous (`||` always means "NULL-or-empty field
between"). The payload is taken exactly as it will be stored — no
re-serialization, no normalization at hash time — so the on-disk bytes and
the hash-input bytes are identical. This is critical: any normalization at
hash time creates an attack surface where two different on-disk states hash
to the same value.

**Payload serialization mandate (normative).** Writers MUST produce payload
bytes via canonical JSON serialization: sorted keys, compact separators,
UTF-8, no ASCII-escaping of non-ASCII codepoints. The Python reference form
is:

```python
payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

Any other language producing payloads for a Luna cartridge MUST emit
byte-equivalent output. This mandate does not conflict with the "hash-input
bytes equal on-disk bytes" rule — the hash is still computed over stored
bytes, never re-serialized at hash time — but it constrains writers so that
two independent correct implementations of the same operation produce
identical payload bytes, hash to identical `entry_hash` values, and the
chain stays portable across implementations. Without this mandate, the
"anyone can verify" property silently degrades to "anyone using the same
writer can verify." `validate_ledger()` re-parses each payload and
re-serializes with canonical settings; any byte mismatch is a `BuildError`.

**Hash algorithm: SHA-256** (64 hex chars). Documented in
`meta.ledger_hash_algorithm = 'sha256'` machine-readably so a future spec
can adopt a different algorithm for *new* cartridges. **The algorithm is
locked at genesis.** Once a cartridge has been built with
`meta.ledger_hash_algorithm = X`, that value is immutable for the lifetime
of the cartridge. Migrating an existing chain to a new algorithm would
require recomputing every `entry_hash`, which invalidates any external
root that was previously published — defeating the whole point of the
chain. If SHA-256 is ever broken, the response is at the format-version
layer: v0.4+ cartridges use the new algorithm; existing v0.3 cartridges
retain SHA-256 with the understood degradation. The chain is not a moving
target.

### Behavioral changes

**Builder (`src/luna/cartridge/builder.py`):**

1. After finalizing the cartridge body but before the SPEC-006 finalize pragma
   stack, insert the genesis row:
   ```python
   genesis_payload = json.dumps({
       "application_id": "0x4C554E43",
       "user_version": 2,    # or 3 once v0.3 ships
       "source_hash": meta["source_hash"],
       "format_version": meta["format_version"],
   }, sort_keys=True, separators=(",", ":"))
   insert_ledger_event(
       conn,
       event_type="meta",
       actor_id=SYSTEM_ACTOR_ULID,
       actor_role="system",
       target_kind=None,
       target_ulid=None,
       payload=genesis_payload,
   )
   ```
2. Initialize `meta.ledger_hash_algorithm`, `meta.ledger_genesis_ulid`,
   `meta.ledger_head_seq`, `meta.ledger_head_hash`.
3. Register the system actor row in `annotation_actors`.

**New module (`src/luna/cartridge/ledger.py`):**

The canonical insert pattern, transaction-wrapped:

```python
SYSTEM_ACTOR_ULID = "00000000000000000000000000"

def insert_ledger_event(
    conn: sqlite3.Connection,
    event_type: str,
    actor_id: str,
    actor_role: str,
    payload: str,                      # caller produces via canonical JSON; see "Payload serialization mandate"
    target_kind: str | None = None,
    target_ulid: str | None = None,
    target_cartridge_ulid: str | None = None,
) -> tuple[int, str]:
    """Insert an event, compute the hash chain link, update head pointers.
    Returns (seq, entry_hash). Raises LedgerError on any invariant violation.
    All operations happen in a single transaction; partial writes are impossible.

    target_cartridge_ulid is only valid when event_type is 'cartridge_imported'
    or 'cartridge_reviewed'; the table-level CHECK enforces this at insert."""

    with conn:  # transaction
        # 1. Lock the ledger head against concurrent writers (BEGIN EXCLUSIVE preferred).
        head = conn.execute(
            "SELECT value FROM meta WHERE key = 'ledger_head_hash'"
        ).fetchone()
        prev_hash = head[0] if head and head[0] else None

        # 2. Generate the event's ULID and timestamp.
        event_ulid = generate_ulid()
        entry_ts = int(time.time() * 1000)

        # 3. Compute hash over (seq, ts, type, actor_id, role, target_kind,
        #    target_ulid, payload, prev_hash). seq is the next AUTOINCREMENT
        #    value; we pre-compute it via sqlite_sequence to make the hash
        #    deterministic before insert.
        next_seq = (conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM annotation_ledger"
        ).fetchone()[0])
        canonical = "|".join([
            str(next_seq),
            str(entry_ts),
            event_type,
            actor_id,
            actor_role,
            target_kind or "",
            target_ulid or "",
            target_cartridge_ulid or "",
            payload,
            prev_hash or "",
        ])
        entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        # 4. Insert the event row.
        conn.execute(
            """INSERT INTO annotation_ledger
               (seq, ulid, entry_ts, event_type, actor_id, actor_role,
                target_kind, target_ulid, target_cartridge_ulid,
                payload, prev_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (next_seq, event_ulid, entry_ts, event_type, actor_id, actor_role,
             target_kind, target_ulid, target_cartridge_ulid,
             payload, prev_hash, entry_hash),
        )

        # 5. Touch the actor row (UPSERT into annotation_actors).
        conn.execute(
            """INSERT INTO annotation_actors
               (actor_id, display_name, first_seen, last_seen, primary_role)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(actor_id) DO UPDATE SET last_seen = excluded.last_seen""",
            (actor_id, actor_id, entry_ts, entry_ts, actor_role),
        )

        # 6. Update the meta head pointers (UPSERT).
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('ledger_head_seq', ?)",
            (str(next_seq),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('ledger_head_hash', ?)",
            (entry_hash,),
        )

        return next_seq, entry_hash
```

**SPEC-001 ambassador upgrade flow becomes:**

```python
def ambassador_upgrade_claim(
    conn: sqlite3.Connection,
    claim_id: int, node_id: int,
    actor_id: str,
) -> None:
    """Upgrade a match_failed claim to anchored, with full ledger provenance."""
    with conn:
        # 1. Insert the ledger event first; the event_id becomes the FK target.
        upgrade_payload = json.dumps({
            "claim_id": claim_id,
            "node_id": node_id,
            "reason": "ambassador_upgrade",
        }, sort_keys=True, separators=(",", ":"))
        _, event_hash = insert_ledger_event(
            conn,
            event_type="claim_anchored",
            actor_id=actor_id,
            actor_role="ambassador",
            target_kind="extractions",
            target_ulid=fetch_extraction_ulid(conn, claim_id),
            payload=upgrade_payload,
        )

        # 2. Insert the claim_sources row pointing at the event.
        conn.execute(
            """INSERT INTO claim_sources
               (claim_id, node_id, anchor_method, anchored_by, anchored_at, event_id)
               VALUES (?, ?, 'manual', ?, ?, ?)""",
            (claim_id, node_id, actor_id, int(time.time() * 1000), event_hash),
        )

        # 3. Flip the extraction's anchor_status.
        conn.execute(
            "UPDATE extractions SET anchor_status = 'anchored' WHERE id = ?",
            (claim_id,),
        )
```

The whole operation is one transaction. Either all three writes commit
together or none do; there is never a `claim_sources` row pointing at a
nonexistent ledger event.

**Reader (`src/luna/cartridge/__init__.py` and consumers):**

1. New module-level helper `verify_ledger_chain(conn)` walks the ledger in
   `seq` order, recomputes each `entry_hash`, asserts `prev_hash` matches the
   prior row's `entry_hash`. Returns `(ok, broken_at_seq)`. Fast path: verify
   only that `meta.ledger_head_hash` matches the actual head row's
   `entry_hash` and that `meta.ledger_head_seq` matches `MAX(seq)`. Full
   verification is opt-in (`lun fsck --full-chain`); fast verification runs
   on every cartridge open.
2. `resolve_source_ref()` and other anchor consumers gain optional
   `include_provenance=True` parameter. When set, each `claim_sources` row
   returns alongside its associated ledger event if `event_id` is populated.
3. Reader exposes a `ledger_events(target_ulid)` function for retrieving the
   full event history of any anchorable row.

**`lun fsck` additions:**

- `--ledger` flag: walk the chain, verify every link, report break point and
  hex diff of the recomputed vs stored `entry_hash`.
- `--ledger-head`: fast-check that meta head pointers match the actual head.
- Default `lun fsck` runs the fast head check; full chain verification is
  opt-in because it's O(n) and most cartridges grow long ledgers over time.

### Migration path

The ledger is a v0.3 addition. Two migration scenarios:

**1. Fresh v0.3 builds.** Builder writes the ledger table, triggers, actors
table, meta keys, and genesis row at creation. No migration logic.

**2. Existing v0.2 cartridges.** The `lun migrate v2-to-v3` tool:
   - Creates `annotation_ledger`, `annotation_actors`, both triggers, all
     indexes.
   - Inserts a genesis row with `event_type = 'meta'`, `actor_role =
     'system'`, payload describing the cartridge identity *at migration time*
     (the cartridge's current `application_id`, `user_version` post-migration,
     and `source_hash`).
   - Adds a second meta event with `event_type = 'meta'` and `actor_role =
     'system'` recording the migration itself (`action =
     "migrated_v2_to_v3"`, pre-migration `user_version`, post-migration
     `user_version`, timestamp). This keeps the system actor reserved for chain
     meta-events and makes the migration auditable from the ledger.
   - Sets `meta.ledger_hash_algorithm = 'sha256'`,
     `meta.ledger_genesis_ulid`, `meta.ledger_head_seq`,
     `meta.ledger_head_hash`.

The migration is **read-compatible**: v0.3 readers handle both v0.2 and v0.3
cartridges (v0.2 cartridges report "no ledger" rather than failing). Old v0.2
readers seeing a v0.3 cartridge ignore the new table and triggers (forward-
compat fallback for SELECT on unknown tables is to skip them; for unknown
trigger-source events the table simply doesn't exist in their query plan).

**No data loss in either direction.** Going from v0.2 to v0.3 is purely
additive. There is no v0.3 → v0.2 downgrade path defined — once the ledger
exists, removing it would discard provenance that has accumulated.

## Migration mechanics (SQLite-specific)

`CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`: all DDL-only operations on
new tables. O(1) regardless of existing cartridge size. Cross-reference
`05_Reference/SQLite_Research.md` Topic 5.

**Trigger creation order matters.** Create the table first, then the indexes,
then the triggers. SQLite allows triggers to reference tables that don't exist
yet at creation time (delayed validation), but the canonical migration order
is table → indexes → triggers so that any failure mode produces a clean
partial state. Cross-reference `05_Reference/SQLite_CodeMap.md`
`src/trigger.c:104` for trigger registration semantics.

**Trigger interaction with other DDL.** Per `05_Reference/SQLite_Research.md`
Topic 5, a full table rewrite (CREATE-new → INSERT-SELECT → DROP-old →
RENAME) drops all triggers on the old table. This is the #1 migration
footgun. The ledger triggers must be recreated as part of any future
migration that rewrites `annotation_ledger`. The migration tool is
responsible for this; the format spec doesn't allow `annotation_ledger`
rewrites without explicit trigger reattachment as part of the same
transaction.

**The `INSERT OR REPLACE INTO meta` UPSERTs are O(1)** — meta is a small
key-value table. The composite index `idx_ledger_target` is the only index
that could grow large; it's a normal B-Tree index, O(log n) inserts.

**Transaction scope.** Every ledger insert (and every operation that produces
a ledger event) must run inside a single transaction that covers: the
`annotation_ledger` row, the `annotation_actors` UPSERT, the two `meta` head
pointer UPSERTs, and any related row updates (e.g., the SPEC-001
`claim_sources` insert + `extractions.anchor_status` UPDATE for an ambassador
upgrade). `BEGIN IMMEDIATE` or `BEGIN EXCLUSIVE` prevents two concurrent
writers from racing on `MAX(seq)`. Cross-reference Topic 5's locking
guidance.

## Validation rules

Build time, runs in `validate_ledger()` before cartridge finalization:

```python
EXPECTED_NO_UPDATE_DDL = (
    "CREATE TRIGGER annotation_ledger_no_update "
    "BEFORE UPDATE ON annotation_ledger "
    "BEGIN "
    "SELECT RAISE(ABORT, 'annotation_ledger is append-only: updates forbidden'); "
    "END"
)
EXPECTED_NO_DELETE_DDL = (
    "CREATE TRIGGER annotation_ledger_no_delete "
    "BEFORE DELETE ON annotation_ledger "
    "BEGIN "
    "SELECT RAISE(ABORT, 'annotation_ledger is append-only: deletes forbidden'); "
    "END"
)

def _normalize_ddl(s: str) -> str:
    """Collapse whitespace runs to single spaces for DDL comparison."""
    return " ".join(s.split())

def validate_ledger(conn):
    """Ledger structural invariants and hash chain integrity (full check, O(n))."""

    # 0. Append-only triggers exist and DDL matches expected.
    #    An admin who DROPs these triggers, mutates rows, then recreates them
    #    bypasses the chain integrity check unless the recreated DDL matches
    #    exactly. Whitespace-normalized comparison raises the bypass bar.
    for trig_name, expected in (
        ("annotation_ledger_no_update", EXPECTED_NO_UPDATE_DDL),
        ("annotation_ledger_no_delete", EXPECTED_NO_DELETE_DDL),
    ):
        row = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
            (trig_name,),
        ).fetchone()
        if not row:
            raise LedgerError(f"append-only trigger {trig_name} is missing")
        if _normalize_ddl(row[0]) != _normalize_ddl(expected):
            raise LedgerError(
                f"append-only trigger {trig_name} DDL has been modified"
            )

    # 1. Genesis row exists and is well-formed.
    genesis = conn.execute("""
        SELECT seq, event_type, actor_role, actor_id, prev_hash, entry_hash
        FROM annotation_ledger WHERE seq = 1
    """).fetchone()
    if not genesis:
        raise BuildError("annotation_ledger has no genesis row (seq=1)")
    g_seq, g_type, g_role, g_aid, g_prev, g_hash = genesis
    if g_type != "meta" or g_role != "system" or g_prev is not None:
        raise BuildError(
            f"Genesis row malformed: type={g_type!r}, role={g_role!r}, "
            f"prev_hash={g_prev!r}; expected ('meta', 'system', NULL)"
        )
    if g_aid != SYSTEM_ACTOR_ULID:
        raise BuildError(
            f"Genesis row actor_id={g_aid!r}; expected SYSTEM_ACTOR_ULID "
            f"({SYSTEM_ACTOR_ULID!r})"
        )

    # 2. Hash chain is intact: every prev_hash matches the prior row's entry_hash,
    #    and every payload is canonically serialized.
    rows = conn.execute("""
        SELECT seq, entry_ts, event_type, actor_id, actor_role,
               target_kind, target_ulid, target_cartridge_ulid,
               payload, prev_hash, entry_hash
        FROM annotation_ledger ORDER BY seq
    """).fetchall()
    expected_prev = None
    for row in rows:
        seq, ts, etype, aid, arole, tkind, tulid, tcart, payload, prev, stored_hash = row
        if prev != expected_prev:
            raise BuildError(
                f"Chain break at seq={seq}: prev_hash={prev!r} but "
                f"expected {expected_prev!r}"
            )
        # 2a. Payload canonical re-serialization check (serializer mandate).
        try:
            reparsed = json.loads(payload)
            recanon = json.dumps(
                reparsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
        except (ValueError, TypeError) as e:
            raise BuildError(f"seq={seq}: payload is not valid JSON: {e}")
        if recanon != payload:
            raise BuildError(
                f"seq={seq}: payload bytes are not canonical JSON. "
                f"stored={payload!r} canonical={recanon!r}"
            )
        # 2b. Hash recompute over canonical 10-field serialization.
        canonical = "|".join([
            str(seq), str(ts), etype, aid, arole,
            tkind or "", tulid or "", tcart or "", payload, prev or "",
        ])
        recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if recomputed != stored_hash:
            raise BuildError(
                f"Hash mismatch at seq={seq}: stored={stored_hash}, "
                f"recomputed={recomputed}"
            )
        expected_prev = stored_hash

    # 3. Meta head pointers match the actual head.
    if rows:
        head_seq = rows[-1][0]
        head_hash = rows[-1][10]
        meta_head_seq = conn.execute(
            "SELECT value FROM meta WHERE key = 'ledger_head_seq'"
        ).fetchone()
        meta_head_hash = conn.execute(
            "SELECT value FROM meta WHERE key = 'ledger_head_hash'"
        ).fetchone()
        if not meta_head_seq or int(meta_head_seq[0]) != head_seq:
            raise BuildError(
                f"meta.ledger_head_seq={meta_head_seq} disagrees with "
                f"actual head seq={head_seq}"
            )
        if not meta_head_hash or meta_head_hash[0] != head_hash:
            raise BuildError(
                f"meta.ledger_head_hash disagrees with actual head entry_hash"
            )

    # 4. Every event with target_kind / target_ulid resolves.
    unresolved = conn.execute("""
        SELECT seq, target_kind, target_ulid FROM annotation_ledger
        WHERE target_kind IS NOT NULL AND target_ulid IS NOT NULL
    """).fetchall()
    for seq, tkind, tulid in unresolved:
        if tkind not in {"extractions", "doc_nodes", "claim_sources", "annotation_ledger"}:
            raise BuildError(f"seq={seq}: unknown target_kind={tkind!r}")
        # Cross-table lookup; details depend on tkind. "annotation_ledger"
        # supports self-referential events like claim_reconciled, which
        # reference a prior claim_disputed event by ULID (the companion
        # SPEC-005_payload-schemas.md defines the per-event-type semantics).
        ...

    # 5. annotation_actors aggregates match the ledger.
    actor_counts = conn.execute("""
        SELECT actor_id, MIN(entry_ts), MAX(entry_ts), COUNT(*)
        FROM annotation_ledger GROUP BY actor_id
    """).fetchall()
    for aid, first, last, _count in actor_counts:
        row = conn.execute(
            "SELECT first_seen, last_seen FROM annotation_actors WHERE actor_id = ?",
            (aid,),
        ).fetchone()
        if not row or row[0] != first or row[1] != last:
            raise BuildError(
                f"annotation_actors disagrees with ledger for actor_id={aid}"
            )

    # 6. meta.ledger_hash_algorithm matches what was used.
    algo = conn.execute(
        "SELECT value FROM meta WHERE key = 'ledger_hash_algorithm'"
    ).fetchone()
    if not algo or algo[0] != "sha256":
        raise BuildError(
            f"meta.ledger_hash_algorithm must be 'sha256' for v0.3; got {algo}"
        )
```

Read time falls into three tiers, separated because cost scales differently:

- **Fast-open path** (always, on every `validate_cartridge_open()`): trigger
  DDL check from `validate_ledger()` Step 0 + head pointer check
  (`meta.ledger_head_seq` and `meta.ledger_head_hash` match the actual head
  row). Both are O(1) regardless of ledger size. This is the floor: a v0.3
  cartridge that fails either check refuses to open.
- **`lun fsck` default** (on demand, but cheap enough to run often): the
  fast-open path plus a sanity walk of the last 100 events (configurable
  via `--tail N`). Catches recent corruption without paying the full O(n)
  cost. Typical run is <50ms regardless of total ledger length.
- **`lun fsck --full-chain`** (opt-in, expensive): the full
  `validate_ledger()` walk above — every payload re-serialization, every
  hash recompute, every chain link. Cost is O(n) in events; a 10K-event
  ledger takes ~100ms–1s depending on hardware. Also runs automatically at
  the end of `lun migrate` (n is small at migration time because the chain
  was just created). External verification — publishing
  `meta.ledger_head_hash` to any witness — provides equivalent tamper
  detection at the network layer without the per-open cost.
- **`lun fsck --resolve-targets`** (opt-in, independent flag): every event
  with a `target_ulid` resolves to a real row. Catches dangling references
  introduced by future schema changes that didn't migrate the ledger. Can
  combine with `--full-chain`.

## Governance implications

This spec is the governance arc. Everything else SPEC-001 / SPEC-003 / SPEC-006
implies about non-builder actors, dispute, refinement, and audit becomes
operational once the ledger exists.

**Soft-covenant honesty.** The append-only trigger pattern (per Topic 3) is a
*soft covenant*, not a cryptographic guarantee. An admin with `sqlite3` CLI
access can:

- `PRAGMA writable_schema = ON; UPDATE sqlite_schema SET sql = '...' WHERE name = 'annotation_ledger_no_update';` — disable the trigger
- `DROP TABLE annotation_ledger` — destroy the chain entirely
- Replace the cartridge file with a forged version

What the soft covenant *does* provide:

- Application code cannot accidentally modify or delete events (every write
  path goes through `insert_ledger_event()`).
- Tampering is *detectable* — any modification breaks the chain at the
  modification point, and `validate_ledger()` reports it.
- External verification is possible: publishing `meta.ledger_head_hash` to
  any external witness (a public chronicle, a community registry, a
  signature service) lets observers detect divergence between the published
  root and the actual file.

The spec explicitly does not pursue cryptographic tamper-proofing inside
SQLite. The mitigation for that risk is external verification, not internal
hardening. Cross-reference Topic 3.

**Anchor provenance becomes real.** SPEC-001 added `claim_sources.event_id`
as a forward reference. With this spec, an ambassador upgrade or migration
event populates that column with the relevant ledger event's `entry_hash`,
giving every anchor a verifiable provenance trail.

**Multi-actor trust composition.** SPEC-004 (implemented, 2026-05-22) reads anchor signals
from SPEC-001 + extraction signals from SPEC-003 to compose multi-axis
trust. With this spec, SPEC-004 gains access to *who* anchored what and
*when*, which directly informs the authority and temporal axes.

**Cross-cartridge governance.** When community A imports community B's
cartridge, A's actions against B's cartridge are recorded in B's ledger
(if A has write access to B's file) or A's own ledger (if not). The
`cartridge_imported` event type is the entry point for this; the full
cross-cartridge interaction model is out of scope for this spec but the
event taxonomy reserves the slot.

**Owner / ambassador / elder / oracle roles** are named in the schema but
their *permissions* (who can issue which event types, who can override
whom, who can declare a reconciliation final) are deliberately left
unspecified. That's a separate spec — actor roles spec, currently
unnumbered — that will declare the permission matrix. This spec provides
the substrate; permissions layer on top.

## Alternatives considered

**Alt 1: Use Git-style commit chain instead of seq + prev_hash.**
Rejected. Git's model (DAG with merge commits) is overkill for a single-cartridge
append log; the linear chain is sufficient and SQL-native. Multi-cartridge
event reconciliation may eventually want DAG semantics, but that's a different
spec scope (cross-cartridge governance), not this one.

**Alt 2: Use SHA-3 (via `ext/misc/shathree.c`) instead of SHA-256.**
Considered. SHA-3 is the more modern algorithm and the SQLite extension is
available. Rejected because: (a) it requires loading an extension at every
read site, which contradicts the working principle that generic SQLite
tooling should be sufficient; (b) SHA-256 has stronger ecosystem support
in standard library implementations (every language has it in stdlib);
(c) the soft-covenant honesty section explicitly acknowledges the chain
isn't a cryptographic primitive in the strong sense — the choice of SHA-256
vs SHA-3 is not load-bearing for the threat model. The `meta.ledger_hash_algorithm`
key documents the choice machine-readably so a future spec can switch.

**Alt 3: Use HMAC-SHA256 with a per-cartridge key instead of plain SHA-256.**
Rejected. The whole point of the chain is public verifiability — anyone with
the cartridge should be able to verify the chain. A keyed MAC requires
distributing the key to verifiers, which collapses to either "the key is
public" (no benefit) or "the key is private" (no public verification).
Signing individual entries with Ed25519 (per actor) is a better fit for
authenticity claims and is reserved for a future spec via the
`annotation_actors.public_key` column.

**Alt 4: Make the trigger `RAISE(ROLLBACK)` instead of `RAISE(ABORT)`.**
Rejected. Per Topic 3, `ABORT` rolls back the current statement and leaves
the surrounding transaction intact; `ROLLBACK` kills the whole transaction.
For a trigger that fires on attempted ledger mutation, `ABORT` is correct:
the application gets `SQLITE_CONSTRAINT` and can handle it, rather than
losing all its other in-flight work. `ROLLBACK` is appropriate for invariant
violations where the partial state is corrupt; an attempted UPDATE on a
ledger row is not corrupting state, just attempting a forbidden operation.

**Alt 5: Store the payload as BLOB (binary) instead of TEXT (JSON).**
Rejected. JSON is queryable in standard SQLite (via the JSON1 extension,
compiled in by default since 3.38). BLOBs require an external schema to
interpret. The hash-input is bytes either way, so the choice doesn't affect
integrity. JSON loses ~10% on size vs binary packing for small payloads;
gains everything on inspectability.

**Alt 6: Allow updates to `annotation_ledger.payload` for "correction" events,
gated by an additional `corrected_by` event.**
Rejected. Once a row is mutable, the chain semantics evaporate — even if a
"correction event" references the original, the original's hash no longer
matches its stored content. Corrections are new events that reference the
original; they don't modify it. This is the same principle as a git commit
that fixes a typo: a new commit, not a mutation of the old one.

**Alt 7: Inline actor metadata in the ledger row instead of a separate
`annotation_actors` table.**
Rejected. Inlining means every event row carries duplicate actor metadata
(display name, role at that time, etc.), which (a) bloats the chain
canonical serialization, (b) makes a name change require a new ledger event
just to update the inline copy, (c) prevents efficient "list all events by
actor X" queries without scanning the whole ledger. Keeping actor metadata
in a separate table is the standard relational decomposition.

**Alt 8: Use rowid as the seq (no separate `seq` column).**
Rejected. The hash chain canonical serialization needs the sequence number,
which means it must be deterministic at hash-compute time. `rowid` is
assigned at insert time and is the same as `seq` in the typical case, but
using a named `seq` column makes the intent explicit and survives any
future migration that touches rowid semantics.

**Alt 9: Single global ledger across all cartridges, instead of per-cartridge ledger.**
Rejected. A cartridge is a portable, self-contained unit; its ledger must
travel with it. A global ledger would require synchronization between
distributors, which collapses the portability property. Per-cartridge ledger
+ external publishing of head hashes is the correct decomposition: each
cartridge is self-verifiable; cross-cartridge agreement uses the published
roots.

**Alt 10: Defer the actor model entirely; record only event_type + payload.**
Rejected. The whole point of the governance arc is "who did what when." An
event log without `actor_id` is just a change log. Recording the actor is
the smallest unit of work that makes the ledger useful for governance.

## Open questions

None remaining. All seven open questions from the 2026-05-21 draft were
resolved the same day:

1. **Cross-cartridge `target_ulid` references — resolved by adding the
   column now.** `annotation_ledger` gains `target_cartridge_ulid TEXT`
   (nullable) with `CHECK (target_cartridge_ulid IS NULL OR event_type IN
   ('cartridge_imported', 'cartridge_reviewed'))`. Adding it now is
   non-negotiable: the column participates in the canonical 10-field
   hash serialization, so adding it later would invalidate every
   pre-existing chain's hashes under the new serialization (a chain-era
   boundary event would mitigate this but introduces exactly the
   per-cartridge algorithm-shift complexity that Q4 rejects on
   principle). NULL means "current cartridge"; populated means
   cross-cartridge target. The CHECK pins which event types can
   semantically reference another cartridge.

2. **Payload serializer mandate — resolved as mandate, elevated to
   normative.** Writers MUST produce payload bytes via canonical JSON
   (`json.dumps(value, sort_keys=True, separators=(",", ":"),
   ensure_ascii=False).encode("utf-8")` or byte-equivalent in another
   language). `validate_ledger()` re-parses each payload and re-serializes
   with canonical settings; mismatch is a `BuildError`. This preserves
   the "hash-input bytes = on-disk bytes" rule (writers are constrained,
   not the hash function) while killing the failure mode where two
   correct implementations produce different bytes and the chain stops
   being independently verifiable.

3. **System actor identity — resolved with sentinel
   `'00000000000000000000000000'`.** The sentinel passes the standard
   ULID format CHECK without exemption (26 chars, all in Crockford
   Base32); real ULID generators cannot collide because the first 10
   chars encode unix-ms timestamp, and all-zeros means
   `1970-01-01T00:00:00Z`, which no real cartridge build will produce.
   Rejected the generated-ULID-in-meta option (forces verifiers into an
   extra lookup) and the cartridge's-own-ULID option (conflates
   cartridge-as-target with cartridge-as-actor).

4. **Hash function migration path — resolved with algorithm immutability.**
   Once a cartridge has been built with `meta.ledger_hash_algorithm = X`,
   that value is immutable for the lifetime of the cartridge. If SHA-256
   is ever broken, the fix lives at the format-version layer: v0.4+
   cartridges use the new algorithm; existing v0.3 cartridges retain
   SHA-256 with understood degradation. Rejected per-cartridge
   recomputation (destroys the integrity of all previously-published
   roots) and chain-era boundary events (splits one chain into N glued
   chains, complicates every verifier for marginal gain).

5. **Payload schemas — resolved in companion spec.**
   `SPEC-005_payload-schemas.md` (also in `01_Specs/implemented/`) declares
   the required keys per `event_type`, the unknown-key preservation rule
   (readers must preserve unknown keys since they're hashed in, but may
   ignore them semantically), the build-time payload validation, and the
   "Adding new event types" process governing format-taxonomy growth.
   The companion's own open questions (severity enum lock, partial_aspect
   conditional, target_field vocabulary lock, relationship requirement,
   extensibility process) are all resolved. Both specs moved to
   `implemented/` together.

6. **Trigger DDL verification on read — resolved as fast-path check.**
   `validate_ledger()` Step 0 queries `sqlite_schema` for both
   append-only triggers and compares whitespace-normalized DDL against
   expected constants (`EXPECTED_NO_UPDATE_DDL`,
   `EXPECTED_NO_DELETE_DDL`). Both are O(1). This raises the bar on the
   most obvious tamper path (drop triggers → mutate rows → recompute
   hashes → recreate triggers): the admin would also have to restore
   exact DDL bytes the reader expects. Soft-covenant framing per Topic
   3 — not cryptographic, just intent-vs-opportunity.

7. **`lun fsck` default behavior — resolved as tiered.** Fast-open path
   (trigger DDL check + head pointer check, both O(1)) runs on every
   `validate_cartridge_open()`. `lun fsck` default adds a sanity walk of
   the last N events (default 100, configurable). `lun fsck --full-chain`
   pays the full O(n) cost; `lun migrate` runs it automatically at
   migration end. External verification (publishing
   `meta.ledger_head_hash` to a witness) handles tamper detection at the
   network layer without per-open cost.

## Dependencies

Upstream (must be implemented before this spec):

- **SPEC-006 (implemented)** — establishes `application_id`, `user_version`,
  the meta-key contract this spec extends, and the v0.3 migration framework
  this spec plugs into.
- **SPEC-001 (implemented)** — established `claim_sources.event_id` as a
  forward reference; this spec resolves it. SPEC-001's `validate_anchors()`
  invariant about non-auto anchors requiring provenance is enforced via the
  ledger from this spec forward.
- **SPEC-002 (implemented)** — provides the ULID identity layer that
  `target_ulid`, `actor_id`, and the ledger's own `ulid` column build on.
- **SPEC-003 (implemented)** — its `extraction_method = 'manual'` value
  becomes operational with this spec (a manual extraction comes from an
  ambassador event in the ledger).

Blocks:

- **SPEC-004 (implemented, 2026-05-22; multi-axis weights)** — reads ledger events to weight
  trust signals by actor authority and event recency.
- **Actor roles spec (planned, unnumbered)** — defines permission matrix
  over event_types × actor_roles. This spec leaves permissions deliberately
  unspecified.
- **Cross-cartridge governance spec (planned, unnumbered)** — defines how
  events propagate across cartridge boundaries and how the cross-cartridge
  event resolution works.

Coupled (implemented together):

- **`SPEC-005_payload-schemas.md` (implemented, companion)** — declares payload
  structure per `event_type`, the unknown-key preservation rule, and the
  format-taxonomy extensibility process. Its `validate_payloads()` is
  Step 7 of this spec's `validate_ledger()`.

## Implementation notes

- Commit/PR reference: Luna Engine commit `407122f` (`feat(cartridge): SPEC-005 v0.3 schema + annotation ledger + lun fsck`)
- Implementation date: 2026-05-22
- Verification: 81 SPEC-005 tests passed in 0.81s; `lun fsck` passed default, `--ledger`, and `--payloads`; build -> migrate roundtrip produced v0.3 cartridges with genesis plus migration ledger rows.
- Deviations from pre-implementation draft: the v0.2 -> v0.3 migration event is a system `meta` event, not `system + cartridge_imported`, because the ledger CHECK reserves the system actor for meta-events.
- Follow-up issues created: no tracker items in this docs-only closeout; next separate briefs are ambassador-upgrade ledger wiring, ReaderPrototype v0.3 support, SPEC-004 composer integration, and the Meditations v0.3 audit.
