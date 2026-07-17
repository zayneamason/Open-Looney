# SPEC-NNN: [Title]

**Status:** draft | active | accepted | implemented | rejected
**Severity:** critical | high | medium | low
**Author:** Ahab
**Created:** YYYY-MM-DD
**Last updated:** YYYY-MM-DD
**Affects format version:** v0.X

---

## Problem statement

What is broken, missing, or insufficient about the current state?
One paragraph. No solutions yet.

## Observed evidence

Concrete data, quotes, query results, or reproductions that demonstrate
the problem is real. Link to audit files in `04_Audits/` when applicable.

## Root cause analysis

Why does this happen? Distinguish between symptoms and causes.
If multiple modes produce the same symptom, enumerate them.

## Proposed solution

The actual design. Prefer schema-additive changes over schema-breaking
changes. Prefer validation over runtime checking. Prefer making invalid
states unrepresentable over checking for them.

### Schema changes

```sql
-- Exact DDL for any new tables, columns, indexes, triggers, constraints
```

### Behavioral changes

What code paths need to change? What validation runs when?

### Migration path

How do existing cartridges move to the new version? Is it:
- Read-compatible (new reader handles old files)
- Forward-compatible (old reader handles new files, ignoring additions)
- Migrating (one-time rewrite required)
- Breaking (requires major version bump)

## Validation rules

What checks run at build time? At read time? What should fail loud?

```python
# Pseudocode for validation logic
```

## Governance implications

How does this spec interact with:
- Ledger / annotation events
- Multi-axis imprint weights
- Actor roles (owner, ambassador, elder, oracle)
- Cross-cartridge traversal
- Memory Matrix integration

If none, state "N/A."

## Alternatives considered

Other designs considered and why they were not chosen.

## Open questions

Numbered list of unresolved design decisions. These block acceptance.

## Dependencies

Other specs that must be accepted or implemented before this one.

## Implementation notes

(Filled in when status moves to `implemented`)

- Commit/PR reference:
- Implementation date:
- Deviations from spec:
- Follow-up issues created:
