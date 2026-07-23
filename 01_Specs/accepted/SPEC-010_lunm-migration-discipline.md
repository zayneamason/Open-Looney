# SPEC-010: LUNM migration discipline

**Status:** active
**Severity:** high
**Author:** Ahab (with Claude)
**Created:** 2026-07-21
**Last updated:** 2026-07-21
**Affects format version:** LUNM v0.1 (no `user_version` bump — see § Migration path)

---

## Problem statement

The engine applies 25 in-place migrations to every matrix it opens, and 22 of them cannot fail. Not "do not fail" — *cannot report failing*: each wraps its statements in `except Exception` and writes a `logger.debug` line. A migration that silently does nothing leaves the file in a state the engine believes it has, and nothing anywhere notices. This is not hypothetical. [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) Q1 found that `profile_config` — a table SPEC-008 declares a format invariant — is created by exactly one of these swallowing migrations, on the only fail-silent path in a file whose other tables ride a propagating `executescript`. SPEC-010 sets the rule that would have caught it, plus the mechanics [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) Q4 handed down for `user_version` bumps.

## Observed evidence

All counts taken 2026-07-21 against Luna Engine HEAD `2ed07b0`.

- **`src/luna/substrate/database.py` contains 43 `except Exception` handlers and 22 "migration skip" `logger.debug` lines.** The pattern is uniform and evidently copied:

  ```python
  for stmt in statements:
      try:
          await self._connection.execute(stmt)
      except Exception as e:
          logger.debug(f"profile_config migration skip: {e}")
  ```

  That is `_migrate_profile_config_table()`. Twenty-one siblings are shaped identically.
- **The contrast is inside the same function.** `_load_schema()` runs `await self._connection.executescript(schema_sql)` unguarded at `database.py:199`; a failure there propagates and the engine does not start. Two classes of table — one that fails loud, one that cannot fail — with nothing recording which is which or why.
- **The severity gradient is real and currently inverted.** `profile_config` is a SPEC-008 § 4.3 format invariant and rides the silent path. `_migrate_quests_completed_at_column()` patches an engine-extension table and rides the same path. The mechanism does not distinguish them.
- **`logger.debug` is the wrong level even for tolerated failures.** Debug is off in any normal deployment, so a tolerated failure and a catastrophic one produce identical observable output: nothing.
- **Migrations are introspection-gated, not version-gated.** Every helper dispatches on `PRAGMA table_info(...)` or `CREATE TABLE IF NOT EXISTS` and no-ops when already applied. `user_version` plays no part in dispatch — established in [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) Q4's resolution, which also found that policy (a) had been nominally in force since 2026-05-10 and violated 25 consecutive times without consequence.
- **The bump mechanism has a fork hazard.** `database.py:155–175` writes the pragmas only inside the `application_id == 0` branch. Editing the `user_version` literal at `:164` would give new files the new value while every existing matrix stayed at `2`, with no detector.

## Root cause analysis

1. **`try/except` around DDL was doing two unrelated jobs.** One is legitimate: a migration that runs on every open must tolerate already-being-applied. The other is not: swallowing a genuine failure. SQLite's `IF NOT EXISTS` and the introspection guards already handle the first job, which means the `except Exception` is, in every one of these 22 cases, handling only the second. The pattern was almost certainly copied for the first reason and now serves only the second.

2. **There was no vocabulary for "this table matters more."** Without a classification, a blanket rule is the only option, and a blanket fail-loud rule is genuinely wrong for a conditional subsystem whose absence is legal. So the blanket fail-*silent* rule won by default. [SPEC-009](SPEC-009_lunm-schema-ownership.md) supplies the missing vocabulary, which is why SPEC-010 depends on it.

## Proposed solution

Three rules. The first is the spec's teeth; the second and third make it survivable.

### 4.1 Rule 1 — failure tolerance is tiered by classification

A migration's permitted failure behaviour is determined by the [SPEC-009](SPEC-009_lunm-schema-ownership.md) classification of the table it touches:

| Table classification | On failure | Rationale |
| --- | --- | --- |
| `format-invariant` | **MUST propagate.** No `except` around the DDL. | The file is not a valid LUNM file without it. Starting anyway produces a file that violates SPEC-008 § 4.3 while reporting success. |
| `engine-extension` | **MAY degrade**, and MUST log at `warning` or above, naming the table and the exception. | A missing feature table degrades one feature. Continuing is defensible; doing so invisibly is not. |
| `conditional` | **MAY degrade**, MUST log at `info` or above. | Absence is legal, so failure is expected in normal operation and should not read as an error. |
| `vestigial` | SHOULD NOT be migrated at all. | The table is scheduled for removal. New migration code against it is waste. |

A migration touching tables of mixed classification takes the strictest applicable rule.

### 4.2 Rule 2 — a tolerated exception must be named

Where an `except` is permitted, it MUST name the specific exception type it tolerates and state why in a comment. `except Exception` around DDL is prohibited outright, at every classification. Catching everything is how a typo in a column name becomes a silent no-op.

### 4.3 Rule 3 — idempotency is structural, not defensive

Migrations MUST achieve idempotency through `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, or an explicit `PRAGMA table_info` guard — never by catching the error that re-application raises. This is what removes the legitimate half of the current `try/except`'s job, and it is what makes Rule 2 affordable rather than onerous. The engine already does this correctly in all 25 helpers; SPEC-010 ratifies the practice and removes the redundant guard that hides real failures.

### 4.4 Startup integrity report

At the end of the migration chain, the engine MUST emit a summary: how many migrations ran, how many no-opped, and — naming each — how many degraded. A degraded migration on an `engine-extension` table is acceptable; twelve of them is a broken install, and today the two are indistinguishable. This is what makes "MAY degrade" safe rather than a renamed version of the current behaviour.

### 4.5 `user_version` bump mechanics

[SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) § 4.1 settles *when* LUNM bumps. SPEC-010 carries *how*:

1. A bump MUST land as an explicit migration branch that reads the current `user_version` and writes the new one. It MUST NOT be performed by editing the literal at `database.py:164`, which sits inside the `application_id == 0` branch and would therefore apply only to newly-created files — forking production matrices at the old value with no detector.
2. The bump migration MUST be idempotent under Rule 3: re-running it on an already-bumped file is a no-op, not an error.
3. `lunm.format_version` ([SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) Q2) MUST move in the same migration. The two are the same fact in two places, and SPEC-008's Block B validation asserts they agree.
4. A bump MUST NOT be combined with unrelated schema work in one migration. The bump is the thing a future reader will bisect for.

### Schema changes

None. SPEC-010 constrains how DDL is applied, not what it says.

### Behavioral changes

1. Remove the 22 blanket `except Exception` handlers from `database.py`, replacing each per Rules 1–3. `_migrate_profile_config_table()` is the priority: it touches a format invariant and MUST become fail-loud, which is also [SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md) Q1's first precondition for `implemented/`.
2. Raise the surviving tolerated-failure log lines from `debug` to `warning` / `info` per Rule 1.
3. Add the § 4.4 integrity report.

Sequencing note: SPEC-008 Q1 requires relocating `profile_config`'s DDL into `schema.sql`, which moves it onto the already-propagating `executescript` path and satisfies Rule 1 for that table as a side effect. Doing the SPEC-008 work first makes this spec's highest-severity item disappear rather than needing a separate fix.

### Migration path

Forward-compatible; no `user_version` bump, per SPEC-008 § 4.1's triggers — changing how a migration reports failure alters no table and nothing a reader can observe.

**One risk deserves stating plainly.** Making 22 migrations fail-loud may surface failures that are happening today and going unseen. That is the point of the change, but it means the rollout order matters: land the integrity report (§ 4.4) *first*, observe one release at `warning`, then convert the format-invariant migrations to propagating. Converting first and observing second risks turning a silent partial failure into an engine that will not start, on a user's machine, with no prior signal.

## Validation rules

Static, at review time:

```python
# Pseudocode — a lint over the migration helpers.
for helper in migration_helpers(database_py):
    for handler in helper.exception_handlers:
        assert handler.type is not Exception, \
            f"{helper.name}: bare `except Exception` around DDL is prohibited (Rule 2)"

    if classification(helper.tables) == "format-invariant":
        assert not helper.exception_handlers, \
            f"{helper.name} touches a format invariant and MUST propagate (Rule 1)"
```

Runtime, at open: the § 4.4 report. No build-time validation applies — LUNM has no build.

## Governance implications

- **Ledger / annotation events:** N/A. LUNM has no `annotation_ledger`, and a hash-chained log would conflict with a continuously-mutated substrate.
- **Multi-axis imprint weights:** N/A.
- **Actor roles:** N/A.
- **Cross-cartridge traversal:** Indirect. SPEC-008 § 4.4's identity check reads `lunm.matrix_ulid` from `profile_config`; if the migration that creates that table can fail silently, the identity check degrades to family-only without anything saying so. Rule 1 closes that path.
- **Memory Matrix integration:** SPEC-010 governs every future change to the matrix. Its practical effect is that SPEC-011+ DDL ratifications have a defined way to ship.

## Alternatives considered

- **(a) Blanket fail-loud — no `except` around any DDL, ever.** Simpler to state and to lint, and rejected. It is wrong for `conditional` families: an `ih_*` migration failing because the Hub is not installed is correct behaviour, and a rule that calls it an error will be suppressed wholesale the first time it fires. A rule that gets globally disabled protects nothing.
- **(b) Idempotency and ALTER patterns as the spec's centre**, with the swallow pattern as a footnote. Rejected: it documents good practice the engine already follows — all 25 helpers are correctly introspection-gated — while leaving the actual defect untouched. The engine's migrations are well-written and unable to report failure; a spec about writing them well addresses the half that is already fine.
- **(c) Migration lifecycle and ordering as the centre.** There is a genuine ordering hazard — `_migrate_turn_type_column()` and `_migrate_turn_id_thread_column()` must run *before* `executescript` because `schema.sql` declares indexes on columns they add. Rejected as the centre because it is one known, commented, working case, whereas the silent-failure surface is 22 cases and unmonitored. Ordering belongs in this spec as a supporting rule, not its spine.
- **(d) Do nothing; rely on review.** Rejected. Twenty-two identical handlers is what review-only produces.

## Open questions

Each Q below blocks `active → accepted`.

1. **Does Rule 1 apply retroactively to all 22 handlers, or only to new migrations?** Retroactive is correct in principle and is the rollout risk named in § Migration path. **Recommendation:** retroactive, but staged per that section — report first, then convert format-invariant, then the rest. Q3 covers what happens if the report reveals live failures.

2. **What is the failure behaviour when the classification is unknown?** A migration touching a table absent from SPEC-009's manifest has no tier. **Recommendation:** treat unknown as `format-invariant` — fail loud. An unclassified table is a SPEC-009 conformance failure already, and the strict default makes the two defects surface together rather than masking one another.

3. **If the integrity report reveals migrations failing in production today, is that a SPEC-010 blocker or a separate bug?** **Recommendation:** separate bugs, filed individually, not blocking SPEC-010's acceptance — but SPEC-010 MUST NOT reach `implemented/` while a `format-invariant` migration is known to be failing, since that is the exact condition Rule 1 exists to prohibit.

4. **Does the lint in § Validation rules ship, and where?** A regex over `database.py` is fragile; an AST check is real work. **Recommendation:** AST-based, in the engine's test suite, scoped to files SPEC-009's manifest names as owners. Fragile linting of a rule this load-bearing is worse than none, because a passing fragile lint reads as proof.

5. **Do the `cartridge/` migrations fall under this spec?** `cartridge/migrate.py` migrates LUNC cartridges, not LUNM matrices, and runs its own `executescript` at `migrate.py:507`. **Recommendation:** out of scope — SPEC-010 is a LUNM spec — but flag whether LUNC deserves a symmetric spec, since the cartridge builder has its own failure-handling conventions this spec has not examined.

6. **What governs the engine-root `migrations/` directory?** `_migrate_ambassador_tables()` (`database.py:1233–1252`) reads `migrations/004_ambassador_protocol.sql` from disk and `executescript`s it, wrapped in the same `except Exception` / `logger.debug` swallow as its siblings — so a missing or unreadable *file* degrades identically to a failed statement. Path-loaded DDL has a failure mode module-resident DDL does not: the file can simply be absent from a deployment. **Recommendation:** Rule 1 applies by the classification of the tables involved, and path-loaded DDL additionally MUST distinguish "file not found" from "statement failed" in its logging. See [SPEC-009](SPEC-009_lunm-schema-ownership.md) Q5 for the directory's disposition.

## Dependencies

**Upstream (must be accepted):**

- **[SPEC-008](../accepted/SPEC-008_lunm-family-foundation.md)** (accepted 2026-07-21) — supplies the `user_version` bump *triggers* that § 4.5 gives mechanics for, and the `profile_config` finding that motivates Rule 1.
- **[SPEC-009](SPEC-009_lunm-schema-ownership.md)** (active) — supplies the classification Rule 1 keys off. SPEC-010 can be *accepted* alongside SPEC-009, but cannot be *implemented* before it: without a manifest there is no way to know which tier a migration falls under.

**Downstream:**

- **SPEC-011+ (future)** — per-family DDL ratification. Each will ship changes that must comply with SPEC-010.

## Implementation notes

(Filled in when status moves to `implemented`.)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
