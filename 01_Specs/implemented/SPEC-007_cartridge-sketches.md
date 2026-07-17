# SPEC-007: Cartridge sketches (bloom filters baked into the cartridge)

**Status:** implemented (2026-05-23; engine commit `c20673d` + reader v0.3.2 consumer; coupled with v0.3 format spec sketches DDL amendment)
**Severity:** medium
**Author:** Ahab (with Claude)
**Created:** 2026-05-22
**Last updated:** 2026-05-24
**Affects format version:** v0.3 (additive; v0.2 cartridges remain valid without sketches)

---

## Problem statement

`.lun` cartridges are designed for portability — a single SQLite file moves between hosts, gets shared, and stacks alongside other cartridges. The natural endpoint is the **shelf**: a consumer holding N cartridges and asking shelf-scoped questions like *"which of my 50 cartridges mention the term 'virtue'?"*, *"do any of these cartridges already contain the extraction with ULID `01HQ3K…`?"*, or *"which cartridges talk about Marcus Aurelius?"* The reader prototype's v2 design ([`06_Prototypes/ReaderPrototype/SPEC.md`](../../06_Prototypes/ReaderPrototype/SPEC.md) § Open hooks for next slices) and SPEC-005's cross-cartridge promotion flow ([`01_Specs/implemented/SPEC-005_annotation-ledger.md`](SPEC-005_annotation-ledger.md) § Governance implications → "Cross-cartridge governance") both assume this shape — multiple cartridges queried together — without specifying how a consumer answers a membership question without opening every file.

The naive answer scales linearly: open every cartridge, attach the FTS5 index, run the query, close. For Meditations-scale cartridges (2.5 MiB, ~3,800 nodes, ~1,100 extractions) that is roughly 50 ms of cold open per file. Fifty cartridges is 2.5 seconds of latency for a single shelf query and at least 50 file-system file handles, every time. The cost compounds when the question is *"do any of these 5 corpora mention any of these 100 terms?"* — Cartesian product over N cartridges and M terms, all paid in full open-and-scan even when 95% of the cartridges have nothing to say.

This spec adds a small, optional, self-contained **sketch layer** to the cartridge format: append-only `BLOB`s containing bloom filters keyed by named sketch kinds (extraction ULIDs, entity surface forms, FTS5 term vocabulary), baked into the cartridge at build time and read in bulk at shelf-open time. Shelf consumers ask the sketches first, get a list of *candidates*, then open only the candidates to verify. The format gains a probabilistic-pre-filter; the v0.2 contract is untouched.

## Observed evidence

- **Reader v1 SPEC v2 hook (filed 2026-05-22).** The reader prototype's [SPEC](../../06_Prototypes/ReaderPrototype/SPEC.md) § Open hooks for next slices lists *"Bloom-filter cartridge sketches (multi-cartridge-shelf consumer)"* explicitly: once the shelf lands, opening every cartridge to answer "which of my 50 cartridges contains 'virtue'?" does not scale. The reader entry calls out a few KB per cartridge and recommends baking the filter into `meta` as part of a v0.3 builder change.
- **SPEC-005 cross-cartridge surface.** The [annotation ledger](SPEC-005_annotation-ledger.md) defines `target_cartridge_ulid` on `annotation_ledger` (column declaration inside the `CREATE TABLE annotation_ledger` block at lines 103–135; specifically line 125) as the cross-cartridge identity surface for events. The v0.3 format spec separately reserves [`nexus_refs`](../../03_Format_Spec/LUN-FORMAT_v0.3.md) (`CREATE TABLE nexus_refs` at `LUN-FORMAT_v0.3.md:387`; originally placeholdered in `LUN-FORMAT_v0.2.md:404` per SPEC-005's cross-cartridge governance discussion) as the cross-cartridge promotion table. The promotion flow — *"is this local node already in the Nexus?"*, *"does another cartridge already carry this extraction ULID?"* — is a membership question by construction. Neither SPEC-005 nor the format spec specifies how a promoter answers it without scanning. SPEC-007 fills that gap on the producer side; SPEC-005 stays the consumer.
- **Meditations reference cartridge** ([`07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun`](../../07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun), built against the [`AUDIT_2026-05-22_meditations-v02`](../../04_Audits/AUDIT_2026-05-22_meditations-v02.md) baseline; non-ledger row counts confirmed unchanged in the v0.3 audit at [`AUDIT_2026-05-22_meditations-v03`](../../04_Audits/AUDIT_2026-05-22_meditations-v03.md)) ships 1,106 extractions (512 claims + 532 entities + 62 summaries), 532 distinct entity surface forms, and an FTS5 vocabulary on the order of a few thousand terms. The exact sizing exercise for v0.3 sketches is performed against this corpus in § 7.4 below.
- **The shelf is the obvious next reader surface.** The reader SPEC § Open hooks for next slices names *"Multi-cartridge shelf"* explicitly. Until sketches exist, the shelf either pays the full-scan latency above or it builds an out-of-band catalog index that lives outside the cartridge and breaks the file-is-source-of-truth principle ([`08_Journal/2026-05-10.md`](../../08_Journal/2026-05-10.md) records that principle as load-bearing for the family).

## Root cause analysis

The format has no answer to *"could this cartridge contain X?"* short of *"open it and look."* That works in single-cartridge mode (which is all the reader v1 supports) and breaks the moment any consumer holds more than a handful of cartridges. There are two possible architectural moves:

1. **Off-cartridge catalog index.** A shelf-side database catalogs `(cartridge_ulid, sketch)` pairs separately from the cartridges themselves. The shelf consults the catalog before opening any cartridge.
2. **Sketches baked into the cartridge.** The cartridge ships with its own self-describing membership filter. The shelf reads all N sketches into memory at shelf-open and queries them locally.

Option (1) is operationally fine for one consumer but breaks portability — when a cartridge moves to a new host, its catalog membership is lost. Two consumers cannot share a shelf without sharing or rebuilding the catalog. SPEC-005's cross-cartridge promotion flow is meant to work *across hosts and roles* (an ambassador receiving cartridges from elsewhere needs to answer membership questions without first being handed an opaque sidecar index). Option (1) breaks the file-is-source-of-truth principle and is therefore not viable for the format.

Option (2) keeps every cartridge self-describing. A receiver gets the sketch for free; a sender does not have to ship anything extra. The cost is a small storage overhead per cartridge (target: ≤ 1% of cartridge size for the default sketch family, ~17 KiB for Meditations — see § 7.4 for the worked example) and a build-time pass over the data the builder already has. Bloom filters are the textbook data structure for this — sub-linear membership queries, no false negatives, tunable false-positive rate, fixed memory, trivially serializable — and they are well-understood enough that any consumer can implement reading them from the wire format in fifty lines of code.

The downstream design question is which sketch kinds to specify, how to parameterize them, and how to keep the format additive (so v0.2 cartridges and consumers unaware of sketches keep working unchanged).

## Proposed solution

### 7.1 — The sketches table (v0.3 schema addition)

A new optional table `sketches` is added at v0.3. Builders SHOULD populate it; readers MUST tolerate its absence.

```sql
CREATE TABLE sketches (
    sketch_kind     TEXT NOT NULL,          -- enum below; identifies what was inserted
    sketch_version  INTEGER NOT NULL,       -- per-kind schema version; starts at 1
    hash_family     TEXT NOT NULL,          -- 'murmur3_x64_128' in v0.3 (only allowed value)
    num_hashes      INTEGER NOT NULL,       -- k (number of hash functions); 1 ≤ k ≤ 32
    num_bits        INTEGER NOT NULL,       -- m (bit array length); MUST be a multiple of 8
    num_inserted    INTEGER NOT NULL,       -- n (count of distinct items inserted); 0 ≤ n
    seed            INTEGER NOT NULL,       -- hash seed (uint64); use 0 for deterministic builds
    bitset          BLOB NOT NULL,          -- ceil(num_bits/8) bytes; little-endian bit order
    built_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    builder_version TEXT,                   -- optional; provenance string e.g. 'luna-builder/0.3.0'
    notes           TEXT,                   -- optional; free-text human-readable summary
    PRIMARY KEY (sketch_kind, sketch_version)
);

-- Index for shelf consumers that look up by kind without specifying version:
CREATE INDEX idx_sketches_kind ON sketches(sketch_kind);
```

The `sketches` table is created unconditionally by the v0.3 builder (empty if the builder declines to populate it). Older v0.2 cartridges have no `sketches` table at all; v0.3 readers MUST handle that case — see § 7.5 (Migration path).

#### 7.1.1 — Sketch kinds (v0.3)

| `sketch_kind` | Source vocabulary | Item normalization | Primary consumer |
|---|---|---|---|
| `extraction_ulid` | `extractions.ulid` (all rows, regardless of `extractions.type`) | raw ULID string (26 chars, Crockford base32, uppercase) | SPEC-005 promotion flow ("does any cartridge on this shelf already contain ULID X?") |
| `entity_surface` | Distinct values of `extractions.content` where `extractions.type = 'entity'` | NFKC-normalized; case-folded with Unicode default casing; trimmed of leading/trailing whitespace | Shelf entity-mention queries ("which cartridges mention Marcus Aurelius?") |
| `fts_term` | The FTS5 index vocabulary over `doc_nodes.content` (i.e. the set of tokens that FTS5 would tokenize from any node content) | The FTS5 tokenizer output (porter unicode61, matching builder config). Implicitly parameterized by `meta.fts_tokenizer_config` per § 7.2.5; consumers MUST check that meta value before trusting the sketch. | Shelf full-text pre-filter ("which cartridges contain the term 'virtue'?") |
| `node_ulid` | `doc_nodes.ulid` (all rows) | raw ULID string | Cross-cartridge node-identity checks; SPEC-005 `nexus_refs` consumer |

A v0.3 builder MAY populate any subset of the four kinds. Each kind is independent — missing kinds carry no penalty and no implicit semantics. Shelf consumers that need a kind the builder did not populate fall back to the full-scan path for that query.

The `sketch_version` integer is per-`sketch_kind` and starts at `1`. It exists so that future versions of this spec can amend the item-normalization rules (e.g., switching the FTS5 tokenizer) without breaking older sketches — a consumer reads the version, picks the matching normalization, and ignores rows whose version it does not know. The v0.3 builder always writes `sketch_version = 1`.

#### 7.1.2 — Hash family and seed

The only allowed value for `hash_family` in v0.3 is `'murmur3_x64_128'` — Austin Appleby's MurmurHash3 128-bit variant on x86_64. The 128-bit output is split such that `h1` is the **low 64 bits** (bytes 0–7 in MurmurHash3's standard little-endian output) and `h2` is the **high 64 bits** (bytes 8–15). All consumers MUST agree on this split. The `k` hashes used to set bits are derived by Kirsch–Mitzenmacher double-hashing:

```
for i in 0..k:
    bit_index = (h1 + i * h2) mod num_bits
```

This is the standard library implementation across most bloom-filter packages and is deterministic given `(seed, item_bytes, num_bits, k)`. Other hash families MAY be added in future SPEC-007 amendments; consumers MUST reject sketches whose `hash_family` is unknown rather than guess.

**Integer-width discipline (reimplementer hazard).** The Kirsch–Mitzenmacher formula `(h1 + i*h2) mod num_bits` is arithmetic on uint64 values that can overflow when computed in a fixed-width 64-bit integer type. For any `i ≥ 1` where `h2` has its high bit set (~half of all hash values), `i * h2` exceeds 2^64 and wrapping multiplication produces a value distinct from the mathematical `(h1 + i*h2) mod num_bits`. The two implementations of this spec verified byte-for-byte agreement only after promoting the intermediate computation to a 128-bit type (or arbitrary-precision integers in languages that have them by default). Implementers in fixed-width languages MUST perform the multiply-and-add in at least uint128 (or equivalent wider type) before the modulo. Languages with arbitrary-precision integers (Python, Ruby, Erlang) avoid the hazard automatically. The defensive `0 ≤ bit_index < num_bits` check in § 7.3.1 step 4 catches the symptom but does not substitute for getting the arithmetic right — a sketch built with overflowing arithmetic and queried with overflowing arithmetic will be self-consistent but produce false-negatives against a correctly-built sketch (see [`08_Journal/2026-05-23.md`](../../08_Journal/2026-05-23.md) entry #6 for the case that motivated this clarification).

The `seed` is a uint64 used as MurmurHash3's seed parameter. Builds aiming for byte-identical determinism between runs use `seed = 0`. Builds that want unpredictable seeds (to defend against adversarial input crafted to collide) MAY use a random seed. Either is SPEC-007-compliant.

#### 7.1.3 — Bitset encoding

The `bitset` BLOB is exactly `ceil(num_bits / 8)` bytes long. Bit `i` is stored at byte `floor(i / 8)`, bit position `i mod 8` within that byte, **least-significant-bit-first**. So bit 0 is the LSB of byte 0, bit 7 is the MSB of byte 0, bit 8 is the LSB of byte 1, and so on. This matches the convention used by [BitVec](https://crates.io/crates/bitvec) `Lsb0` order and Python's `bitarray` default.

Validators MUST reject sketches where `length(bitset) ≠ ceil(num_bits / 8)`.

### 7.2 — Builder behavior

A v0.3 builder, when populating sketches, performs the following at the end of the build pipeline (after `extractions` and `doc_nodes` are stable but before the final `VACUUM`):

1. **Compute target parameters per kind.** Given the count of distinct items `n` for the kind and the desired false-positive rate `p` (default `p = 0.01`), compute:
   ```
   m = ceil(-n * ln(p) / (ln(2)^2))
   m = ((m + 7) // 8) * 8                          # round UP to nearest multiple of 8
   k = max(1, min(32, int(round(m / n * ln(2)))))  # round half-to-even (Python round() default)
   ```
   The `round()` call uses banker's rounding (round half to even) to match Python's default and most standard library implementations; this matters only at the integer boundary and is fixed here so independent implementations agree byte-for-byte. If `n = 0`, the builder MUST skip the kind (no zero-item sketches).

2. **Allocate the bitset.** Zero-initialized `ceil(m / 8)` bytes.

3. **Insert each item.** Stream the source vocabulary once, normalize per § 7.1.1, hash with the chosen `seed` and `hash_family`, set `k` bits per item. Track the count of distinct items actually inserted as `num_inserted` (which equals `n` when the source vocabulary has no duplicates after normalization).

4. **Insert one row per populated kind.** `(sketch_kind, sketch_version=1, hash_family, num_hashes=k, num_bits=m, num_inserted, seed, bitset, built_at=now, builder_version, notes=NULL)`.

5. **Stamp `meta`.** Write `meta.sketches_present = '<comma-separated kinds in alphabetical order>'` — e.g., `'entity_surface,extraction_ulid,fts_term'`. Consumers use `meta.sketches_present` as a fast pre-check before joining `sketches`. Builders that populate no sketches MUST omit `meta.sketches_present` entirely (its absence means "no sketches" for the cartridge).

The builder MAY skip step 5 only if it also writes zero sketches; populating sketches without stamping `meta` is invalid.

#### 7.2.1 — Default false-positive rate

The default `p = 0.01` (1% false-positive rate) is chosen to keep the sketch small while remaining useful: a shelf of 50 cartridges with `p = 0.01` returns a candidate list of ~0.5 false positives per query on top of any true matches, which the verify-by-opening pass eliminates trivially. Builders MAY choose a different `p` per kind (e.g., `p = 0.001` for `extraction_ulid` to make promotion-flow membership checks tighter) and MUST record their choice implicitly via the resulting `(num_bits, num_hashes, num_inserted)` triple — readers compute the actual `p` from these values rather than relying on a stored target.

#### 7.2.5 — Tokenizer-config provenance (`fts_term` kind only)

A builder that writes the `fts_term` sketch MUST also write a `meta.fts_tokenizer_config` value identifying the FTS5 tokenizer configuration used to produce it. The v0.3 Luna builder uses `unicode61` and SHOULD write `meta.fts_tokenizer_config = 'unicode61'`. Future SPEC-007 amendments expand the allowed vocabulary as the builder gains tokenizer options (e.g., `'unicode61_porter'`, `'unicode61_remove_diacritics_2'`).

A consumer querying an `fts_term` sketch MUST read `meta.fts_tokenizer_config` first and tokenize the query term with the same tokenizer config before computing the bloom-filter membership check. A consumer that does not recognize the value MUST treat the sketch as unusable and fall back to the full-scan path. The sketch is implicitly parameterized by this meta value; there is no per-sketch column for tokenizer config.

`meta.fts_tokenizer_config` is REQUIRED iff a `fts_term` sketch is present; it is OPTIONAL (and SHOULD be omitted) when no `fts_term` sketch is written. Validators MUST flag the inconsistency.

### 7.3 — Reader behavior

#### 7.3.1 — v0.3 reader

A v0.3 reader (or any consumer aware of SPEC-007) opening a cartridge:

1. Checks `meta.sketches_present`. If absent, treats the cartridge as having no sketches and proceeds to full-scan paths for any shelf query.
2. If `meta.sketches_present` is non-empty, the reader MAY load any subset of sketches it cares about by selecting from `sketches` filtered by `sketch_kind`. Loading is lazy — there is no requirement to load sketches at all.
3. For each loaded sketch row, the reader MUST validate:
   - `hash_family` is in its set of supported families.
   - `length(bitset) = ceil(num_bits / 8)`.
   - `num_hashes ∈ [1, 32]`.
   - `num_inserted ≥ 0` and `num_inserted ≤ num_bits` (sanity; not a hard upper bound on bloom filter math but catches obviously corrupted rows).
   A failed validation makes the sketch unusable; the reader logs and falls back to full-scan for queries that would have used it. Validation failure of a sketch MUST NOT cause the cartridge open to fail — the rest of the cartridge is independent.
4. Querying a sketch for membership of an item:
   - Normalize the item per § 7.1.1 for that kind.
   - Compute the same Kirsch–Mitzenmacher bit indices from `(item, seed, hash_family, num_hashes, num_bits)`.
   - Implementers MUST validate `0 ≤ bit_index < num_bits` before any bitset access. The `mod num_bits` step guarantees this mathematically, but a malformed sketch with `num_bits = 0` or an integer-overflow bug in the modulo step could violate it. The defensive check is cheap; a computed `bit_index` outside the range MUST be treated as sketch malformation and the sketch SHOULD be marked unusable per step 3.
   - If any of the `k` bits is `0`, the item is **definitely not in the cartridge**.
   - If all `k` bits are `1`, the item is **probably in the cartridge** — verification by opening the actual table is REQUIRED before any consumer-facing claim about presence.

#### 7.3.2 — Pre-SPEC-007 reader

A reader unaware of SPEC-007 reading a v0.3 cartridge sees the `sketches` table as a noise table and ignores it. SQLite's per-table indexing means an unread table has zero query cost; the storage cost is bounded by § 7.4. No v0.2 contract surface is touched.

#### 7.3.3 — Shelf consumer pattern

The canonical shelf consumer pattern over `N` cartridges and an `item` of kind `K`:

```
candidates = []
for cartridge in shelf:
    sketch = cartridge.sketches.get(K)
    if sketch is None:
        # No sketch for this kind in this cartridge; must fall back.
        candidates.append((cartridge, "unknown"))
    elif sketch.contains(item):
        candidates.append((cartridge, "probable"))
    # else: definitely-not — skip
return candidates
```

A shelf with all-sketched cartridges returns a candidate list of size `(true_matches + expected_false_positives)`. The downstream pass opens each candidate and runs the precise query (FTS5, ULID lookup, surface-form match) to filter false positives.

### 7.4 — Sizing against the Meditations baseline

Worked example using [`07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`](../../07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun) (4.59 MiB total, rebuilt 2026-05-23 against engine `24c19c2`) at the default `p = 0.01`:

| `sketch_kind` | `n` (items) | `m` (bits) | `m / 8` (bytes) | `k` (hashes) | Storage per sketch |
|---|---:|---:|---:|---:|---:|
| `extraction_ulid` | 2,758 | 26,440 | 3,305 B | 7 | ~3.3 KiB |
| `node_ulid` | 3,813 | 36,552 | 4,569 B | 7 | ~4.5 KiB |
| `entity_surface` | 912 | 8,744 | 1,093 B | 7 | ~1.1 KiB |
| `fts_term` | 6,843 | 65,592 | 8,199 B | 7 | ~8.2 KiB |
| **Total** | — | — | — | — | **~17 KiB** |

17 KiB of sketches on a 4.59 MiB cartridge is **~0.37% storage overhead** — well under the 1% target. The reader's v2-hook note ("few KB per cartridge") holds up under the math.

**Sizing baseline provenance.** Numbers in the table above are observations against the rebuilt Meditations cartridge ([`07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun`](../../07_Sample_Cartridges/Marcus-Aurelius-Meditations.v03.lun), engine `24c19c2`, 2026-05-23), not fixed targets. The `extraction_ulid` and `fts_term` counts depend on the extraction model (this baseline used `claude-haiku-4-5-20251001`) and the FTS5 tokenizer config (`unicode61`); rebuilds against different models or tokenizer configs will produce different `n`. The `node_ulid` count is structural (PDF parse → tree) and the `entity_surface` count is post-NFKC+casefold-dedup and post-bare-name (engine commit `24c19c2`). The sizing *claim* (≤ 1% storage overhead at default `p = 0.01`) is the load-bearing invariant; the specific row numbers are illustrative.

Build cost is one pass over the four source vocabularies. For Meditations this is ~14,000 distinct items times ~7 hash-bit-sets each, all in-memory — sub-millisecond on any modern machine. The cost scales linearly with `n`; even a 100,000-extraction cartridge sketches in under a second.

### 7.5 — Migration path

**Strict additive change at v0.3.** No v0.2 surface is touched.

- **v0.2 cartridges:** No `sketches` table. No `meta.sketches_present`. v0.3 readers detect the absence and fall through to full-scan paths. There is no migration required for existing v0.2 cartridges; they simply do not benefit from the sketch pre-filter. A v0.2 cartridge re-built against a v0.3 builder gains sketches naturally as part of the rebuild.
- **v0.3 cartridges read by v0.2 readers:** The v0.2 reader is unaware of `sketches`. It sees the table as a benign extra and ignores it. v0.2 readers DO NOT enforce that unknown tables are forbidden (SPEC-006 enforces the meta contract, not table-set closure), so this is forward-compatible by construction.
- **PRAGMA bump:** `PRAGMA user_version` advances `2 → 3` at v0.3. `meta.format_version` advances `'0.2' → '0.3'`. The `application_id = 0x4C554E43` ('LUNC') is unchanged; the family is the same.
- **Optional populate.** v0.3 builders MAY ship without sketches. Such a cartridge is a valid v0.3 cartridge that simply lacks pre-filter capability. This makes SPEC-007 a pure-opt-in addition for the builder team and avoids gating the v0.3 release on sketch-population work.

The migration is therefore: ship v0.3 with `sketches` table created (empty) and the builder optionally populating it. Cartridges built before the builder gains sketch-population stay valid; cartridges built after gain the pre-filter. Shelf consumers always tolerate both shapes.

## Validation rules

Validation runs at both build time (builder self-check) and read time (reader pre-use check). Failures at build time fail the build loudly. Failures at read time degrade the affected sketch only (the cartridge stays openable; the sketch becomes unusable).

```python
def validate_sketch_row(row, supported_hash_families, max_num_hashes=32):
    # Hash family must be known to this consumer.
    if row.hash_family not in supported_hash_families:
        raise UnknownHashFamily(row.hash_family)

    # Bitset length must exactly match the declared bit count.
    expected_bytes = (row.num_bits + 7) // 8
    if len(row.bitset) != expected_bytes:
        raise BitsetLengthMismatch(
            expected=expected_bytes, actual=len(row.bitset)
        )

    # k must be in [1, max].
    if not (1 <= row.num_hashes <= max_num_hashes):
        raise InvalidHashCount(row.num_hashes)

    # m must be a positive multiple of 8.
    if row.num_bits <= 0 or row.num_bits % 8 != 0:
        raise InvalidBitCount(row.num_bits)

    # n must be sane.
    if row.num_inserted < 0:
        raise InvalidInsertCount(row.num_inserted)
    if row.num_inserted > row.num_bits:
        # Allowed in theory (filter is saturated) but is a build bug in practice.
        raise SuspiciousInsertCount(row.num_inserted, row.num_bits)

    # sketch_version must be a known value for this kind.
    if row.sketch_version not in KNOWN_SKETCH_VERSIONS[row.sketch_kind]:
        raise UnknownSketchVersion(row.sketch_kind, row.sketch_version)


def validate_meta_sketches_present(meta_value, sketches_table_rows):
    # If meta.sketches_present is set, it must match the actual rows.
    if meta_value is None:
        if sketches_table_rows:
            raise SketchesPresentButMetaUnset()
        return
    declared = set(meta_value.split(","))
    actual = {row.sketch_kind for row in sketches_table_rows}
    if declared != actual:
        raise MetaSketchesMismatch(declared=declared, actual=actual)
```

The builder MUST run `validate_sketch_row` against every row it writes, and MUST verify `meta.sketches_present` is consistent with the rows after writing. The reader MUST run `validate_sketch_row` lazily (when it first uses a sketch) and SHOULD run `validate_meta_sketches_present` at open if it cares about catching truncated builds.

A v0.3 reader MUST NOT cache the result of a stale validation — if the cartridge file changes between opens (rare in practice; cartridges are read-only after build) validation re-runs on the next open.

## Governance implications

- **Ledger / annotation events (SPEC-005):** Sketches are build-time artifacts and are *not* events. Building a sketch does not append to `annotation_ledger`. Sketches are recomputed (or omitted) on rebuild; the ledger is the source of truth for *what happened*, the sketches are a derived index for *what is in this file*. A future spec amendment may introduce a `cartridge_sketched` event if sketch-provenance becomes important for governance, but v0.3 stays event-free for sketches.
- **Multi-axis imprint weights (SPEC-004):** Sketches do not contribute to any axis of the trust vector. They are a pre-filter for "could this cartridge contain X" and have no bearing on "how much should I trust extraction X within this cartridge." Composers MUST NOT read from `sketches` as a trust input — that would conflate membership-probability with extraction-confidence, which is exactly the category error SPEC-003 fixed.
- **Actor roles (owner, ambassador, elder, oracle):** Sketches are role-neutral. The ambassador role (which SPEC-005 § Governance implications → "Cross-cartridge governance" frames as the primary cross-cartridge actor) is the heaviest user of the sketch pre-filter, but the format does not encode role in the sketch itself.
- **Cross-cartridge traversal:** This is the primary motivating consumer. `extraction_ulid` and `node_ulid` sketches make SPEC-005's promotion flow O(1) per cartridge instead of O(cartridge open). `nexus_refs` lookups across a shelf become a sketch-pass-then-verify pattern instead of an N-way scan.
- **Memory Matrix integration:** The runtime matrix family (`'LUNM'`, `memory_matrix.lun`) is out of scope for SPEC-007. The matrix has different access patterns (it is mutated in place, not shipped as a read-only artifact) and bloom filters age poorly under mutation — a deletion in the source vocabulary requires either a counting-bloom variant or a rebuild. If the matrix family wants membership pre-filters, that is a separate spec.

## Alternatives considered

### A. Store sketches in `meta` as base64 BLOBs.

The `meta` table is `(key TEXT, value TEXT)`. Sketches could be base64-encoded and stored as `meta.sketch_extraction_ulid = '<base64>'`, etc. Rejected because:

1. `meta` is human-readable key/value, by convention. Stuffing binary blobs in it breaks the "open the cartridge in `sqlite3` and inspect meta" affordance that SPEC-006 set up.
2. Parameters (`m`, `k`, `seed`, `hash_family`, `num_inserted`) would have to be packed into the same value (a JSON envelope, say), which is its own mini-format. A dedicated table with typed columns is simpler.
3. Base64 inflates the payload by 33%, partially defeating the size-conscious design.

The dedicated `sketches` table costs one CREATE TABLE statement and gains typed columns, named indices, and a natural place for `built_at` and `builder_version` provenance.

### B. Use cuckoo filters or quotient filters instead of bloom filters.

Cuckoo filters support deletion and have slightly better space efficiency at high load factors. Quotient filters offer similar properties with better cache behavior. Both are real options. Rejected for v0.3 because:

1. Bloom filters are taught in every algorithms course and implemented in every language's standard library or near-equivalent. Implementer cost is near zero.
2. Cartridges are immutable after build — the deletion-support advantage of cuckoo filters has no value here.
3. The space difference at the target false-positive rates (`p = 0.01`) is < 20% — meaningful at petabyte scale, irrelevant at the few-KiB scale this spec is operating at.

If a future SPEC-007.1 needs the deletion property (for the matrix family, say), cuckoo filters can be added as a new `hash_family` value, side-by-side with bloom filters. The schema accommodates this without change.

### C. Computed-on-open sketches (no on-disk storage).

A consumer could compute the sketch fresh at cartridge-open time by scanning the relevant tables, then keep it in memory for the session. This avoids any format change. Rejected because:

1. Computing the sketch *is* the full scan the spec is trying to avoid. The shelf-open cost becomes "open every cartridge and scan its tables to build the sketch" — strictly worse than the status quo, where the open-and-scan happens lazily per query.
2. A sketched-on-open approach forces the shelf to open every cartridge eagerly to build sketches, even when many of them never get queried.
3. Sketches need to persist between shelf sessions, which means an off-cartridge cache, which is option (1) from the root-cause section — already rejected.

The point of baking the sketch into the cartridge is that the build pays the scan cost once, and every subsequent shelf-open is free.

### D. Store sketches in a separate sidecar file (`<cartridge>.sketch`).

A `.sketch` sidecar adjacent to the `.lun` file. Rejected because it breaks the single-file portability invariant — the whole point of the cartridge is that one file is everything. A sidecar that can be lost in transit makes the cartridge accidentally less portable.

## Resolved questions

Each Q below was resolved per Ahab review on 2026-05-23 ahead of the `draft → accepted` promotion. Question bodies are preserved per the `project_spec_lifecycle` principle that the reasoning trail survives the decision; each `**Resolution (2026-05-23):**` line records what was picked.

1. **Is the `fts_term` sketch normative or optional?** FTS5's tokenizer choice is per-cartridge (`unicode61`, with optional `porter`/`stemming`/diacritic-removal flags). A `fts_term` sketch is only useful to a consumer that knows the tokenizer config. v0.3 cartridges use the same builder config consistently, but if the builder ever exposes per-cartridge tokenizer customization, the sketch becomes coupled to a config that isn't in `sketches`. Options: (a) require `sketches` to record tokenizer config when `sketch_kind = 'fts_term'`; (b) store the tokenizer config in `meta` and have the sketch refer to it implicitly; (c) drop `fts_term` from v0.3 and add it later when tokenizer-config questions are resolved. **Recommendation:** (b) for v0.3, with a `meta.fts_tokenizer_config` key, and document that the `fts_term` sketch is implicitly parameterized by that meta value.
   **Resolution (2026-05-23):** (b). New `meta.fts_tokenizer_config` key added; the `fts_term` sketch is implicitly parameterized by it. See § 7.2.5 (Tokenizer-config provenance).
2. **What is the default `p`?** This spec proposes `p = 0.01`. SPEC-005 promotion-flow membership checks may want tighter (`p = 0.001`); shelf full-text pre-filter may tolerate looser (`p = 0.05`). Should the default be per-kind, or uniform with builders free to override? **Recommendation:** uniform `p = 0.01` default; per-kind overrides via builder flag.
   **Resolution (2026-05-23):** Uniform `p = 0.01` default; per-kind overrides via builder flag. The recommendation in the spec body holds; no further amendment needed.
3. **Should the seed default to 0 (deterministic) or be randomized per build?** Deterministic seeds let two independent builds of the same source produce byte-identical sketches, which simplifies cache invalidation. Random seeds defend against pathological inputs crafted to maximize collisions, which is mostly a non-issue for non-adversarial corpora. **Recommendation:** default `seed = 0`; builders MAY override.
   **Resolution (2026-05-23):** Default `seed = 0`; builders MAY override. The § 7.1.2 wording holds; no further amendment.
4. **Should `meta.sketches_present` be a JSON array instead of a comma-separated string?** Comma-separated is simpler to read in a `sqlite3` REPL; JSON is more rigorous. The other `meta` values are mostly bare strings (`format_version = '0.2'`), so comma-separated matches the surrounding convention. **Recommendation:** comma-separated.
   **Resolution (2026-05-23):** Comma-separated. The § 7.2 step 5 wording holds; no further amendment.
5. **Does the `sketches` table need a `cartridge_ulid` column?** All other tables in v0.2 are implicitly scoped to "this cartridge" — there is no `cartridge_ulid` column on `doc_nodes` or `extractions`. The sketch is bound to the cartridge by virtue of being in the same file. Adding the column would be redundant. **Recommendation:** no `cartridge_ulid` column.
   **Resolution (2026-05-23):** No `cartridge_ulid` column. The schema in § 7.1 holds.
6. **Is there value in sketching `extraction_sources.node_ulid` or other join-table values separately?** Probably not — `node_ulid` is already covered by the `node_ulid` sketch, and join-table membership is a derived question. **Recommendation:** no.
   **Resolution (2026-05-23):** No. The four sketch kinds in § 7.1.1 hold; join-table membership stays derived.

## Dependencies

- **SPEC-006 (implemented):** Establishes the v0.2 baseline (`application_id`, `user_version`, `meta` contract). SPEC-007 is the first additive change layered on top.
- **SPEC-002 (implemented):** ULIDs are the items in the `extraction_ulid` and `node_ulid` sketches. The normalization rule ("raw ULID string, 26 chars, Crockford base32, uppercase") matches SPEC-002's canonical form.
- **SPEC-005 (implemented):** The primary consumer of `extraction_ulid` and `node_ulid` sketches. SPEC-005 does not need to change to use SPEC-007 — its promotion-flow code path can ask the sketch before opening a candidate cartridge.

No spec must change as a prerequisite for SPEC-007 to be accepted. The reader prototype's v2 hooks list calls out the consumer side; no engine code change is required for SPEC-007 acceptance, only for SPEC-007 implementation.

## Implementation notes

- **Implementation date:** 2026-05-23.
- **Commit/PR references:**
  - **Engine slice:** Luna Engine commit `c20673d` (`feat(cartridge): add sketch prefilters`) on top of `65551ae`.
  - **Reader consumer slice (v0.3.2):** [`06_Prototypes/ReaderPrototype/`](../../06_Prototypes/ReaderPrototype/) — Rust `src-tauri/src/shelf.rs` (~390 LOC including tests) + TS adapter `src/shelf.ts` + Zustand `src/shelfStore.ts` + demo UI `src/components/ShelfPanel.tsx` + 3 new Tauri commands (`open_shelf`, `close_shelf`, `shelf_filter_candidates`). 49 Rust tests pass (37 pre-existing + 12 new SPEC-007). `npm run build` clean.
- **Engine change surface (9 files):**
  - `pyproject.toml` — added `"mmh3>=4.0.0"` to the `dependencies` array. `mmh3.hash128(item, seed, signed=False, x64arch=True)` is the canonical Python binding for MurmurHash3_x64_128 specified in § 7.1.2.
  - `src/luna/cartridge/sketches.py` — NEW (~290 LOC). The SPEC-007 surface module: `compute_params`, `split_hash`, `bit_indices`, `make_bitset` / `set_bit` / `bit_is_set` (LSB-first per § 7.1.3), `normalize_ulid` / `normalize_entity_surface` / `normalize_fts_term` (§ 7.1.1), vocabulary iterators per kind, `build_sketch_for_kind`, `populate_sketches` (the build-time entry point), and `sketch_contains` (the query path with OOB-guard per § 7.3.1). Constants `HASH_FAMILY = "murmur3_x64_128"`, `DEFAULT_P = 0.01`, `DEFAULT_SEED = 0`, `SKETCH_VERSION = 1`, `FTS_TOKENIZER_CONFIG = "unicode61"`, `BUILDER_VERSION = "luna-builder/0.3.0"`.
  - `src/luna/cartridge/schema.py` — added `CREATE TABLE IF NOT EXISTS sketches (...)` block after `nexus_refs` and `CREATE INDEX IF NOT EXISTS idx_sketches_kind ON sketches(sketch_kind)` in the indexes block.
  - `src/luna/cartridge/builder.py` — added the sketch-population call after `validate_payloads()` and before `conn.commit()`. Writes `meta.sketches_present` and (when `fts_term` populates) `meta.fts_tokenizer_config = 'unicode61'`.
  - `src/luna/cartridge/validation.py` — added `SketchValidationError` exception class and three validators: `validate_sketch_row(row)`, `validate_meta_sketches_present(meta_sp, meta_ft, kinds_in_table)`, `validate_sketches(conn)` (the fsck-callable orchestrator).
  - `src/luna/cartridge/fsck.py` — added `'sketches'` mode dispatch in `_run_fsck`; exposes `--sketches` in the standalone CLI.
  - `src/luna/cartridge/cli.py` — added `--sketches` to the `lun fsck` subparser's mutually-exclusive group.
  - `tests/test_cartridge_sketches.py` — NEW (~340 LOC; 27 unit tests covering parameter computation, bitset layout, hash split, FPR within slack, normalization, populate roundtrip, and all validator paths).
  - `tests/test_cartridge_sketches_cli.py` — NEW (~110 LOC; 4 CLI tests covering the happy path, corrupted-bitset detection, missing-tokenizer-config detection (Q1 coupling), and the default-fsck regression gate).
- **Verification:** 122 cartridge tests pass (91 pre-existing + 31 new SPEC-007). `lun fsck --sketches` on a sketch-bearing Meditations v0.3 cartridge returns OK in 1.7 ms (Step 0 + sketch validation; well under the 50 ms target). E2E rebuild against the v0.3 Meditations cartridge (`/tmp/meditations-spec7-e2e.lun`) produces all four sketch kinds with `(num_bits, num_hashes)` per § 7.4 within rounding tolerance:
  - `extraction_ulid`: n=1106, m=10608, k=7 (matches § 7.4 exactly).
  - `node_ulid`: n=3813, m=36552, k=7 (vs. § 7.4 m=36576; 0.07% delta from spec's round-up direction).
  - `entity_surface`: n=211, m=2024, k=7 (vs. § 7.4 n=532 — the spec's count was pre-NFKC+casefold dedup; after § 7.1.1 normalization, ~60% collapse).
  - `fts_term`: n=6843, m=65592, k=7 (vs. § 7.4 ~4,500 estimate — the estimate was loose; actual is higher but the sizing claim ("a few KB per cartridge") still holds: total sketch storage 14,347 bytes ≈ 0.56% of the 2.5 MB cartridge, beating the 1% target).
- **Deviations from spec:** none. `compute_params` implements § 7.2 step 1 exactly with the round-up-to-multiple-of-8 and banker's rounding (per Q7 spec-gap clarification). The 128-bit split follows § 7.1.2's low/high contract verbatim. Bitset layout matches § 7.1.3 (LSB-first). Q1 `meta.fts_tokenizer_config` coupling is enforced by `validate_meta_sketches_present`.
- **Follow-up issues created:** none.
  - **Carry-forward for consumer slice:** the reader prototype's `## Open hooks for next slices` continues to call out a multi-cartridge shelf surface that would consume `sketch_contains()` across N cartridges. That's the next reader slice; this slice is producer-only.
  - **Known limit:** `iter_fts_terms` uses `temp.nodes_fts_vocab` (created via `CREATE VIRTUAL TABLE`) on the live connection. The DROP at end of iterator restores state, but a reader opening the cartridge read-only after this temp table was attached should not see it (per SQLite temp-table semantics). Builders open the connection RW so this is non-issue.
- **No migration for existing v0.3 cartridges.** Per design decision: existing v0.3 cartridges built before this slice remain valid without sketches; readers gracefully handle the absence per § 7.5. Rebuild to gain sketches.

- **Bare-name fix for entity_surface — engine commit `24c19c2` (2026-05-23).** `iter_entity_surfaces` was updated to yield BOTH the raw `extractions.content` (which the extractor stores as `f"{name} [{etype}]"`) and the bare name (everything before the last ` [`), so SketchedShelf consumers can match on either form. Before this fix, typing `Marcus Aurelius Antoninus` (no `[person]` suffix) in the Tauri reader returned definite miss, which violated the UX expectation even though it was correct per the literal sketch contents. Spec-compatible per § 7.1.1 ("distinct entity surface forms" — format-agnostic; the spec doesn't mandate the literal `"name [type]"` shape). Storage impact: Meditations `entity_surface` cardinality 465 → 912 (+96% — bigger than expected because almost every entity has a `[type]` suffix and NFKC+casefold doesn't collapse bare vs. bracketed forms), total sketch storage 14,347 → 17,143 bytes ≈ 0.37% of cartridge (still under 1% target). New test `test_iter_entity_surfaces_yields_full_and_bare` in `tests/test_cartridge_sketches.py` pins the behavior — 32 cartridge sketches tests pass (31 prior + 1 new). **Scope limitation:** partial-name queries like `Marcus` alone still return definite miss; bloom filters are exact-match only. Fuzzy/partial matching would require n-grams or a separate FTS5 entity index — a different feature, out of scope.

- **§ 7.1.2 integer-width clarification — 2026-05-24.** Added a paragraph to § 7.1.2 documenting the uint128-promotion requirement for fixed-width-language reimplementers of the Kirsch–Mitzenmacher inner loop. Motivated by the reader v0.3.2 u64-overflow bug discovered during visual verification ([`08_Journal/2026-05-23.md`](../../08_Journal/2026-05-23.md) entry #6): Rust's `wrapping_mul` on `i * h2` silently overflows when `h2` has its high bit set, producing bit indices that diverge from a correctly-arithmetic'd Python implementation and violating the "false = definitely not" contract from § 7.3.1. The pinned Rust regression test `bit_indices_python_parity_virtue_seed0` at `06_Prototypes/ReaderPrototype/src-tauri/src/shelf.rs:354-372` is the durable cross-language parity artifact (hardcodes Python-computed `(h1, h2)` and the full 7-element bit-index vector for `("virtue", seed=0, k=7, m=65592)`). No engine, reader, or cartridge change in this amendment; the bug was already fixed in reader v0.3.2 source on 2026-05-23 by promoting the multiply-and-add to u128 before the modulo.

- **§ 7.4 sizing re-baseline — 2026-05-24.** Updated the sizing table to reflect the rebuilt Meditations cartridge against engine `24c19c2`: `entity_surface` n=465 → 912 (bare-name fix above), `extraction_ulid` n=1,106 → 2,758 (Haiku 4.5 extraction richness; cartridge rebuild details in [`08_Journal/2026-05-23.md`](../../08_Journal/2026-05-23.md) entry #5), `node_ulid` and `fts_term` rebaselined to the deterministic Meditations counts (3,813 and 6,843 respectively). Total sketch storage 12 KiB → 17 KiB; overhead 0.5% → 0.37% (cartridge grew from 2.5 MiB to 4.59 MiB). The "≤ 1% storage overhead" claim is unchanged and remains the load-bearing invariant. Added a "Sizing baseline provenance" note below the table clarifying that row numbers are observations against a specific build, not targets.
