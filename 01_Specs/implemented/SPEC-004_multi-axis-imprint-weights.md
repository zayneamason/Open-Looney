# SPEC-004: Multi-axis imprint weights

**Status:** implemented (2026-05-22; reader v0.3.1 ships canonical reference composer `lun.format/reference-v1@1.0.0`)
**Severity:** medium
**Author:** Ahab (with Claude)
**Created:** 2026-05-22
**Last updated:** 2026-05-23
**Affects format version:** v0.2 (consumable now); v0.3+ (full signal set after SPEC-005 implementation)

---

## Problem statement

SPEC-003 removed the v0.1 `extractions.confidence` column on the (correct) grounds that a hardcoded scalar was not a measurement. The removal left a primitive-shaped hole. Applications reading `.lun` cartridges need *some* way to surface trust information to users — "how much should I believe this extraction?" — and the v0.2 contract gives them four orthogonal raw signals (`anchor_status`, `anchor_method`, `extraction_method`, `llm_logprob_sum` / `llm_token_count`) plus the SPEC-005 ledger event stream (when implemented), but no shared composition contract for turning those into a presentable trust indicator.

Without a contract, every consumer composes differently. Two readers staring at the same cartridge will produce different trust displays from the same underlying data, and no third party can tell what algorithm produced what number. The fragmentation problem SPEC-003 deferred to "SPEC-004 formalizes this" (SPEC-003 line 121).

This spec defines the composition contract: **a four-axis trust vector**, computed by named, versioned composer functions that operate purely over existing cartridge signals. No new cartridge schema. The format stays composer-agnostic; composers self-identify in their output so a user (or auditor) can always tell which algorithm produced which vector.

## Observed evidence

- SPEC-003 (commit `8d5c6d9`, Phase 4, 2026-05-12) dropped `extractions.confidence` and replaced it with raw signals. SPEC-003 line 121 explicitly forward-references SPEC-004 for the composition contract; SPEC-003 line 258 names this as "the primary downstream consumer" of SPEC-003.
- SPEC-001 (implemented) added the `anchor_status` taxonomy (`anchored`, `synthesized`, `match_failed`, `filtered`, `unknown`) plus `anchor_method` (`auto`, `manual`, `migrated`) on `claim_sources` — orthogonal categorical signals that any trust composer needs to read.
- SPEC-005 (accepted 2026-05-21) defines an 8-event-type append-only ledger plus a `annotation_actors` registry. Lines 741-744 of SPEC-005 name SPEC-004 as the consumer of anchor signals from SPEC-001 + extraction signals from SPEC-003 to compose multi-axis trust, and note that SPEC-005 unlocks access to *who* anchored what and *when* — directly informing the Authority and Temporal axes.
- The 2026-05-22 Marcus-Aurelius-Meditations audit ([`04_Audits/AUDIT_2026-05-22_meditations-v02.md`](../../04_Audits/AUDIT_2026-05-22_meditations-v02.md)) verified the raw-signal availability on a real corpus: 458 anchored claims + 54 match_failed + 62 anchored summaries + 532 entities with `unknown` status, all 1106 extractions with NULL logprob columns (carried-forward Phase 5 item 5). That cartridge is the reference test corpus for the SPEC-004 reference composer.
- The 2026-05-21 status sweep ([`08_Journal/2026-05-21.md`](../../08_Journal/2026-05-21.md) lines 112-117) named four axes: **Authority, Contestation, Temporal, Resonance**. This spec adopts that naming.

## Root cause analysis

Single-scalar trust collapses orthogonal dimensions (model uncertainty, source authority, anchor strength, age, contestation history) into one number that cannot be decomposed (SPEC-003 line 47). Different applications surface trust to different users for different purposes; a teacher displaying a quote from this cartridge has different priorities than an automated downstream pipeline ingesting claims. A single scalar locks every reader into one priority ordering.

The right shape is a **vector of independent axes**, each with a clear input signature and a clear semantic. UIs render or aggregate the axes as they choose. Different applications can implement different composers that all honor the same axis contract; their outputs can be compared because they share a vocabulary even when they disagree on weights.

The remaining design question is which axes. The 2026-05-21 journal already named four that correspond to four distinct decisions a reader-application makes about trust:

- **Who** vouched for this? (Authority)
- **Has anyone disagreed?** (Contestation)
- **How old / fresh is the signal?** (Temporal)
- **How often has it been reaffirmed?** (Resonance)

These four are orthogonal — none can be derived from the others — and together they exhaust the trust-shaping inputs that v0.2 + SPEC-005 make available. Adding a fifth (e.g., a separate "model uncertainty" axis from `llm_logprob_sum`) would arguably make sense, but model uncertainty is already an input to Authority (a high-logprob LLM extraction with an auto anchor is more authoritative than a low-logprob one) and splitting it out invites composers to double-count. v0.2 ships with four axes; a SPEC-004.1 amendment can add more if practice surfaces a clear gap.

## Proposed solution

### 4.1 — The four axes

| Axis | Semantic meaning | Inputs (raw signals) | Range | NULL when |
|---|---|---|---|---|
| **Authority** | Who vouched for this, and with what method? | `anchor_status` and `anchor_method` (SPEC-001); `anchored_by` (SPEC-001); `extraction_method` and `llm_logprob_sum` / `llm_token_count` (SPEC-003); `annotation_actors` registry + `actor_role` of ledger events (SPEC-005, when present) | `[0.0, 1.0]` | `anchor_status = 'unknown'` (entities in v0.2); no signal at all |
| **Contestation** | Has this been disputed, reconciled, overridden, or filtered after build? | SPEC-005 events: `claim_disputed`, `claim_reconciled`, `claim_filtered`, `summary_overridden` targeting this extraction's ULID | `[0.0, 1.0]` | No SPEC-005 ledger in cartridge |
| **Temporal** | How fresh is the most recent trust-relevant event? | `meta.created_at` (build time); `claim_sources.anchored_at` (SPEC-001); `annotation_ledger.entry_ts` (SPEC-005, when present) of most recent event targeting this row | `[0.0, 1.0]` | No `anchored_at` and no events; cartridge has no `meta.created_at` |
| **Resonance** | Has this been reaffirmed by multiple actors over time? | SPEC-005 events of type `claim_anchored`, `cartridge_reviewed`, `cartridge_imported`; counted by distinct `actor_id` | `[0.0, 1.0]` | No SPEC-005 ledger in cartridge |

For v0.2 cartridges (pre-SPEC-005-implementation), only **Authority** and **Temporal** will yield non-NULL values. Composers MUST return NULL for **Contestation** and **Resonance** when the ledger tables are absent. That is by design — applications get a partial trust vector and can render accordingly. v0.3 cartridges with SPEC-005 implemented unlock the full four-axis vector.

### 4.2 — The composer contract

A **SPEC-004 composer** is any function with the signature:

```
composer(cartridge_handle, target_ulid) -> TrustVector
```

where `cartridge_handle` is a read-only SQLite connection to a v0.2-or-later `.lun` cartridge, `target_ulid` is the SPEC-002 ULID of an extraction (claim, entity, or summary), and the return value is a **TrustVector** with this shape:

```python
{
    "spec_version":     "0.4",                  # SPEC-004 spec version
    "composer_id":      "example.org/composer", # opaque identifier; recommend reverse-DNS or URI
    "composer_version": "1.0.0",                # semver for THIS composer's algorithm
    "target_ulid":      "01HQ3K...",            # echoed for traceability
    "computed_at":      "2026-05-22T18:30:00Z", # ISO 8601 UTC of composition time
    "axes": {
        "authority":    0.72,                   # float in [0.0, 1.0] or null
        "contestation": null,
        "temporal":     0.95,
        "resonance":    null,
    },
    "notes": "...",                             # optional; free-text composer-specific commentary
}
```

#### Composer obligations

A composer MUST:

1. Be **deterministic** over `(cartridge_state_at_open, target_ulid)` for a given `(composer_id, composer_version)`. Two invocations against the same cartridge snapshot with the same target MUST return identical axis values (modulo `computed_at`, which records wall-clock time).
2. Return **NULL** per-axis when the inputs that axis requires are absent. NEVER fabricate a value to fill a gap.
3. Honor the axis ranges: each axis value MUST be `null` or a `float` in `[0.0, 1.0]` inclusive. Composers SHOULD clamp internal computations to this range explicitly rather than relying on input bounds.
4. Read only from documented v0.2 / SPEC-005 surfaces: `extractions`, `claim_sources`, `claim_context_nodes`, `meta`, `doc_nodes`, and (when present) `annotation_ledger` + `annotation_actors`. Composers MUST NOT read from `nexus_refs` for trust purposes in v0.2 (cross-cartridge promotion is SPEC-005 territory; reading from it requires SPEC-005 to be implemented and is a v0.3+ concern).
5. Populate all required TrustVector fields. Missing fields make the output non-SPEC-004-compliant.
6. Set `spec_version` to the SPEC-004 version it implements (`"0.4"` for this initial version).

A composer MAY:

- Use any internal algorithm: piecewise functions, exponential decay, log-prob normalization, weighted sums, neural composers, anything that respects the contract.
- Cache results application-side keyed by `(cartridge_ulid, target_ulid, composer_version)`. Cache invalidation when the cartridge changes (e.g., new ledger events appended) is the application's responsibility.
- Surface intermediate signals through the optional `notes` field for debugging.
- Implement a subset of axes — a composer that only fills Authority and returns NULL for the other three is SPEC-004-compliant, just less useful.

A composer MUST NOT:

- Write to the cartridge.
- Depend on actor state outside the `annotation_actors` registry (e.g., calling out to an external reputation service breaks determinism and portability).
- Collapse the axes into a single scalar at the contract level. (The application's render layer can choose to display a single number; the *contract* is a vector.)

### 4.3 — Reference composer (informative, not normative)

A worked example to illustrate the contract. **This is one valid composer; it is not the canonical default.** Implementations can and should differ.

The reference composer is grounded in the Marcus-Aurelius-Meditations baseline ([audit](../../04_Audits/AUDIT_2026-05-22_meditations-v02.md)): 458 anchored claims (`auto` method, `llm` extraction, logprob NULL per Phase 5 item 5), 54 `match_failed`, 62 anchored summaries, 532 entities with `unknown` anchor_status, no SPEC-005 ledger.

#### Authority (reference algorithm)

```python
def authority(row):
    if row.anchor_status == "unknown":
        return None  # entities in v0.2, until entity-anchoring spec lands

    base = {
        "anchored":     0.75,
        "synthesized":  0.55,
        "match_failed": 0.20,
        "filtered":     0.10,
    }[row.anchor_status]

    # Method bonus: manual/migrated provenance is more authoritative than auto
    method_bonus = {
        "auto":     0.00,
        "migrated": 0.05,  # human ran a migration tool against this
        "manual":   0.15,  # named actor explicitly anchored
    }[row.anchor_method or "auto"]

    # LLM logprob bonus: high-confidence LLM extractions get a small bump.
    # When logprob is NULL (current builder per Phase 5 item 5), bonus is 0.
    logprob_bonus = 0.0
    if row.llm_logprob_sum is not None and row.llm_token_count:
        mean_logprob = row.llm_logprob_sum / row.llm_token_count
        # mean_logprob ∈ (-inf, 0]; very high confidence ≈ 0, low ≈ -2
        logprob_bonus = max(0.0, min(0.10, 0.10 + mean_logprob * 0.05))

    return min(1.0, base + method_bonus + logprob_bonus)
```

Worked Meditations examples (current cartridge state, no SPEC-005 ledger):
- Anchored claim, auto, llm, logprob NULL → `0.75 + 0.00 + 0.00 = 0.75`
- Anchored summary, auto, llm, logprob NULL → `0.75 + 0.00 + 0.00 = 0.75`
- `match_failed` claim, auto, llm, logprob NULL → `0.20 + 0.00 + 0.00 = 0.20`
- Entity, anchor_status=unknown → `None`

#### Temporal (reference algorithm)

```python
def temporal(row, now=None):
    now = now or current_unix_ms()
    most_recent_ms = max(filter(None, [
        row.anchored_at,           # from claim_sources (SPEC-001)
        row.latest_event_ts,       # from annotation_ledger if present (SPEC-005)
        row.meta_created_at_ms,    # cartridge build time fallback
    ]), default=None)
    if most_recent_ms is None:
        return None

    age_days = (now - most_recent_ms) / (1000 * 60 * 60 * 24)
    # Exponential decay with 180-day half-life. Tunable per composer.
    HALF_LIFE_DAYS = 180.0
    return 0.5 ** (age_days / HALF_LIFE_DAYS)
```

Worked Meditations example (cartridge built 2026-05-22, audit run same day):
- Any extraction → `now - meta.created_at ≈ 0 days → temporal ≈ 1.0`
- Same cartridge audited 180 days later → `temporal ≈ 0.5`

#### Contestation (reference algorithm)

```python
def contestation(row):
    if not row.has_ledger:
        return None  # SPEC-005 not present
    n_disputes = row.count_events(["claim_disputed", "summary_overridden"])
    n_filtered = row.count_events(["claim_filtered"])
    n_reconciled = row.count_events(["claim_reconciled"])

    # Disputes and overrides drag contestation toward 0;
    # reconciliations partially restore.
    raw = 1.0 - 0.30 * n_disputes - 0.50 * n_filtered + 0.15 * n_reconciled
    return max(0.0, min(1.0, raw))
```

Worked Meditations example: no ledger present → `None` for every extraction.

#### Resonance (reference algorithm)

```python
def resonance(row):
    if not row.has_ledger:
        return None  # SPEC-005 not present
    distinct_actors = row.count_distinct_actors_in_events([
        "claim_anchored", "cartridge_reviewed", "cartridge_imported",
    ])
    # Asymptotic saturation: 1 actor → 0.35, 2 → 0.60, 5 → 0.92, ∞ → 1.0
    if distinct_actors == 0:
        return 0.0
    return 1.0 - 0.5 ** distinct_actors
```

Worked Meditations example: no ledger present → `None` for every extraction.

#### Reference composer self-identification

```python
SPEC_004_REFERENCE = {
    "composer_id":      "lun.format/reference-v1",
    "composer_version": "1.0.0",
    "spec_version":     "0.4",
}
```

Reproducibility expectation: running this reference composer against `07_Sample_Cartridges/Marcus-Aurelius-Meditations.lun` at the audit-recorded state, every claim of `anchor_status='anchored'` should produce `TrustVector.axes == {authority: 0.75, contestation: null, temporal: ~1.0, resonance: null}` (modulo `temporal` decay if run later than the audit date).

### 4.4 — Reader rendering guidance

Mirroring SPEC-001's display invariants. Per-axis affordances readers SHOULD honor:

| Axis | Display affordance | Mandatory metadata |
|---|---|---|
| Authority | Badge or color saturation; `0` → muted, `1` → bold | Always show alongside the extraction |
| Contestation | Warning chip ("disputed by N") when below `0.5`; surface on hover otherwise | Show when non-NULL |
| Temporal | Freshness icon (e.g., "today", "6mo ago"); decay visualization | Show when non-NULL |
| Resonance | Count chip ("affirmed by N") when above `0.4`; hidden otherwise | Show when non-NULL |

NULL axes MUST render as "—" or be omitted entirely. NEVER substitute a default value at render time — that would silently re-fabricate the trust signal the composer correctly disclaimed.

**Cross-composer comparison rule.** Any UI surfacing a TrustVector MUST also surface `composer_id` + `composer_version`, even if compactly (e.g., a tooltip on the trust badge). A user looking at two TrustVectors must always be able to tell which composers produced them. This is the SPEC-003 anti-fragmentation principle applied at the render layer.

### 4.5 — What's explicitly out of scope

- **Orphan semantic classification.** The Phase 5 deferral item 4 from [`08_Journal/2026-05-21.md`](../../08_Journal/2026-05-21.md) ("full SPEC-001 orphan semantic classification — synthesis/filtered detection via multi-lineage similarity + section-type heuristics") is single-axis classification refinement, not multi-axis composition. SPEC-004 explicitly does NOT absorb this. Recommend a separate future spec (unnumbered for now; the placeholder SPEC-007 went to cartridge sketches in [`01_Specs/implemented/SPEC-007_cartridge-sketches.md`](SPEC-007_cartridge-sketches.md), an unrelated concern).
- **Entity anchoring.** SPEC-001 deferred entity anchoring to a future spec. Until that lands, the Authority axis MUST return NULL for entities (because `anchor_status='unknown'` is legitimate and load-bearing). Composers that fabricate Authority for entities violate the contract.
- **A canonical default composer.** SPEC-004 defines the *contract*; §4.3's reference composer is illustrative only. No default composer ships in the format or in `lun fsck`. Applications choose or write their own.
- **Schema additions to the cartridge.** The composition primitive lives in code. The format stays composer-agnostic.
- **Cross-cartridge trust aggregation.** Composing TrustVectors from multiple cartridges (e.g., when a claim has been promoted via `nexus_refs`) is a SPEC-005 concern (plus a future cross-cartridge-aggregation spec, unnumbered for now), not SPEC-004. The composer signature is single-cartridge. SPEC-007 cartridge sketches provide cross-cartridge membership pre-filter but not trust aggregation.
- **A `validate_trust_vector` reference implementation.** See Q4 below; SPEC-004 ships the contract, not a validator library.

## Migration path

Purely additive at the spec level; **zero cartridge schema impact, zero migration tool needed**.

- v0.2 cartridges (currently shipping): SPEC-004 composers work immediately. Authority and Temporal axes yield non-NULL values; Contestation and Resonance yield NULL because no SPEC-005 ledger exists yet.
- v0.3 cartridges (post-SPEC-005-implementation): all four axes available. Composers detect ledger presence via `SELECT name FROM sqlite_master WHERE name='annotation_ledger'` (or by trying the SELECT and catching the error — both are valid SPEC-004 patterns).
- Pre-v0.2 cartridges: out of scope. v0.2 readers refuse to open them per SPEC-006 + the SPEC-002 Q5 retirement (see [LUN-FORMAT_v0.2.md](../../03_Format_Spec/LUN-FORMAT_v0.2.md) File identification step 3).

## Validation rules

Validation is application-level, not cartridge-level. There is nothing in the cartridge to validate against this spec — the spec only constrains composer behavior.

**Composer output validation** (recommended for applications consuming TrustVectors from third-party composers):

```python
def validate_trust_vector(tv: dict) -> None:
    # Required keys
    required = {"spec_version", "composer_id", "composer_version",
                "target_ulid", "computed_at", "axes"}
    missing = required - tv.keys()
    if missing:
        raise SpecError(f"TrustVector missing required keys: {missing}")

    # Axes shape
    axis_names = {"authority", "contestation", "temporal", "resonance"}
    if set(tv["axes"].keys()) != axis_names:
        raise SpecError(f"axes must contain exactly {axis_names}")

    # Each axis value in [0,1] or None
    for name, value in tv["axes"].items():
        if value is None:
            continue
        if not isinstance(value, (int, float)):
            raise SpecError(f"axes.{name} must be float or null; got {type(value)}")
        if not (0.0 <= value <= 1.0):
            raise SpecError(f"axes.{name}={value} out of [0,1]")

    # spec_version recognized
    if tv["spec_version"] not in {"0.4"}:
        raise SpecError(f"unrecognized spec_version: {tv['spec_version']}")

    # computed_at parses as ISO 8601
    datetime.fromisoformat(tv["computed_at"].replace("Z", "+00:00"))
```

**Reference test corpus.** The Marcus-Aurelius-Meditations cartridge serves as the SPEC-004 reference test corpus. Implementations of the §4.3 reference composer should produce the worked values listed there when run against this cartridge.

## Governance implications

- **The choice of composer is itself a governance decision.** "We use composer X v1.2.0" is a community / application policy choice as much as "we trust elder Y to reconcile disputes" is a SPEC-005 governance choice. Surfacing composer identity in the UI (per the cross-composer comparison rule in §4.4) makes that policy choice visible to users.
- **SPEC-005's actor registry feeds Authority.** When an `ambassador` upgrades a `match_failed` claim to `anchored` (SPEC-001 ambassador-upgrade ceremony), SPEC-005 records that event and SPEC-004's Authority axis treats it as more authoritative than an `auto` anchor. SPEC-004 makes the *consequences* of SPEC-005's governance decisions legible to readers.
- **Multi-composer disagreement is detectable.** Two TrustVectors with the same `target_ulid` and `spec_version` but different `composer_id`s can have different axis values; that disagreement is itself a meta-signal. Future tooling (out of scope here) could surface "this claim's Authority varies across composers — investigate why."
- **Composer-induced bias is recoverable.** Because composers are versioned and identified, a community that discovers a composer was systematically over-weighting one axis can re-compose with a different composer; the underlying cartridge is unchanged. Trust composition is reversible in a way that schema-baked trust scores aren't.

## Alternatives considered

- **Single composite trust score.** Rejected. SPEC-003 line 47 already established that "single-scalar trust is the wrong shape for the problem"; SPEC-004 inherits that reasoning. A single number per extraction mixes orthogonal axes and locks every application into one priority ordering.
- **Cartridge-side cached trust signals.** Rejected. SPEC-003 line 121 says "The composition algorithm — how these combine into a UI-displayable trust indicator — lives in code, not in the cartridge." Caching in the cartridge re-introduces the fragmentation problem: which composer's cache wins?
- **Composer identity in cartridge `meta`** (e.g., `meta.default_composer_id = 'lun.format/reference-v1'`). Rejected. Same problem — it picks a winner at build time, removing application choice. Composers can read from `meta` if they want; SPEC-004 doesn't store composer identity there.
- **Schema-defined extensible axes** (e.g., `axes` is open-ended dict; composers can add custom axes). Rejected. Extensibility forces every consumer to handle unknown axes, defeating the rendering contract. If practice surfaces a missing axis, a SPEC-004.1 amendment adds it normatively.
- **Output as scalar with axis breakdown as metadata.** Rejected — same as the first alternative; just hides the scalar problem behind a metadata wrapper.
- **Fewer axes** (collapse Temporal + Resonance into "vitality"). Considered. They answer different questions ("how fresh is the most recent signal?" vs. "how often has it been reaffirmed?") and a freshness change does not imply a resonance change. Keep separate.
- **More axes** (e.g., separate "model uncertainty" axis from logprob). Considered. Model uncertainty is already an input to Authority and surfacing it as its own axis invites composers to double-count. Out of scope for this version; revisit if practice shows a clear need.
- **Asynchronous composer signature** (returns Future / Promise). Rejected. SPEC-004 composers are pure functions over cartridge state. If an application needs async composition (e.g., for slow models), the application wraps a SPEC-004-compliant sync composer in its own async layer.
- **Composer registry via well-known URL** (e.g., composers register at `lun.format/composers/`). Considered. Worth doing eventually but outside SPEC-004's scope. SPEC-004 just defines the contract; a registry is an ecosystem concern.

## Resolved questions

Resolved 2026-05-22 per Ahab review. Each Q below records the resolution following the original analysis; bodies retained per the spec-lifecycle principle that the reasoning trail survives the decision.

**Q1 — Axis range: `[0.0, 1.0]` vs. `[-1.0, 1.0]`.**
A `[-1, 1]` range would let an axis express "negative trust" (e.g., a heavily disputed claim could have negative Contestation rather than just low Contestation). The downside is two-fold: (a) it doubles the rendering complexity (composers must decide what "0" means — neutral? unknown? absent?), and (b) negative signals belong conceptually in Contestation, not in negative numbers in Authority. **Recommend `[0.0, 1.0]`** with NULL for "no signal" and low values for "weak signal." If a future axis genuinely needs negative values, add it as a separate axis rather than redefining the range.

**Resolution (2026-05-22):** `[0.0, 1.0]` + NULL for absent signal. Spec body §4.1 already reflects this.

**Q2 — Companion payload-schemas file.**
SPEC-005 split into two files (`SPEC-005_annotation-ledger.md` + `SPEC-005_payload-schemas.md`) because the payload schemas are a stable, separate contract that the ledger spec depends on. SPEC-004's reference composer is *informative*, not a stable contract — composers are expected to differ. **Recommend keeping SPEC-004 as a single file**; the reference composer stays inline in §4.3. If at some future point a normative composer is needed, it becomes its own spec.

**Resolution (2026-05-22):** Single file. Reference composer stays inline in §4.3, clearly marked informative.

**Q3 — Determinism scope.**
A composer is deterministic over `(cartridge_state_at_open, target_ulid)`. But cartridges grow ledger entries over time; an "anchored" claim today may have 5 disputes appended tomorrow, changing Contestation. Re-running the same composer with the same `target_ulid` will yield different axes if the cartridge state has changed between calls. **Recommend the spec's current language**: composers are deterministic over a *snapshot* of cartridge state; `computed_at` documents which snapshot. Re-composition on cartridge change is the application's responsibility. (Caching guidance in §4.2 supports this.)

**Resolution (2026-05-22):** Snapshot semantics. §4.2 obligation 1 + caching guidance reflect this; re-composition on cartridge change is the application's responsibility.

**Q4 — Validation library: shipped or contract-only?**
SPEC-005 ships explicit `validate_ledger()` / `validate_payloads()` pseudocode because those checks need to run at build time to guarantee cartridge invariants. SPEC-004 has no equivalent build-time check — composers run at application read time, so there's nothing to validate against the cartridge. The `validate_trust_vector()` sketch in §"Validation rules" is recommended for applications consuming third-party TrustVectors. **Recommend contract-only**: the spec defines the validation rules; concrete implementations are library-level decisions (e.g., a Python `lun.trust` package could ship `validate_trust_vector`; SPEC-004 doesn't mandate this).

**Resolution (2026-05-22):** Contract-only. The `validate_trust_vector` sketch in §"Validation rules" is illustrative; concrete validators ship in downstream libraries.

## Dependencies

**Must be accepted before this spec can be implemented:**

- **SPEC-001 (implemented)** — `anchor_status`, `anchor_method`, `anchored_by`, `anchored_at`. Composers read these as Authority and Temporal inputs.
- **SPEC-002 (implemented)** — ULID identity. The TrustVector `target_ulid` field uses SPEC-002 ULIDs.
- **SPEC-003 (implemented)** — `extraction_method`, `llm_logprob_sum`, `llm_token_count`. Composers read these as Authority inputs. SPEC-003 line 121 is the foundational forward-reference this spec resolves.
- **SPEC-005 (implemented)** — `annotation_ledger` event types and `annotation_actors` registry. Required for Contestation and Resonance axes to be non-NULL. Composers gracefully degrade when SPEC-005 is not implemented (return NULL for those axes).

**This spec does not block:**

- v0.3 format spec work (the integer-rowid removal phase in SPEC-002 D5 is orthogonal).
- Any further extraction/anchoring spec (e.g., entity anchoring) — those produce signals that SPEC-004 composers would read; they don't depend on SPEC-004.

**This spec is referenced by:**

- SPEC-001 lines forward-referencing trust composition over `anchor_status`.
- SPEC-003 lines 47, 121, 258, 265, 267, 325 (primary downstream consumer).
- SPEC-005 lines 741-744, 939-940 (reads ledger events to weight trust by authority and recency).

## Implementation notes

- Reference: ReaderPrototype v0.3.1 (slice #3 of the post-v0.3 rollout per [`08_Journal/2026-05-22.md`](../../08_Journal/2026-05-22.md) "Post-v0.3 slice #3 — SPEC-004 reference composer landed (reader v0.3.1)").
- Implementation date: 2026-05-22.
- Canonical reference composer: [`06_Prototypes/ReaderPrototype/src-tauri/src/trust.rs`](../../06_Prototypes/ReaderPrototype/src-tauri/src/trust.rs) (498 LOC). Constants per §4.3: `COMPOSER_ID = "lun.format/reference-v1"`, `COMPOSER_VERSION = "1.0.0"`, `SPEC_VERSION = "0.4"`, `HALF_LIFE_DAYS = 180.0`. Four axis functions `authority()`, `temporal()`, `contestation()`, `resonance()`; public entry points `compose(conn, target_ulid) -> TrustVector` and `compose_batch(conn, target_ulids: &[String])`.
- Frontend adapter: [`06_Prototypes/ReaderPrototype/src/trust.ts`](../../06_Prototypes/ReaderPrototype/src/trust.ts) (the JS-side swap point for a future application-supplied composer).
- Display surface (per §4.4): [`src/components/TrustBadges.tsx`](../../06_Prototypes/ReaderPrototype/src/components/TrustBadges.tsx) (full 4-axis drawer display) and [`src/components/AuthorityBar.tsx`](../../06_Prototypes/ReaderPrototype/src/components/AuthorityBar.tsx) (compact saturation bar in ExtractionsPanel rows).
- Verification: 37 `cargo test` passing (29 pre-existing + 8 new trust tests covering anchored/match_failed/entity baseline composition, axis range invariants, determinism, batch parity, manual-anchor delta from slice #2). `npm run build` clean; 233 kB bundle / 72 kB gzip.
- Deviations from accepted draft: none. The composer follows §4.3 piecewise; the §4.4 display surface follows the rendering guidance; Q1–Q4 resolutions hold as drafted.
- Follow-up issues: no tracker items. Manual visual verification (the closing gate the test suites cannot reach) still pending — `npm run tauri dev` smoke test against the v0.3 Meditations cartridge and `/tmp/meditations-slice2-test.lun` to confirm AuthorityBar + TrustBadges render and the upgraded extraction `01KS76F9RD1FHNZ8BXSQT4ZQF2` composes to Authority ≈ 0.90.

---

## Cross-references

- [`03_Format_Spec/LUN-FORMAT_v0.2.md`](../../03_Format_Spec/LUN-FORMAT_v0.2.md) — v0.2 cartridge contract and raw-signal column definitions.
- [`01_Specs/implemented/SPEC-001_orphan-claims.md`](SPEC-001_orphan-claims.md) — `anchor_status` taxonomy + provenance columns; Authority input.
- [`01_Specs/implemented/SPEC-002_portable-ids.md`](SPEC-002_portable-ids.md) — ULID identity used by `target_ulid`.
- [`01_Specs/implemented/SPEC-003_meaningful-confidence.md`](SPEC-003_meaningful-confidence.md) — raw signals; this spec's foundational forward-referrer.
- [`01_Specs/implemented/SPEC-005_annotation-ledger.md`](SPEC-005_annotation-ledger.md) — ledger events that feed Contestation, Resonance, and parts of Authority/Temporal.
- [`01_Specs/implemented/SPEC-005_payload-schemas.md`](SPEC-005_payload-schemas.md) — event payload structure (for composers that inspect payloads).
- [`04_Audits/AUDIT_2026-05-22_meditations-v02.md`](../../04_Audits/AUDIT_2026-05-22_meditations-v02.md) — reference test corpus baseline numbers.
- [`08_Journal/2026-05-21.md`](../../08_Journal/2026-05-21.md) — origin of the four-axis naming (Authority / Contestation / Temporal / Resonance).
- [`08_Journal/2026-05-22.md`](../../08_Journal/2026-05-22.md) — drafting session log for this spec.
