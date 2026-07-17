"""
Cartridge Validation
====================

Single source of truth for .lun cartridge invariants. Four module-level
validators plus the open-time meta check, gathered here in Phase 5 once
the count crossed the threshold for a dedicated module.

Validators:
    validate_extractions  — SPEC-003 signal contract
    validate_ulids        — SPEC-002 identity + cross-reference
    validate_anchors      — SPEC-001 anchor classification
    validate_cartridge_open — SPEC-006 / SPEC-002 Q5 / SPEC-003 open-time gate

Exceptions:
    BuildError, WrongFamilyError, UnsupportedVersionError,
    UnsupportedAttributionError

builder.py and __init__.py re-export from here so the public API is byte-
identical to pre-Phase-5 state. Semantics unchanged; this is a pure refactor.
"""

from __future__ import annotations

import re
import sqlite3


# SPEC-002 D1: canonical ULID — first char [0-7], remaining 25 in Crockford Base32.
ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


class BuildError(Exception):
    """SPEC-001: raised when a cartridge build fails an invariant check."""


class WrongFamilyError(Exception):
    """Raised when a file's application_id doesn't match the cartridge family."""


class UnsupportedVersionError(Exception):
    """Raised when a cartridge's user_version is outside the supported range."""


class UnsupportedAttributionError(Exception):
    """SPEC-003: raised when a v0.2 cartridge's meta logprob markers are
    missing or set to unsupported values. Legacy cartridges (uv<2) predate
    SPEC-003 and are exempt from this check."""


def validate_extractions(conn: sqlite3.Connection) -> None:
    """SPEC-003: verify extraction-signal invariants before cartridge finalization."""

    # 1. No 'confidence' column should exist post-migration
    cols = [row[1] for row in conn.execute("PRAGMA table_info(extractions)").fetchall()]
    if "confidence" in cols:
        raise BuildError(
            "extractions.confidence still exists. SPEC-003 schema change did not complete."
        )

    # 2. llm_logprob_sum range: <= 0 (natural log); flag < -1000 as unit error
    bad_logprob = conn.execute("""
        SELECT id, llm_logprob_sum FROM extractions
        WHERE llm_logprob_sum IS NOT NULL
          AND (llm_logprob_sum > 0.0 OR llm_logprob_sum < -1000.0)
    """).fetchall()
    if bad_logprob:
        raise BuildError(
            f"{len(bad_logprob)} extractions have llm_logprob_sum outside (-1000, 0]. "
            f"Natural log of a probability must be <= 0. A value below -1000 suggests "
            f"a unit error (log10 vs ln) or extreme content length."
        )

    # 3. llm_token_count must be positive when populated
    bad_tokens = conn.execute("""
        SELECT id, llm_token_count FROM extractions
        WHERE llm_token_count IS NOT NULL AND llm_token_count <= 0
    """).fetchall()
    if bad_tokens:
        raise BuildError(f"{len(bad_tokens)} extractions have non-positive llm_token_count.")

    # 4. Paired-NULL invariant
    mismatched = conn.execute("""
        SELECT id FROM extractions
        WHERE (llm_logprob_sum IS NULL) != (llm_token_count IS NULL)
    """).fetchall()
    if mismatched:
        raise BuildError(
            f"{len(mismatched)} extractions have mismatched logprob/token_count NULLs. "
            f"Both must be populated together or both NULL."
        )

    # 5. extraction_method enum (belt-and-suspenders against CHECK)
    bad_method = conn.execute("""
        SELECT id, extraction_method FROM extractions
        WHERE extraction_method NOT IN ('llm', 'rule', 'ner', 'manual')
    """).fetchall()
    if bad_method:
        raise BuildError(f"{len(bad_method)} extractions have invalid extraction_method values.")

    # 6. logprob_base meta marker
    row = conn.execute("SELECT value FROM meta WHERE key = 'logprob_base'").fetchone()
    if not row or row[0] != "e":
        raise BuildError(
            f"meta.logprob_base must be 'e' (natural log). "
            f"Got: {row[0] if row else 'MISSING'}"
        )

    # 7. logprob_attribution meta marker
    row = conn.execute("SELECT value FROM meta WHERE key = 'logprob_attribution'").fetchone()
    if not row or row[0] != "response_level":
        raise BuildError(
            f"meta.logprob_attribution must be 'response_level' for v0.2. "
            f"Got: {row[0] if row else 'MISSING'}"
        )


def validate_anchors(conn: sqlite3.Connection) -> None:
    """SPEC-001: validate anchor classification invariants. Raise BuildError if
    any invariant is violated. Runs after extraction completes and before
    finalize_for_shipping()."""

    # Invariant 1: no claim ships with anchor_status='unknown' (spec line 143-144).
    # Type-scoped — entities/summaries may be 'unknown' without violating the spec.
    unknowns = conn.execute(
        "SELECT id, content FROM extractions "
        "WHERE type = 'claim' AND anchor_status = 'unknown'"
    ).fetchall()
    if unknowns:
        raise BuildError(
            f"{len(unknowns)} claims have anchor_status='unknown'. "
            f"All claims must be classified before cartridge can ship."
        )

    # Invariant 2: anchor_status values are within the CHECK set (defense in depth).
    bad_status = conn.execute(
        "SELECT id, type, anchor_status FROM extractions "
        "WHERE anchor_status NOT IN ('anchored', 'synthesized', 'match_failed', 'filtered', 'unknown')"
    ).fetchall()
    if bad_status:
        raise BuildError(
            f"{len(bad_status)} extractions have invalid anchor_status (sample: {bad_status[:3]})"
        )

    # Invariant 3: 'anchored' extractions have >=1 claim_sources row.
    orphan_anchored = conn.execute(
        "SELECT e.id, e.type FROM extractions e "
        "LEFT JOIN claim_sources cs ON e.id = cs.claim_id "
        "WHERE e.anchor_status = 'anchored' AND cs.claim_id IS NULL"
    ).fetchall()
    if orphan_anchored:
        raise BuildError(
            f"{len(orphan_anchored)} extractions are marked 'anchored' but have no "
            f"claim_sources rows (sample: {orphan_anchored[:3]})"
        )

    # Invariant 4: 'match_failed' extractions have anchor_reason set.
    missing_reason = conn.execute(
        "SELECT id FROM extractions "
        "WHERE anchor_status = 'match_failed' "
        "AND (anchor_reason IS NULL OR anchor_reason = '')"
    ).fetchall()
    if missing_reason:
        raise BuildError(
            f"{len(missing_reason)} extractions are 'match_failed' but missing anchor_reason"
        )

    # Invariant 5: 'synthesized' extractions have >=2 context nodes AND >=2 distinct parent lineages.
    bad_synth = conn.execute("""
        WITH synth AS (
            SELECT id FROM extractions WHERE anchor_status = 'synthesized'
        ),
        ctx AS (
            SELECT ccn.claim_id,
                   COUNT(DISTINCT ccn.node_id) AS n_nodes,
                   COUNT(DISTINCT dn.parent_id) AS n_parents
            FROM claim_context_nodes ccn
            JOIN doc_nodes dn ON dn.id = ccn.node_id
            WHERE ccn.claim_id IN (SELECT id FROM synth)
            GROUP BY ccn.claim_id
        )
        SELECT s.id, COALESCE(c.n_nodes, 0), COALESCE(c.n_parents, 0)
        FROM synth s
        LEFT JOIN ctx c ON s.id = c.claim_id
        WHERE COALESCE(c.n_nodes, 0) < 2 OR COALESCE(c.n_parents, 0) < 2
    """).fetchall()
    if bad_synth:
        raise BuildError(
            f"{len(bad_synth)} synthesized extractions fail the lineage check "
            f"(need >=2 nodes AND >=2 distinct parent_ids); sample: {bad_synth[:3]}"
        )

    # Invariant 6: non-auto anchor_method requires anchored_by + anchored_at.
    bad_provenance = conn.execute(
        "SELECT claim_id, node_id, anchor_method, anchored_by, anchored_at "
        "FROM claim_sources "
        "WHERE anchor_method != 'auto' "
        "AND (anchored_by IS NULL OR anchored_at IS NULL)"
    ).fetchall()
    if bad_provenance:
        raise BuildError(
            f"{len(bad_provenance)} non-auto claim_sources rows missing provenance "
            f"(sample: {bad_provenance[:3]})"
        )


def validate_ulids(conn: sqlite3.Connection) -> None:
    """SPEC-002: verify ULID population, format, uniqueness, and cross-reference
    integrity. Called at build time before validate_anchors() and before
    finalize_for_shipping(). Raises BuildError on any violation."""

    checks = [
        ("doc_nodes",           "ulid",       "id"),
        ("extractions",         "ulid",       "id"),
        ("claim_sources",       "claim_ulid", "claim_id"),
        ("claim_sources",       "node_ulid",  "claim_id"),
        ("claim_context_nodes", "claim_ulid", "claim_id"),
        ("claim_context_nodes", "node_ulid",  "claim_id"),
        ("embeddings",          "node_ulid",  "node_id"),
    ]

    for table, ulid_col, pk_col in checks:
        # Invariant 1: NULL check — every ULID column must be populated.
        nulls = conn.execute(
            f"SELECT {pk_col} FROM {table} WHERE {ulid_col} IS NULL"
        ).fetchall()
        if nulls:
            raise BuildError(
                f"{table}.{ulid_col} is NULL on {len(nulls)} rows. "
                f"All rows must have a ULID after v0.2 build."
            )

        # Invariant 2: format check — 26-char Crockford Base32.
        bad_format = [
            row for row in conn.execute(
                f"SELECT {ulid_col} FROM {table}"
            ).fetchall()
            if not ULID_RE.match(row[0])
        ]
        if bad_format:
            raise BuildError(
                f"{table}.{ulid_col} has {len(bad_format)} malformed ULID(s). "
                f"Expected canonical 26-char ULID matching ^[0-7][0-9A-HJKMNP-TV-Z]{{25}}$"
            )

    # Invariant 3: uniqueness on primary ULID columns (belt-and-suspenders behind
    # UNIQUE INDEX — catches indexing bugs).
    for table, ulid_col in [("doc_nodes", "ulid"), ("extractions", "ulid")]:
        dupes = conn.execute(
            f"SELECT {ulid_col}, COUNT(*) AS c FROM {table} "
            f"GROUP BY {ulid_col} HAVING c > 1"
        ).fetchall()
        if dupes:
            raise BuildError(
                f"{table}.{ulid_col} has duplicate values: {dupes[:5]}"
            )

    # Invariant 4: cross-reference integrity — shadow ULIDs match the parent
    # table's ULID for the same integer FK. Catches builder bugs that write
    # inconsistent shadows.
    bad_claim_ref = conn.execute("""
        SELECT cs.claim_id, cs.claim_ulid, e.ulid
        FROM claim_sources cs
        JOIN extractions e ON e.id = cs.claim_id
        WHERE cs.claim_ulid != e.ulid
    """).fetchall()
    if bad_claim_ref:
        raise BuildError(
            f"claim_sources.claim_ulid mismatch on {len(bad_claim_ref)} rows: "
            f"shadow ULID does not match extractions.ulid for the same claim_id"
        )

    bad_node_ref = conn.execute("""
        SELECT cs.node_id, cs.node_ulid, dn.ulid
        FROM claim_sources cs
        JOIN doc_nodes dn ON dn.id = cs.node_id
        WHERE cs.node_ulid != dn.ulid
    """).fetchall()
    if bad_node_ref:
        raise BuildError(
            f"claim_sources.node_ulid mismatch on {len(bad_node_ref)} rows"
        )

    bad_ctx_claim_ref = conn.execute("""
        SELECT ccn.claim_id, ccn.claim_ulid, e.ulid
        FROM claim_context_nodes ccn
        JOIN extractions e ON e.id = ccn.claim_id
        WHERE ccn.claim_ulid != e.ulid
    """).fetchall()
    if bad_ctx_claim_ref:
        raise BuildError(
            f"claim_context_nodes.claim_ulid mismatch on {len(bad_ctx_claim_ref)} rows"
        )

    bad_ctx_node_ref = conn.execute("""
        SELECT ccn.node_id, ccn.node_ulid, dn.ulid
        FROM claim_context_nodes ccn
        JOIN doc_nodes dn ON dn.id = ccn.node_id
        WHERE ccn.node_ulid != dn.ulid
    """).fetchall()
    if bad_ctx_node_ref:
        raise BuildError(
            f"claim_context_nodes.node_ulid mismatch on {len(bad_ctx_node_ref)} rows"
        )

    bad_emb_ref = conn.execute("""
        SELECT emb.node_id, emb.node_ulid, dn.ulid
        FROM embeddings emb
        JOIN doc_nodes dn ON dn.id = emb.node_id
        WHERE emb.node_ulid != dn.ulid
    """).fetchall()
    if bad_emb_ref:
        raise BuildError(
            f"embeddings.node_ulid mismatch on {len(bad_emb_ref)} rows"
        )


def validate_cartridge_open(conn) -> None:
    """SPEC-006 + SPEC-002 Q5 + SPEC-003: enforce family/version/meta-marker integrity at open.

    Phase 5 closeout: v0.1 cartridges (app_id=0) are no longer accepted. They must be
    migrated to v0.2 via `python -m luna.cartridge.migrate <path>` before they can be read.
    The reader's legacy synthesis branches (user_ver < 2) remain in place for the
    SPEC-002 Q5 uv=1 partial-migration case only."""
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id != 0x4C554E43:
        raise WrongFamilyError(
            f"Cartridge has application_id={app_id:#x}, expected LUNC (0x4C554E43). "
            f"Pre-SPEC-006 v0.1 cartridges (app_id=0) must be migrated first via "
            f"`python -m luna.cartridge.migrate <path>`."
        )
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_ver not in (1, 2):
        raise UnsupportedVersionError(
            f"Cartridge user_version={user_ver}, expected 1 (integer-only mode) or 2 (full v0.2)"
        )

    if user_ver != 2:
        return  # Legacy uv=1 cartridges predate SPEC-003; no meta marker check.

    # SPEC-003: v0.2 (uv=2) MUST have logprob meta markers in expected state.
    base = conn.execute("SELECT value FROM meta WHERE key = 'logprob_base'").fetchone()
    if not base or base[0] != "e":
        raise UnsupportedAttributionError(
            f"meta.logprob_base must be 'e' for v0.2 cartridges. "
            f"Got: {base[0] if base else 'MISSING'}"
        )
    attribution = conn.execute("SELECT value FROM meta WHERE key = 'logprob_attribution'").fetchone()
    if not attribution or attribution[0] != "response_level":
        raise UnsupportedAttributionError(
            f"meta.logprob_attribution must be 'response_level' for v0.2 cartridges. "
            f"Got: {attribution[0] if attribution else 'MISSING'}"
        )
