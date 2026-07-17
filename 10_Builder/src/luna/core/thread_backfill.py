"""
One-shot idempotent migration: THREAD memory_nodes → canonical `threads` table.

THREAD rows in `memory_nodes` carry a JSON payload in `content` with topic,
status, project_slug, entities, open_tasks, and timestamps. Slice 2 of the
TaskManager Board architecture treats `threads` as the canonical source of
truth; THREAD memory_nodes remain as projection artifacts.

This module bridges the two: it walks THREAD nodes, parses the JSON, and
INSERT OR IGNOREs into `threads`. Idempotent by primary key — running twice
is a no-op.

Scope intentionally narrow:
- `threads` metadata only (no `open_tasks → tasks` migration in this pass)
- no Librarian retrofit
- embedded JSON `id` is ignored in favor of memory_node.id (57 rows in live
  data had blank embedded ids; using memory_node.id keeps references stable)
- empty `topic` preserved as '' so the frontend's "Untitled thread" fallback
  is the single source of that UX
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


_VALID_STATUSES = {"active", "parked", "resumed", "closed"}


async def backfill_threads(db: MemoryDatabase) -> Dict[str, Any]:
    """Migrate THREAD memory_nodes → `threads` rows. Idempotent.

    Returns a summary safe to log at info level. Skips rows that would
    collide with existing `threads.id` (INSERT OR IGNORE). Never deletes or
    updates anything in memory_nodes.
    """
    summary: Dict[str, Any] = {
        "threads_scanned": 0,
        "threads_inserted": 0,
        "skipped_existing": 0,
        "skipped_invalid": 0,
        "empty_topic_preserved": 0,
    }

    rows = await db.fetchall(
        "SELECT id, content FROM memory_nodes WHERE node_type = 'THREAD'"
    )
    summary["threads_scanned"] = len(rows)
    if not rows:
        return summary

    existing_rows = await db.fetchall("SELECT id FROM threads")
    existing_ids = {r[0] for r in existing_rows}

    for row in rows:
        node_id = row[0]
        raw = row[1]
        try:
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            summary["skipped_invalid"] += 1
            logger.debug(f"Thread backfill: invalid JSON on node {node_id}")
            continue
        if not isinstance(payload, dict):
            summary["skipped_invalid"] += 1
            continue

        if node_id in existing_ids:
            summary["skipped_existing"] += 1
            continue

        status = payload.get("status") or "active"
        if status not in _VALID_STATUSES:
            status = "active"

        topic = payload.get("topic") or ""
        if not topic:
            summary["empty_topic_preserved"] += 1

        # Metadata_json carries the bits that don't have their own columns:
        # entities list, entity_node_ids, parent_thread_id, study_context_sections.
        metadata_out: Dict[str, Any] = {}
        for k in ("entities", "entity_node_ids", "parent_thread_id",
                  "study_context_sections", "turn_count"):
            if k in payload and payload[k] not in (None, [], ""):
                metadata_out[k] = payload[k]
        metadata_json = json.dumps(metadata_out) if metadata_out else None

        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO threads (
                    id, topic, status, project_slug,
                    started_at, parked_at, resumed_at, closed_at,
                    resume_count, metadata_json
                ) VALUES (
                    ?, ?, ?, ?,
                    COALESCE(?, datetime('now')), ?, ?, ?,
                    ?, ?
                )
                """,
                (
                    node_id,
                    topic,
                    status,
                    payload.get("project_slug"),
                    payload.get("started_at"),
                    payload.get("parked_at"),
                    payload.get("resumed_at"),
                    payload.get("closed_at"),
                    int(payload.get("resume_count") or 0),
                    metadata_json,
                ),
            )
            summary["threads_inserted"] += 1
        except Exception as e:
            summary["skipped_invalid"] += 1
            logger.warning(f"Thread backfill error on node {node_id}: {e}")

    if summary["threads_inserted"]:
        logger.info(
            "Thread backfill: inserted %d threads (scanned=%d, skipped_existing=%d, "
            "empty_topic=%d, invalid=%d)",
            summary["threads_inserted"], summary["threads_scanned"],
            summary["skipped_existing"], summary["empty_topic_preserved"],
            summary["skipped_invalid"],
        )
    return summary
