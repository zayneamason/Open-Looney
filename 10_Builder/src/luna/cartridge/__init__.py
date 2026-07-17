"""
Luna Knowledge Cartridge (.lun)
===============================

A .lun file is a standalone SQLite database containing a document's
complete node tree, comprehension artifacts anchored to source nodes,
and embeddings for search.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .builder import CartridgeBuilder
from .schema import LUN_SCHEMA

logger = logging.getLogger(__name__)

__all__ = [
    "CartridgeBuilder",
    "LUN_SCHEMA",
    "resolve_source_ref",
    "list_extractions",
    "WrongFamilyError",
    "UnsupportedVersionError",
    "UnsupportedAttributionError",
    "validate_cartridge_open",
]


# Phase 5 Step 5: validators + open-time exceptions centralized in
# luna.cartridge.validation. Re-exported here for public API preservation.
from .validation import (
    UnsupportedAttributionError,
    UnsupportedVersionError,
    WrongFamilyError,
    validate_cartridge_open,
)


def resolve_source_ref(lun_path: Path | str, node_id: int) -> Optional[dict]:
    """Walk the parent chain from a node_id to build a source reference.

    Returns:
        {
            "cartridge": "filename.lun",
            "node_id": int,
            "node_type": str,
            "content": str,
            "section": str (nearest section heading),
            "section_path": ["Document Title", "Chapter 1", "Section 1.2"],
            "position_in_parent": int,
            "claims": [{
                "id": int,
                "ulid": str | None,
                "content": str,
                "anchor_status": str,
                "anchor_reason": str | None,
                "extraction_method": str,            # SPEC-003
                "llm_logprob_sum": float | None,     # SPEC-003 paired-NULL
                "llm_token_count": int | None,       # SPEC-003 paired-NULL
            }],
        }
    """
    lun_path = Path(lun_path)
    if not lun_path.exists():
        return None

    conn = sqlite3.connect(f"file:{lun_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # SPEC-006: validate cartridge family + version before any reads.
        validate_cartridge_open(conn)

        # SPEC-001 + SPEC-002 Q5 read-compat: legacy = no ulid, no anchor_status.
        # Covers v0.1 (uv=0) AND partial-migration LUNC (uv=1, ulid columns absent).
        user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
        is_legacy = (user_ver < 2)

        # Read the node
        node = conn.execute(
            "SELECT id, parent_id, type, content, position FROM doc_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()

        if not node:
            return None

        # Walk parent chain to build section_path
        section_path = []
        current_id = node["parent_id"]
        nearest_section = None

        while current_id is not None:
            parent = conn.execute(
                "SELECT id, parent_id, type, content, meta_json FROM doc_nodes WHERE id = ?",
                (current_id,),
            ).fetchone()

            if parent is None:
                break

            if parent["type"] == "section" and parent["content"]:
                section_path.append(parent["content"])
                if nearest_section is None:
                    nearest_section = parent["content"]
            elif parent["type"] == "document":
                meta = json.loads(parent["meta_json"]) if parent["meta_json"] else {}
                title = meta.get("title", "")
                if title:
                    section_path.append(title)

            current_id = parent["parent_id"]

        section_path.reverse()

        # SPEC-001: query claims anchored to this node. type='claim' filter
        # prevents summaries (now anchored via claim_sources) from leaking into
        # the claims array. Legacy cartridges (uv<2) lack ulid/anchor_status
        # columns — synthesize the missing fields per read-compat contract.
        if is_legacy:
            claim_rows = conn.execute(
                """
                SELECT e.id, e.content
                FROM extractions e
                JOIN claim_sources cs ON e.id = cs.claim_id
                WHERE cs.node_id = ? AND e.type = 'claim'
                """,
                (node_id,),
            ).fetchall()
            # SPEC-003 synthesized for legacy (uv=0 or uv=1):
            # extraction_method='llm' is the migration default (v0.1 only had LLM path);
            # logprob columns don't exist, so NULL.
            claims_out = [
                {
                    "id": c["id"],
                    "ulid": None,
                    "content": c["content"],
                    "anchor_status": "unknown",
                    "anchor_reason": None,
                    "extraction_method": "llm",
                    "llm_logprob_sum": None,
                    "llm_token_count": None,
                }
                for c in claim_rows
            ]
        else:
            claim_rows = conn.execute(
                """
                SELECT e.id, e.ulid, e.content,
                       e.anchor_status, e.anchor_reason,
                       e.llm_logprob_sum, e.llm_token_count, e.extraction_method
                FROM extractions e
                JOIN claim_sources cs ON e.id = cs.claim_id
                WHERE cs.node_id = ? AND e.type = 'claim'
                """,
                (node_id,),
            ).fetchall()
            claims_out = [
                {
                    "id": c["id"],
                    "ulid": c["ulid"],
                    "content": c["content"],
                    "anchor_status": c["anchor_status"],
                    "anchor_reason": c["anchor_reason"],
                    "extraction_method": c["extraction_method"],
                    "llm_logprob_sum": c["llm_logprob_sum"],
                    "llm_token_count": c["llm_token_count"],
                }
                for c in claim_rows
            ]

        return {
            "cartridge": lun_path.name,
            "node_id": node["id"],
            "node_type": node["type"],
            "content": node["content"] or "",
            "section": nearest_section or "",
            "section_path": section_path,
            "position_in_parent": node["position"],
            "claims": claims_out,
        }

    finally:
        conn.close()


def list_extractions(
    lun_path: Path | str,
    type_filter: Optional[str] = None,
    anchor_status_filter: Optional[str] = None,
) -> list[dict]:
    """SPEC-001: list extractions from a cartridge with optional filters.

    Returns matching extractions ordered by id. Each dict includes anchor_status
    and anchor_reason so UI consumers can render badges (e.g., '⚠ unanchored'
    for anchor_status='match_failed' per SPEC-001 line 157).

    Honors SPEC-001 read-compat: v0.1 cartridges have no anchor_status column.
    For v0.1, anchor_status is synthesized as 'unknown' in code; a filter for
    any other value returns an empty list."""
    lun_path = Path(lun_path)
    if not lun_path.exists():
        return []

    conn = sqlite3.connect(f"file:{lun_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        validate_cartridge_open(conn)
        user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
        # SPEC-001 + SPEC-002 Q5: legacy = no ulid, no anchor_status (covers
        # v0.1 uv=0 AND partial-migration LUNC uv=1 with ulid columns absent).
        is_legacy = (user_ver < 2)

        if is_legacy:
            if anchor_status_filter is not None and anchor_status_filter != "unknown":
                return []

            sql = "SELECT id, type, content FROM extractions"
            params: list = []
            if type_filter is not None:
                sql += " WHERE type = ?"
                params.append(type_filter)
            sql += " ORDER BY id"

            rows = conn.execute(sql, params).fetchall()
            # SPEC-003 synthesized fields: legacy schema has no logprob columns;
            # extraction_method defaults to 'llm' (only path in v0.1).
            return [
                {
                    "id": r["id"],
                    "ulid": None,
                    "type": r["type"],
                    "content": r["content"],
                    "anchor_status": "unknown",
                    "anchor_reason": None,
                    "extraction_method": "llm",
                    "llm_logprob_sum": None,
                    "llm_token_count": None,
                }
                for r in rows
            ]

        sql = ("SELECT id, ulid, type, content, anchor_status, anchor_reason, "
               "llm_logprob_sum, llm_token_count, extraction_method FROM extractions")
        conditions: list[str] = []
        params = []
        if type_filter is not None:
            conditions.append("type = ?")
            params.append(type_filter)
        if anchor_status_filter is not None:
            conditions.append("anchor_status = ?")
            params.append(anchor_status_filter)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id"

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "ulid": r["ulid"],
                "type": r["type"],
                "content": r["content"],
                "anchor_status": r["anchor_status"],
                "anchor_reason": r["anchor_reason"],
                "extraction_method": r["extraction_method"],
                "llm_logprob_sum": r["llm_logprob_sum"],
                "llm_token_count": r["llm_token_count"],
            }
            for r in rows
        ]
    finally:
        conn.close()
