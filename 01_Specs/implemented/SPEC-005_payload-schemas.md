# SPEC-005 (companion): Annotation Event Payload Schemas

**Status:** implemented (2026-05-22; engine commit `407122f`; coupled with parent `SPEC-005_annotation-ledger.md`)
**Severity:** high
**Author:** Ahab (with Claude)
**Created:** 2026-05-21
**Last updated:** 2026-05-22 (implemented in Luna Engine commit `407122f`; migration meta payload shape verified)
**Affects format version:** v0.3
**Parent spec:** `01_Specs/implemented/SPEC-005_annotation-ledger.md`

---

## Problem statement

SPEC-005 establishes the annotation ledger's chain mechanics (append-only
triggers, SHA-256 hash chain, canonical 10-field serialization, mandated
canonical JSON for payload bytes). It leaves the *structure* of each payload
unspecified beyond "JSON, event-specific."

A chain with sound integrity but unspecified payload protocol is exactly the
failure mode SPEC-005 cannot itself catch. Two independent correct
implementations of the same operation — say, an ambassador upgrade — could
ship payloads `{"claim_id": "...", "node_id": "...", "reason": "..."}` and
`{"claim": "...", "source_node": "...", "justification": "..."}`. Both pass
every check in SPEC-005's `validate_ledger()`: both are valid canonical JSON,
both hash deterministically, both chain correctly. They are mutually
unreadable. The chain is sound; the protocol on top of it is broken.

This companion spec declares the required payload structure per `event_type`,
specifies the unknown-key preservation rule that lets the format evolve
without breaking chains, and defines the build-time validation that catches
non-conforming payloads before they ship.

## Observed evidence

SPEC-005 line 421 says: *"payload TEXT NOT NULL — JSON; event-specific
structure."* That's the entire payload contract in the parent spec.

The ambassador upgrade pseudocode (SPEC-005, "Behavioral changes" section)
demonstrates one payload shape:

```python
upgrade_payload = json.dumps({
    "claim_id": claim_id,
    "node_id": node_id,
    "reason": "ambassador_upgrade",
}, sort_keys=True, separators=(",", ":"))
```

But nothing in SPEC-005 declares those three keys as the contract for
`claim_anchored` events. A second implementation reading "JSON;
event-specific structure" could reasonably ship a different shape.

The eight event types declared in SPEC-005's `annotation_ledger.event_type`
CHECK constraint all need this treatment. The `meta` escape hatch is a
special case (intentionally open-ended for chain meta-events).

## Root cause analysis

SPEC-005 separated concerns correctly — chain mechanics belong in the parent
spec, payload semantics belong here — but the separation left a gap during
the active-spec window. The right move is to fill it in a companion spec
that ships alongside the parent, rather than postpone or roll into the
parent and bloat it.

The deeper issue is that **a chain's integrity is necessary but not
sufficient for governance.** Governance requires a shared semantic protocol;
the chain only guarantees that whatever protocol was used cannot be silently
mutated. This spec establishes the semantic layer.

## Proposed solution

For each `event_type` declared in SPEC-005, this spec declares:

- **Required keys** — must be present in the payload at insert time; build-time
  validation rejects non-conforming payloads.
- **Optional keys** — recognized by readers; absent without error.
- **Value types and reference semantics** — what shape each value takes;
  whether it's a ULID, an integer, a string, etc.
- **Unknown keys** — preserved verbatim by readers (they're part of the
  hashed payload bytes); semantically ignored unless the reader recognizes
  them. This is how new keys are added without breaking existing chains.

### Per-event-type contracts

All ULIDs below are 26-char Crockford Base32 uppercase strings, matching the
SPEC-002 format. All hashes are 64-char hex SHA-256 strings, matching
SPEC-005's `entry_hash` format. Timestamps in payloads are unix-ms integers,
matching `entry_ts`.

#### `claim_anchored`

Ambassador or elder action: a previously `match_failed` or `unknown` claim
gets anchored to one or more source nodes.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `claim_id` | yes | ULID | The extraction being anchored. Matches the event's `target_ulid`. |
| `node_id` | yes | ULID | The doc_node being anchored to. |
| `reason` | yes | string | Short human-readable rationale. Enum-like in practice: `"ambassador_upgrade"`, `"reanchor_corrected"`, `"migration_unclassified_resolved"`, etc. |
| `confidence_note` | optional | string | If the actor wants to flag uncertainty. Free text. |

The event's `target_kind` MUST be `"extractions"` and `target_ulid` MUST
equal `payload.claim_id`. `validate_payloads()` checks this consistency.

#### `claim_disputed`

Elder action: a claim is flagged for reconciliation. Disputes don't change
the claim's `anchor_status` directly; they create a pending reconciliation
that a later `claim_reconciled` event resolves.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `claim_id` | yes | ULID | The disputed extraction. Matches `target_ulid`. |
| `reason` | yes | string | Why disputed. Enum-like: `"contested_anchor"`, `"factual_error"`, `"paraphrase_drift"`, `"attribution_dispute"`. |
| `evidence` | optional | string \| array of strings | Supporting context: source quotes, references to other claims, links to ledger event hashes of related disputes. |
| `severity` | optional | enum | One of `"low"`, `"medium"`, `"high"`, `"critical"` — the enum is locked for v0.3. Hint for reconciliation prioritization. Adding values requires a new spec under the extensibility process; removing values is forbidden because past events have hashed bytes referencing them. |

#### `claim_filtered`

Ambassador or elder action: a claim is intentionally suppressed from primary
reads. Distinct from `anchor_status = 'filtered'` set at build time — this is
post-build filtering with provenance.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `claim_id` | yes | ULID | The filtered extraction. Matches `target_ulid`. |
| `filter_reason` | yes | string | Why filtered. Enum-like: `"frontmatter"`, `"acknowledgment"`, `"attribution_noise"`, `"contested_unresolved"`, `"community_suppressed"`. |
| `recoverable` | optional | boolean | `true` (default) means the filter can be reversed by a later event; `false` means the claim should never be re-surfaced. |

#### `claim_reconciled`

Elder action: resolves a previously-`claim_disputed` event. The resolution
is final unless a later `claim_disputed` re-opens the question.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `dispute_event_hash` | yes | hex string (64 chars) | The `entry_hash` of the `claim_disputed` event being resolved. |
| `resolution` | yes | string | Enum: `"upheld"` (dispute stands, claim is suppressed/contested), `"overturned"` (dispute dismissed, claim restored), `"partial"` (some aspect modified), `"deferred"` (kicked to a higher actor or future review). |
| `rationale` | yes | string | Human-readable explanation. Required even when terse — accountability matters. |
| `partial_aspect` | conditionally required | string | Required iff `resolution = "partial"`; forbidden otherwise. Short freeform string naming which aspect of the dispute was upheld (the structured counterpart to `rationale` for the partial case). Validation enforces both directions. |
| `claim_id` | optional | ULID | Convenience copy of the disputed claim's ULID. If omitted, readers resolve via the dispute_event_hash → claim_id chain. Including it makes single-row queries cheaper. |

The event's `target_kind` MUST be `"annotation_ledger"` and `target_ulid`
MUST equal the `ulid` of the disputed event (NOT its `entry_hash`).
`dispute_event_hash` in the payload is the integrity reference; `target_ulid`
is the indexable pointer.

The `partial_aspect` key is bidirectionally enforced: present iff
`resolution = "partial"`. A `claim_reconciled` event with `resolution = "partial"`
but no `partial_aspect` fails validation, as does an event with
`resolution = "upheld"` (or `"overturned"` or `"deferred"`) that includes a
`partial_aspect` key. This pattern — conditionally required keys — is a
sanctioned shape that new event types may reuse (see "Adding new event
types" below).

#### `summary_overridden`

Elder action: replace or supplement a builder-generated summary at any node
level.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `target_field` | yes | enum | Locked vocabulary for v0.3: `{"content"}`. Which field on the target extraction is being overridden. Future format versions adding structured summary fields (e.g., `"title"`, `"key_points"`, `"dissent"`) extend this vocabulary in their own spec; v0.3 cartridges may only emit `"content"`. |
| `prior_value` | yes | string | The text being replaced. Allows external verification that the override is what the actor intended. |
| `new_value` | yes | string | The replacement text. Empty string is permitted (means "remove this summary"). |
| `rationale` | yes | string | Why overridden. |
| `target_extraction_id` | optional | ULID | If `target_kind`/`target_ulid` on the event row point at a doc_node, this disambiguates which summary extraction under that node is being overridden. Required when the doc_node has multiple summaries. |

The event's `target_kind` is `"extractions"` (single summary override) or
`"doc_nodes"` (override defined at the node level, applies to a specific
summary identified by `target_extraction_id`).

#### `cartridge_reviewed`

Oracle action: top-level review of the cartridge as a whole. Different from
per-claim events because it doesn't modify any row's `anchor_status`; it
records an opinion about the cartridge as an artifact.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `decision` | yes | string | Enum: `"approved"`, `"approved_with_notes"`, `"rejected"`, `"deferred"`. |
| `summary` | yes | string | Human-readable summary of the review. |
| `review_criteria` | optional | array of strings | Which criteria were applied. E.g., `["anchor_completeness", "source_authority", "synthesis_quality"]`. |
| `score` | optional | number (0.0–1.0) | If the oracle uses a numeric rubric. Interpretation is community-specific; nothing in the format treats this as authoritative. |
| `target_cartridge_ulid` | conditionally required | ULID | Required if this is a review of *another* cartridge (cross-cartridge review per SPEC-005); omitted for a review of the current cartridge. The event row's `target_cartridge_ulid` column must match. |

#### `cartridge_imported`

System or owner action: records that another cartridge has been imported,
referenced, or attached. The v0.2 -> v0.3 migration is not represented as
`cartridge_imported`; it is a system `meta` event because the system actor is
reserved for chain meta-events. `cartridge_imported` remains the payload shape
for later cross-cartridge incorporation.

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `source_application_id` | yes | hex string | The imported cartridge's `PRAGMA application_id`, e.g., `"0x4C554E43"`. |
| `source_source_hash` | yes | hex string (64 chars) | The imported cartridge's `meta.source_hash` (SHA-256 of the original source file). |
| `source_user_version` | yes | integer | The imported cartridge's `PRAGMA user_version`. |
| `source_format_version` | optional | string | The imported cartridge's `meta.format_version` (semver string). |
| `source_cartridge_ulid` | optional | ULID | If the imported cartridge has its own cartridge-level ULID (a future spec may add `meta.cartridge_ulid`), record it here. Currently optional because v0.3 doesn't yet define a cartridge-level ULID. |
| `relationship` | yes | enum | One of `"merged"`, `"attached"`, `"referenced"`, `"migrated_from"`. Always present — no default-elision allowed, because writers omitting the default would produce different payload bytes than writers including it, breaking hash determinism. The ~30 bytes per event saved by elision are not worth the determinism cost. |

The event row's `target_cartridge_ulid` (if set) MUST equal
`payload.source_cartridge_ulid` (when both are present).

#### `meta`

Escape hatch for chain-internal events that aren't governance actions. No
required keys by default; the genesis row is the one mandatory use case and
has its own required shape:

**Genesis row (subset of `meta`)** — required keys when `seq = 1`:

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `application_id` | yes | hex string | The cartridge's `PRAGMA application_id`. |
| `user_version` | yes | integer | The cartridge's `PRAGMA user_version` at genesis. |
| `source_hash` | yes | hex string (64 chars) | The cartridge's `meta.source_hash`. |
| `format_version` | yes | string | The cartridge's `meta.format_version` (semver string). |

Other `meta` event uses (e.g., recording an algorithm switch in a future
v0.4+ cartridge, or chain-internal bookkeeping) define their own payload
shapes per-occurrence. Build-time validation rejects `meta` events at
`seq > 1` with payloads that fail to round-trip through canonical JSON,
but does not enforce required keys beyond the genesis case.

### Unknown-key preservation rule

Readers MUST preserve unknown keys verbatim because the payload bytes are
hashed into the chain. Any modification — even adding an unrecognized key
to a re-serialized copy — breaks the chain integrity check on the modified
row and every downstream row.

In practice this means: readers that consume a payload to display or query
it MUST NOT re-serialize the payload back to storage. If a derived structure
is needed (e.g., for indexing), build it as a separate computed value;
never overwrite the original `annotation_ledger.payload` cell. The
append-only triggers already prevent the latter at the database layer; the
rule is documented here for application-layer consumers too.

Readers MAY ignore unknown keys semantically. A v0.3 reader encountering a
`claim_anchored` payload with an unknown `confidence_note` key (added in a
future spec) hashes it correctly, stores it correctly, and may simply not
display it. Forward-compatibility comes for free as long as the rule holds.

### Behavioral changes

**Builder (`src/luna/cartridge/builder.py`):**

1. Genesis row payload uses the required shape spelled out above. The
   ledger module exports a `build_genesis_payload(meta_dict)` helper that
   produces canonical JSON from the cartridge's `meta` table.
2. Any builder call that inserts a non-`meta` event constructs its payload
   from a typed dict matching the per-event-type contract. Builder type
   hints carry the contract for IDE / type-check time.

**New module (`src/luna/cartridge/payload_schemas.py`):**

Declares one dataclass / TypedDict per event type encoding the required
keys, the optional keys, and the per-event consistency rules (e.g.,
`claim_anchored.claim_id == event.target_ulid`). The
`validate_payloads(conn)` function walks the ledger, parses each payload,
and checks it against its declared schema. Called from the centralized
`validate_ledger()` as Step 7 (after the chain integrity walk).

```python
# Sketch — actual module belongs in src/luna/cartridge/, not the spec.
EVENT_SCHEMAS = {
    "claim_anchored": {
        "required": {"claim_id", "node_id", "reason"},
        "optional": {"confidence_note"},
        "consistency": [
            lambda payload, event: payload["claim_id"] == event.target_ulid,
            lambda payload, event: event.target_kind == "extractions",
        ],
    },
    "claim_disputed": {
        "required": {"claim_id", "reason"},
        "optional": {"evidence", "severity"},
        "consistency": [
            lambda payload, event: payload["claim_id"] == event.target_ulid,
            lambda payload, event: event.target_kind == "extractions",
        ],
    },
    # ... one entry per event_type ...
}

def validate_payloads(conn):
    """Per-event-type payload schema validation. Called from validate_ledger()."""
    rows = conn.execute("""
        SELECT seq, event_type, target_kind, target_ulid, target_cartridge_ulid, payload
        FROM annotation_ledger ORDER BY seq
    """).fetchall()
    for seq, etype, tkind, tulid, tcart, payload_bytes in rows:
        schema = EVENT_SCHEMAS.get(etype)
        if schema is None:
            # Unrecognized event_type would have been rejected by the CHECK
            # at insert time; reaching here means the CHECK constraint has
            # been bypassed (tampering).
            raise BuildError(
                f"seq={seq}: unknown event_type={etype!r}; CHECK constraint "
                f"may have been bypassed"
            )
        payload = json.loads(payload_bytes)
        missing = schema["required"] - set(payload.keys())
        if missing:
            raise BuildError(
                f"seq={seq}: event_type={etype!r} missing required keys: {missing}"
            )
        event_proxy = SimpleNamespace(
            target_kind=tkind, target_ulid=tulid, target_cartridge_ulid=tcart,
        )
        for check in schema.get("consistency", []):
            if not check(payload, event_proxy):
                raise BuildError(
                    f"seq={seq}: event_type={etype!r} fails consistency check"
                )
```

**Reader:** consumes payloads through accessors keyed by event_type that
know the schema. Unknown keys flow through to the consumer untouched; the
reader makes no assertion that the consumer will use them.

### Migration path

This spec ships with SPEC-005 in v0.3. The migration tool that creates the
ledger for v0.2 → v0.3 cartridges populates the genesis row with the
required shape declared above. No pre-existing payloads exist to migrate
(v0.2 has no ledger); migration generates the first event under the v0.3
contract directly.

If a future v0.4 spec adds new event types or new required keys, the
migration tool for v0.3 → v0.4 may need to backfill rows or reject
cartridges that can't be upgraded cleanly. That's a future concern; v0.3
ships with the eight event types here.

## Validation rules

Build time, runs as Step 7 of the centralized `validate_ledger()` (after
Step 0 trigger DDL check, Step 1 genesis well-formedness, Step 2 hash chain
walk including payload canonical re-serialization, Step 3 head pointer
check, Step 4 target resolution, Step 5 actor aggregation, Step 6 meta
algorithm key check — all in SPEC-005). The Step 7 check is `validate_payloads()`
above.

The full validation sequence rejects a build if any of the following hold:

- An event's payload is missing a required key for its declared `event_type`.
- An event's payload contains key/value pairs that fail a per-event-type
  consistency rule (e.g., `claim_anchored.claim_id != event.target_ulid`).
- An event's `event_type` is not in `EVENT_SCHEMAS` (this also catches
  CHECK-constraint bypass attempts).
- The genesis row's payload is missing any of `{application_id, user_version,
  source_hash, format_version}`.

Read time:

- `lun fsck --payloads` runs `validate_payloads()` against the ledger.
  Independent of `--full-chain` because the payload check is cheap relative
  to the hash recompute and can run on its own.
- The fast-open path (every `validate_cartridge_open()`) does NOT run
  payload validation by default. Per-event validation is O(n) and the
  build-time check already guarantees conformance for any cartridge that
  came out of a legitimate builder. Payload validation on open is for
  detecting post-build mutation, which the chain integrity check catches
  more comprehensively.

## Governance implications

This spec converts SPEC-005's chain integrity into a *protocol* — a shared
contract about what events mean, not just that they cannot be silently
modified. Without this layer, two communities running independent Luna
deployments could not interoperate on each other's cartridges: their
ambassadors' upgrades would produce semantically incompatible payloads.

The unknown-key preservation rule is the format's forward-compatibility
mechanism for payloads. New keys can be added by future specs without
breaking existing chains; old readers ignore them, new readers recognize
them, and the chain hashes remain valid throughout. This is the same
property `ALTER TABLE ADD COLUMN` gives at the schema layer (per
`05_Reference/SQLite_Research.md` Topic 5), extended into the payload
layer.

The `target_cartridge_ulid` interactions (SPEC-005 Q1) become operational
through `cartridge_imported` and `cartridge_reviewed` payloads. A future
cross-cartridge governance spec defines how those events propagate across
cartridge boundaries; this spec just establishes the payload structure that
makes such propagation unambiguous.

## Adding new event types

New event types beyond the eight declared above are added through a single
process. This section governs the format's taxonomy growth and is the
authoritative reference for any future spec that proposes one.

A spec proposing a new event type MUST:

1. **Ship in its own spec or as an amendment to this one** — never inline
   in another spec. The taxonomy stays centralized in this document so
   the per-event-type contracts table remains the single source of truth.
2. **Declare a payload contract** — a new entry in the per-event-type
   contracts table above, with required keys, optional keys, value types,
   and consistency rules.
3. **Include behavioral pseudocode** for at least one writer (the
   ambassador upgrade flow in SPEC-005 is the reference shape).
4. **Ship the `CHECK` constraint migration** for
   `annotation_ledger.event_type` as part of the same spec, scoped to the
   format version that introduces the new event type.
5. **State the format version** in which the new event type is first
   valid — v0.4+ event types cannot appear in v0.3 cartridges. Mixed-version
   reads use the unknown-key preservation rule for any unrecognized
   payload keys, but unknown `event_type` values themselves are rejected
   at the CHECK layer.

The unknown-key preservation rule above means *new keys* on existing event
types do NOT require a new event type — they're additive and forward-compat
by construction. Only new *event types* (new entries in the CHECK enum)
require this process.

### Sub-cases of the extensibility process

**Enum extension** (e.g., adding a fifth `severity` value to
`claim_disputed`): a small case of the full process. Still requires a spec
entry updating the per-event-type table, still ships with a CHECK migration
if the enum is enforced at the database layer (most enums in this spec are
application-layer only and live in `payload_schemas.py`), still requires a
format-version statement. **Removing** an enum value is forbidden — past
events have hashed bytes referencing the removed value, and the chain
integrity check would still need to recognize it.

**Vocabulary extension** (e.g., adding `"title"` to `target_field`): same
shape as enum extension. The vocabulary is application-layer; the schema
entry in this spec is the authoritative source. New vocabulary values
ship in the spec that introduces the underlying capability (e.g., a future
structured-summary spec ships the `"title"` extension alongside the
`title` field on extractions).

**Conditionally required keys** (e.g., `partial_aspect` on
`claim_reconciled` when `resolution = "partial"`): a sanctioned pattern
new event types may reuse. Document the conditional rule in prose
alongside the per-event-type table, and encode it as a `consistency`
lambda in `EVENT_SCHEMAS`. The validation enforces both directions:
required-when-condition-holds AND forbidden-when-condition-doesn't.

## Alternatives considered

**Alt 1: Roll payload schemas into SPEC-005 itself rather than a companion.**
Rejected. SPEC-005 already covers chain mechanics, actor model, migration,
governance framing, and 10 alternatives over its own design. Adding eight
per-event-type schema tables and a validation module on top would bloat
the parent spec past the point of useful review. The companion split lets
each spec focus on one layer.

**Alt 2: Use JSON Schema (`https://json-schema.org/`) as the formal
contract, with `.json` files per event type in the repo.**
Rejected for v0.3. JSON Schema is appropriate for protocol contracts that
span organizations and need machine-readable cross-language validators.
For v0.3, where Luna is the only implementation and the schemas are stable
enough to fit in this document, inline tables are more readable and the
dataclass / TypedDict approach in `payload_schemas.py` gives Python-side
type safety without the JSON Schema toolchain dependency. Reconsider when
a second implementation (browser viewer, third-party tool) needs to
validate payloads independently.

**Alt 3: Versioned payload schemas (e.g., `claim_anchored_v1` vs
`claim_anchored_v2`) so payload shape can change without forward-compat
constraints.**
Rejected. Splits one logical event type into N variants, every reader has
to handle every version, the `event_type` CHECK enum grows linearly with
versions. The unknown-key preservation rule handles forward-compatibility
without versioning, and the algorithm-immutability rule from SPEC-005 Q4
applies the same logic to event_type names: once accepted, they don't
break compatibility for the cartridges that use them.

**Alt 4: Open-ended `meta` event payloads with no required keys at all
(including genesis).**
Rejected. The genesis row is too important to leave ungoverned — every
external verifier wants to know what cartridge they're verifying, and the
provenance triple is the answer. Required keys on genesis don't constrain
the `meta` escape hatch for other uses; the validation only enforces the
genesis shape when `seq = 1`.

**Alt 5: Per-event-type payload TABLE (e.g., `event_payload_claim_anchored`
with typed columns) instead of JSON in a single column.**
Rejected. Multiplies the schema by event-type count, fragments the chain
across N tables (every JOIN is a per-event-type lookup), and breaks the
"one chain, one table" simplicity that makes SPEC-005's integrity check
tractable. The JSON-payload-with-schema-validation approach is the
standard pattern for typed events in a single log; reconsider only if
specific event types accumulate so much structured data that single-column
JSON becomes a performance problem (unlikely at the scale of governance
events).

**Alt 6: Don't validate payload structure at all; treat payload as opaque
bytes and rely on convention.**
Rejected. This is the failure mode the spec exists to prevent. Without
build-time enforcement, two correct implementations can ship mutually
unreadable payloads and the protocol breaks silently across deployments.

## Open questions

None remaining. Q6 resolved during drafting; Q1–Q5 resolved as part of
moving this spec from `active/` to `accepted/`; implementation landed on
2026-05-22 in commit `407122f`:

1. **`severity` enum locked to `{"low", "medium", "high", "critical"}`.**
   The exact four values are conventional but not load-bearing; what
   matters is locking them so payloads from different writers hash
   consistently. Adding a fifth value follows the extensibility process
   above; removing a value is forbidden because past events have hashed
   bytes referencing them.

2. **`partial_aspect` is conditionally required** when
   `claim_reconciled.resolution = "partial"`, forbidden otherwise.
   Resolved by adding the key to the `claim_reconciled` contract with
   bidirectional validation. The unconditional `rationale` continues to
   carry narrative context; `partial_aspect` adds structured detail
   specifically for the partial case so governance UIs can render it
   without parsing rationale text.

3. **`target_field` vocabulary locked to `{"content"}` for v0.3.** Future
   format versions adding structured summary fields (`title`,
   `key_points`, `dissent`, etc.) extend the vocabulary in their own
   spec via the vocabulary-extension sub-case above. Restrictive now,
   extensible later, never broken — matches the working principle of
   schema additions, not changes.

4. **`cartridge_imported.relationship` is always required, no
   default-elision.** Writers omitting the default would produce
   different payload bytes than writers including it, breaking hash
   determinism for semantically identical events. The ~30 bytes per
   event saved by elision are not worth the determinism cost.

5. **`event_type` extensibility process documented in this spec**, in
   the "Adding new event types" section above. README's spec-lifecycle
   governs the `active → accepted → implemented` workflow at the
   meta-spec layer; format-taxonomy growth is a format-layer concern
   that belongs alongside the per-event-type contracts it extends.

6. **Cross-event references via `dispute_event_hash` — resolved during
   drafting** by amending SPEC-005's `validate_ledger()` Step 4 set to
   include `"annotation_ledger"`. `target_kind` is unconstrained at the
   database layer (no SQLite CHECK on the column); the gap was
   application-layer. Resolution lives in SPEC-005's `validate_ledger()`
   definition; no CHECK constraint migration was needed.

## Dependencies

Upstream:

- **SPEC-005 (implemented, this spec's parent)** — provides the
  `annotation_ledger` table, the `event_type` CHECK enum, the canonical
  serialization, the `target_kind` / `target_ulid` / `target_cartridge_ulid`
  columns. This spec's contracts plug into those primitives.
- **SPEC-002 (implemented)** — ULID identity. Every `claim_id`, `node_id`,
  `dispute_event_hash` reference in payloads relies on the ULID format
  from SPEC-002.
- **SPEC-006 (implemented)** — the provenance triple `(application_id,
  user_version, source_hash)` that genesis payloads carry comes directly
  from SPEC-006's contract.

Blocks:

- **SPEC-005 implementation** — this companion must be implemented with
  SPEC-005 so ledger payloads are protocol-compatible across writers.
  Without the payload schemas, SPEC-005 ships an under-specified protocol.

Does not block, but interacts with:

- **SPEC-004 (implemented, 2026-05-22; multi-axis weights)** — reads `claim_anchored`,
  `claim_disputed`, `claim_reconciled` events to weight trust signals by
  actor and event outcome. The required-key contracts here define what
  SPEC-004 can rely on.
- **Cross-cartridge governance spec (planned, unnumbered)** — uses
  `cartridge_imported` and `cartridge_reviewed` payloads as the wire
  format for cross-cartridge interactions.

## Implementation notes

- Commit/PR reference: Luna Engine commit `407122f` (`feat(cartridge): SPEC-005 v0.3 schema + annotation ledger + lun fsck`)
- Implementation date: 2026-05-22
- Verification: 81 SPEC-005 tests passed in 0.81s; payload validation passed during `lun fsck --payloads` and migration-tail validation.
- Deviations from pre-implementation draft: v0.2 -> v0.3 migration uses an action-shaped `meta` payload (`migrated_v2_to_v3`) instead of a `cartridge_imported` payload.
- Follow-up issues created: no tracker items in this docs-only closeout; next separate briefs are ambassador-upgrade ledger wiring, ReaderPrototype v0.3 support, SPEC-004 composer integration, and the Meditations v0.3 audit.
