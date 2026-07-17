"""
Nexus Promotion
===============

Replaces the post-generation `_bridge_nexus_to_matrix()` patch with deterministic,
synchronous pointer-graph maintenance.

A "promotion" writes one row to `nexus_nodes` in `memory_matrix.lun` and one row
to `nexus_refs` in the satellite `.lun`. Content stays in the satellite — the
master holds only the pointer. Dedup is an exact-match `nexus_refs` lookup
(cheaper and deterministic vs. the prior 0.80 word-overlap heuristic).

Two callsites:
  - Use-time   (engine post-generation): CLAIM, SECTION_SUMMARY
  - Ingest-time (cartridge builder):     DOCUMENT_SUMMARY, TABLE_OF_CONTENTS

See: ClaudeCo-Projects/Project Eclipse/NEXUS_CORTEX_ARCHITECTURE_BRIEF.md (Move 3)
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from luna.substrate.nexus_registry import NexusRegistry

logger = logging.getLogger(__name__)

# Node types valid for promotion. Anything else is dropped silently — keeps
# stray REFERENCE/SOURCE_TEXT from polluting the pointer graph.
PROMOTABLE_NODE_TYPES = frozenset({
    "CLAIM",
    "SECTION_SUMMARY",
    "DOCUMENT_SUMMARY",
    "TABLE_OF_CONTENTS",
})


async def promote_to_nexus(
    *,
    nexus_registry: "NexusRegistry",
    satellite_conn: sqlite3.Connection,
    collection_key: str,
    satellite_node_id: int,
    node_type: str,
) -> Optional[str]:
    """Promote one satellite node into the Nexus pointer graph.

    Returns the `nexus_node_id` (existing or newly created), or None if the
    promotion was rejected or failed. Best-effort — never raises.
    """
    if node_type not in PROMOTABLE_NODE_TYPES:
        return None

    if not collection_key or satellite_node_id is None:
        return None

    try:
        existing = satellite_conn.execute(
            "SELECT nexus_node_id FROM nexus_refs "
            "WHERE local_node_id = ? AND node_type = ?",
            (satellite_node_id, node_type),
        ).fetchone()
        if existing:
            return existing[0]
    except sqlite3.Error as e:
        logger.warning("[NEXUS-PROMOTE] satellite ref check failed: %s", e)
        return None

    nexus_node_id = uuid.uuid4().hex

    try:
        await nexus_registry._db.execute(
            "INSERT INTO nexus_nodes "
            "(nexus_node_id, collection_key, satellite_node_id, node_type) "
            "VALUES (?, ?, ?, ?)",
            (nexus_node_id, collection_key, str(satellite_node_id), node_type),
        )
    except Exception as e:
        logger.warning("[NEXUS-PROMOTE] master pointer insert failed: %s", e)
        return None

    try:
        satellite_conn.execute(
            "INSERT INTO nexus_refs (local_node_id, nexus_node_id, node_type) "
            "VALUES (?, ?, ?)",
            (satellite_node_id, nexus_node_id, node_type),
        )
        satellite_conn.commit()
    except sqlite3.Error as e:
        logger.warning("[NEXUS-PROMOTE] satellite ref insert failed, rolling back master: %s", e)
        try:
            await nexus_registry._db.execute(
                "DELETE FROM nexus_nodes WHERE nexus_node_id = ?",
                (nexus_node_id,),
            )
        except Exception as rollback_err:
            logger.error(
                "[NEXUS-PROMOTE] master rollback failed — orphan pointer %s: %s",
                nexus_node_id, rollback_err,
            )
        return None

    try:
        await nexus_registry.bump_access(collection_key)
    except Exception as e:
        logger.debug("[NEXUS-PROMOTE] bump_access non-fatal: %s", e)

    return nexus_node_id
