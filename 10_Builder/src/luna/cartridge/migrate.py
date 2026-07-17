"""
Cartridge Migration: v0.1 → v0.2
=================================

Atomic SQL transformer applying SPEC-006 + SPEC-001 + SPEC-002 + SPEC-003 in a
single BEGIN IMMEDIATE transaction. Any failure rolls back; the file is fully
migrated or untouched.

Usage:
    python -m luna.cartridge.migrate <path>            # in-place migration
    python -m luna.cartridge.migrate --strict <path>   # reject orphan claims
    python -m luna.cartridge.migrate --dry-run <path>  # report without writing

Inputs accepted:
    - True v0.1: (application_id=0, user_version=0)
    - SPEC-002 Q5 partial-migration: (application_id=LUNC, user_version=1)

Anything else is rejected by the pre-flight gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


LUNC_MAGIC = 0x4C554E43


class MigrationError(Exception):
    """Raised when v0.1 -> v0.2 migration cannot complete cleanly."""


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, col: str, col_def: str
) -> None:
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")


def _apply_spec_006(conn: sqlite3.Connection, summary: dict) -> None:
    """SPEC-006: application_id, interim user_version, format_version meta marker."""
    conn.execute(f"PRAGMA application_id = {LUNC_MAGIC}")
    conn.execute("PRAGMA user_version = 1")  # interim; bumped to 2 at end of migration

    legacy = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('format_version', '0.2')"
    )
    if legacy:
        conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    summary["spec_006"] = "applied"


def _apply_spec_001(conn: sqlite3.Connection, summary: dict, strict: bool) -> None:
    """SPEC-001: anchor classification + provenance + claim_context_nodes.

    Existing claim_sources rows keep anchor_method='auto' via the column default
    per SPEC-001 line 181 — relabeling as 'migrated' would falsely imply a
    migration-time provenance action AND violate validate_anchors()'s
    non-auto-requires-anchored_by invariant (we have no actor identity for the
    original v0.1 builder run).

    Orphan claims (no row in claim_sources) receive the spec-documented fallback:
    anchor_status='match_failed', anchor_reason='migration_unclassified'. Full
    semantic classification (synthesized/filtered detection) is non-trivial and
    deferred to a future spec, likely bundled with SPEC-004. strict=True rejects
    any orphans so the operator can resolve manually before re-running.
    """
    _add_column_if_missing(
        conn, "extractions", "anchor_status",
        "TEXT NOT NULL DEFAULT 'unknown' "
        "CHECK (anchor_status IN ('anchored','synthesized','match_failed','filtered','unknown'))",
    )
    _add_column_if_missing(conn, "extractions", "anchor_reason", "TEXT")
    _add_column_if_missing(
        conn, "claim_sources", "anchor_method",
        "TEXT NOT NULL DEFAULT 'auto' "
        "CHECK (anchor_method IN ('auto','manual','migrated'))",
    )
    _add_column_if_missing(conn, "claim_sources", "anchored_by", "TEXT")
    _add_column_if_missing(conn, "claim_sources", "anchored_at", "INTEGER")
    _add_column_if_missing(conn, "claim_sources", "event_id", "TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_context_nodes (
            claim_id INTEGER NOT NULL,
            node_id INTEGER NOT NULL,
            relevance REAL NOT NULL,
            PRIMARY KEY (claim_id, node_id),
            FOREIGN KEY (claim_id) REFERENCES extractions(id),
            FOREIGN KEY (node_id) REFERENCES doc_nodes(id),
            CHECK (relevance >= 0.0 AND relevance <= 1.0)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_extractions_anchor_status "
        "ON extractions(anchor_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_claim_context_claim "
        "ON claim_context_nodes(claim_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_claim_context_node "
        "ON claim_context_nodes(node_id)"
    )

    anchored = conn.execute(
        """
        UPDATE extractions SET anchor_status = 'anchored'
        WHERE type = 'claim'
          AND id IN (SELECT DISTINCT claim_id FROM claim_sources)
        """
    ).rowcount

    orphans = conn.execute(
        "SELECT id FROM extractions WHERE type = 'claim' AND anchor_status = 'unknown'"
    ).fetchall()

    if orphans and strict:
        raise MigrationError(
            f"Strict mode: {len(orphans)} orphan claims would receive the "
            f"'migration_unclassified' fallback. Full classification analysis "
            f"(synthesized / filtered / match_failed) is not implemented in Phase 5. "
            f"Either resolve manually before re-running, or drop --strict to accept "
            f"the spec-documented fallback for all orphans."
        )

    conn.execute(
        """
        UPDATE extractions
        SET anchor_status = 'match_failed',
            anchor_reason = 'migration_unclassified'
        WHERE type = 'claim' AND anchor_status = 'unknown'
        """
    )

    summary["spec_001"] = {
        "anchored": anchored,
        "orphans_classified": len(orphans),
        "strict_mode": strict,
    }


def _apply_spec_002(conn: sqlite3.Connection, summary: dict) -> None:
    """SPEC-002: ULID identity (additive). Uses Phase 3.5 canonical generator."""
    from luna.cartridge.builder import ULIDGenerator

    ulid_gen = ULIDGenerator()

    _add_column_if_missing(conn, "doc_nodes", "ulid", "TEXT")
    _add_column_if_missing(conn, "extractions", "ulid", "TEXT")
    _add_column_if_missing(conn, "claim_sources", "claim_ulid", "TEXT")
    _add_column_if_missing(conn, "claim_sources", "node_ulid", "TEXT")
    _add_column_if_missing(conn, "claim_context_nodes", "claim_ulid", "TEXT")
    _add_column_if_missing(conn, "claim_context_nodes", "node_ulid", "TEXT")
    _add_column_if_missing(conn, "embeddings", "node_ulid", "TEXT")

    node_id_to_ulid: dict[int, str] = {}
    for (row_id,) in conn.execute("SELECT id FROM doc_nodes ORDER BY id").fetchall():
        ulid = ulid_gen.next()
        node_id_to_ulid[row_id] = ulid
        conn.execute("UPDATE doc_nodes SET ulid = ? WHERE id = ?", (ulid, row_id))

    extraction_id_to_ulid: dict[int, str] = {}
    for (row_id,) in conn.execute(
        "SELECT id FROM extractions ORDER BY id"
    ).fetchall():
        ulid = ulid_gen.next()
        extraction_id_to_ulid[row_id] = ulid
        conn.execute("UPDATE extractions SET ulid = ? WHERE id = ?", (ulid, row_id))

    for cid, nid in conn.execute(
        "SELECT claim_id, node_id FROM claim_sources"
    ).fetchall():
        conn.execute(
            "UPDATE claim_sources SET claim_ulid = ?, node_ulid = ? "
            "WHERE claim_id = ? AND node_id = ?",
            (extraction_id_to_ulid[cid], node_id_to_ulid[nid], cid, nid),
        )

    for cid, nid in conn.execute(
        "SELECT claim_id, node_id FROM claim_context_nodes"
    ).fetchall():
        conn.execute(
            "UPDATE claim_context_nodes SET claim_ulid = ?, node_ulid = ? "
            "WHERE claim_id = ? AND node_id = ?",
            (extraction_id_to_ulid[cid], node_id_to_ulid[nid], cid, nid),
        )

    for (nid,) in conn.execute("SELECT node_id FROM embeddings").fetchall():
        conn.execute(
            "UPDATE embeddings SET node_ulid = ? WHERE node_id = ?",
            (node_id_to_ulid[nid], nid),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_nodes_ulid ON doc_nodes(ulid)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_extractions_ulid ON extractions(ulid)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('deprecated_columns', ?)",
        ("doc_nodes.id,extractions.id",),
    )

    summary["spec_002"] = {
        "doc_nodes_ulids": len(node_id_to_ulid),
        "extraction_ulids": len(extraction_id_to_ulid),
    }


def _apply_spec_003(conn: sqlite3.Connection, summary: dict) -> None:
    """SPEC-003: drop confidence, add raw signals, set logprob meta markers.

    Existing rows get extraction_method='llm' via the column default (v0.1 only
    had the LLM extraction path). logprob columns stay NULL — no signal from v0.1
    extraction; paired-NULL invariant holds trivially.
    """
    sqlite_ver = sqlite3.sqlite_version_info
    if sqlite_ver < (3, 35, 0):
        raise MigrationError(
            f"SQLite {sqlite_ver} too old. SPEC-003 migration requires 3.35.0+ "
            f"for DROP COLUMN."
        )

    cols = [r[1] for r in conn.execute("PRAGMA table_info(extractions)").fetchall()]
    confidence_dropped = "confidence" in cols
    if confidence_dropped:
        conn.execute("ALTER TABLE extractions DROP COLUMN confidence")

    _add_column_if_missing(conn, "extractions", "llm_logprob_sum", "REAL")
    _add_column_if_missing(conn, "extractions", "llm_token_count", "INTEGER")
    _add_column_if_missing(
        conn, "extractions", "extraction_method",
        "TEXT NOT NULL DEFAULT 'llm' "
        "CHECK (extraction_method IN ('llm','rule','ner','manual'))",
    )

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('logprob_base', 'e')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) "
        "VALUES ('logprob_attribution', 'response_level')"
    )

    summary["spec_003"] = {
        "confidence_dropped": confidence_dropped,
        "llm_extractions_marked": conn.execute(
            "SELECT COUNT(*) FROM extractions WHERE extraction_method = 'llm'"
        ).fetchone()[0],
    }


def _migrate_open_conn(conn: sqlite3.Connection, strict: bool) -> dict:
    """Run the four spec migrations inside a single BEGIN IMMEDIATE transaction
    on an already-open connection. Caller owns lifecycle (commit/rollback/close).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")

    summary: dict = {"strict": strict}

    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    is_v01 = app_id == 0 and user_ver == 0
    is_partial = app_id == LUNC_MAGIC and user_ver == 1
    if not (is_v01 or is_partial):
        raise MigrationError(
            f"Not a migratable cartridge. app_id={app_id:#x}, user_version={user_ver}. "
            f"Expected either (app_id=0, user_version=0) for true v0.1 or "
            f"(app_id=LUNC=0x4C554E43, user_version=1) for SPEC-002 Q5 "
            f"partial-migration state."
        )
    summary["input_state"] = "v0.1" if is_v01 else "uv=1_partial"

    _apply_spec_006(conn, summary)
    _apply_spec_001(conn, summary, strict=strict)
    _apply_spec_002(conn, summary)
    _apply_spec_003(conn, summary)

    conn.execute("PRAGMA user_version = 2")

    from luna.cartridge.validation import (
        validate_anchors,
        validate_extractions,
        validate_ulids,
    )

    validate_extractions(conn)
    validate_ulids(conn)
    validate_anchors(conn)

    return summary


def migrate_v1_to_v2(
    path: Path | str, strict: bool = False, dry_run: bool = False
) -> dict:
    """Atomic v0.1 -> v0.2 migration. Returns a summary dict on success.

    Args:
        path: Path to the .lun cartridge to migrate.
        strict: If True, any orphan claim that would receive the
            'migration_unclassified' fallback fails the migration.
        dry_run: If True, clone the file into an in-memory DB, run the
            migration there, return the summary, and never touch the file
            on disk.

    Raises:
        MigrationError: If the input is not a migratable cartridge, or if any
            validator fails on the migrated state, or if --strict trips on an
            orphan claim. The transaction is rolled back; the on-disk file is
            untouched (atomic).
    """
    path = Path(path)
    if not path.exists():
        raise MigrationError(f"Cartridge not found: {path}")

    if dry_run:
        src = sqlite3.connect(str(path))
        mem = sqlite3.connect(":memory:")
        src.backup(mem)
        src.close()
        try:
            summary = _migrate_open_conn(mem, strict=strict)
            mem.commit()
            summary["path"] = str(path)
            summary["dry_run"] = True
            return summary
        except Exception:
            mem.rollback()
            raise
        finally:
            mem.close()

    conn = sqlite3.connect(str(path))
    try:
        summary = _migrate_open_conn(conn, strict=strict)
        conn.commit()
        summary["path"] = str(path)
        summary["dry_run"] = False
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.ProgrammingError:
            pass
        conn.close()


def _cli() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Migrate a v0.1 .lun cartridge to v0.2 (atomic, in-place)."
    )
    parser.add_argument(
        "path", type=Path,
        help="Path to v0.1 or partial-migration cartridge",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Fail if any orphan claim would receive the migration_unclassified fallback",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report changes without writing to the file",
    )
    args = parser.parse_args()

    try:
        summary = migrate_v1_to_v2(args.path, strict=args.strict, dry_run=args.dry_run)
        print(json.dumps(summary, indent=2))
    except MigrationError as e:
        print(f"MIGRATION FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
