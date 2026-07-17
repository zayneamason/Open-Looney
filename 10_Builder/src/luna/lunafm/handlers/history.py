"""
History channel — long-term memory consolidation.

Surveys memory health (duplicates, fading nodes, abandoned topics),
asks a small LLM for ONE recommended action, emits a CONSOLIDATION
artifact. Merge/archive execution is best-effort and only runs when
the LLM recommends it with a clear target.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def survey_memory_health(station, channel, context: dict) -> dict:
    """Pure SQL survey — no LLM call."""
    db = station.db
    if db is None:
        return {"signal": False}

    duplicates = await db.fetchall(
        """
        SELECT n1.id AS id_a, n2.id AS id_b,
               n1.content AS content_a, n2.content AS content_b,
               n1.node_type, n1.lock_in AS lock_in_a, n2.lock_in AS lock_in_b
        FROM memory_nodes n1
        JOIN memory_nodes n2 ON n1.id < n2.id
        WHERE n1.node_type = n2.node_type
          AND length(n1.content) > 20
          AND n1.content LIKE substr(n2.content, 1, 50) || '%'
          AND (n1.source IS NULL OR n1.source NOT LIKE 'lunafm:%')
          AND (n2.source IS NULL OR n2.source NOT LIKE 'lunafm:%')
        LIMIT 5
        """
    )

    fading = await db.fetchall(
        """
        SELECT id, node_type, content, lock_in, access_count,
               last_accessed, created_at
        FROM memory_nodes
        WHERE lock_in < 0.3
          AND access_count > 5
          AND (last_accessed IS NULL OR last_accessed < datetime('now', '-7 days'))
          AND (source IS NULL OR source NOT LIKE 'lunafm:%')
        ORDER BY access_count DESC
        LIMIT 5
        """
    )

    pollution = await db.fetchall(
        """
        SELECT node_type, COUNT(*) AS cnt
        FROM memory_nodes
        WHERE source LIKE 'lunafm:%'
        GROUP BY node_type
        """
    )

    total_signals = len(duplicates) + len(fading)
    if total_signals == 0:
        return {"signal": False}

    return {
        "signal": True,
        "duplicates": [tuple(r) for r in duplicates],
        "fading": [tuple(r) for r in fading],
        "pollution": [tuple(r) for r in pollution],
    }


def _fmt_row_list(rows: list, limit: int = 3) -> str:
    if not rows:
        return "(none)"
    lines = []
    for r in rows[:limit]:
        try:
            # dup rows: id_a, id_b, content_a, content_b, node_type, ...
            if len(r) >= 5 and isinstance(r[2], str):
                lines.append(f"- {r[4]}: {(r[2] or '')[:80]!r} ↔ {(r[3] or '')[:60]!r}")
            elif len(r) >= 3:
                lines.append(f"- {r[1]}: {(r[2] or '')[:100]!r} (access={r[4] if len(r) > 4 else '?'})")
        except Exception:
            lines.append(f"- {r}")
    return "\n".join(lines)


async def analyze_temporal_patterns(station, channel, context: dict) -> dict:
    """LLM call — pick ONE action. Temperature 0.3."""
    llm = station.llm
    if llm is None:
        return {"emit": False}

    prompt = (
        "You are Luna's archivist. You look at the long arc of memory.\n\n"
        f"Duplicate candidates ({len(context.get('duplicates', []))} pairs):\n"
        f"{_fmt_row_list(context.get('duplicates', []))}\n\n"
        f"Fading memories (once important, now decaying):\n"
        f"{_fmt_row_list(context.get('fading', []))}\n\n"
        "Choose ONE action (the most impactful). Output exactly one line:\n\n"
        "MERGE <id_a> <id_b> — these duplicates should become one node\n"
        "ARCHIVE <id> — this memory has served its purpose, lower its lock_in\n"
        "PATTERN <description> — you see a temporal pattern worth noting\n"
        "HEALTHY — nothing needs attention right now"
    )

    from luna.llm.base import Message as LLMMessage

    try:
        result = await llm.complete(
            messages=[
                LLMMessage(role="system", content="You are Luna's archivist. Be precise."),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=0.3,
            max_tokens=150,
            model=station.default_model,
        )
    except Exception as e:
        logger.debug(f"[LUNAFM:history] LLM call failed: {e}")
        return {"emit": False}

    text = (result.content or "").strip()
    if not text or text.upper().startswith("HEALTHY"):
        return {"emit": False}

    action = "PATTERN"
    target_a = target_b = None
    parts = text.split()
    if parts:
        head = parts[0].upper()
        if head in ("MERGE", "ARCHIVE", "PATTERN"):
            action = head
            if head == "MERGE" and len(parts) >= 3:
                target_a, target_b = parts[1], parts[2]
            elif head == "ARCHIVE" and len(parts) >= 2:
                target_a = parts[1]

    return {
        "emit": True,
        "action": action,
        "description": text,
        "target_a": target_a,
        "target_b": target_b,
    }


async def _merge_nodes(db, id_a: str, id_b: str) -> tuple[bool, str, str]:
    """
    Real merge: pick survivor (higher lock_in), rewire every graph_edge from
    the discard to the survivor, drop the discard's lock_in to near zero,
    retag its source.

    Returns (success, survivor_id, discard_id). If anything fails, nothing
    is written — caller can fall back to a PATTERN artifact.
    """
    a = await db.fetchone(
        "SELECT id, lock_in FROM memory_nodes WHERE id = ?", (id_a,)
    )
    b = await db.fetchone(
        "SELECT id, lock_in FROM memory_nodes WHERE id = ?", (id_b,)
    )
    if not a or not b:
        return False, "", ""

    a_lock = a[1] if a[1] is not None else 0.0
    b_lock = b[1] if b[1] is not None else 0.0
    if a_lock >= b_lock:
        survivor, discard = id_a, id_b
    else:
        survivor, discard = id_b, id_a

    # Rewire outgoing edges (from_id = discard → from_id = survivor).
    # UNIQUE(from_id, to_id, relationship) may collide; INSERT OR IGNORE
    # the rewired row, then delete the original.
    outgoing = await db.fetchall(
        "SELECT id, to_id, relationship, strength, created_at, origin, metadata, scope "
        "FROM graph_edges WHERE from_id = ?",
        (discard,),
    )
    for row in outgoing:
        edge_id = row[0]
        to_id, rel, strength, created, origin, metadata, scope = row[1:]
        if to_id == survivor:
            # self-loop after merge — drop it
            await db.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))
            continue
        await db.execute(
            """
            INSERT OR IGNORE INTO graph_edges
                (from_id, to_id, relationship, strength, created_at, origin, metadata, scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (survivor, to_id, rel, strength, created, origin, metadata, scope),
        )
        await db.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))

    incoming = await db.fetchall(
        "SELECT id, from_id, relationship, strength, created_at, origin, metadata, scope "
        "FROM graph_edges WHERE to_id = ?",
        (discard,),
    )
    for row in incoming:
        edge_id = row[0]
        from_id, rel, strength, created, origin, metadata, scope = row[1:]
        if from_id == survivor:
            await db.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))
            continue
        await db.execute(
            """
            INSERT OR IGNORE INTO graph_edges
                (from_id, to_id, relationship, strength, created_at, origin, metadata, scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (from_id, survivor, rel, strength, created, origin, metadata, scope),
        )
        await db.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))

    # Soft-delete the discard
    await db.execute(
        "UPDATE memory_nodes SET lock_in = 0.01, source = 'lunafm:history:merged', updated_at = datetime('now') WHERE id = ?",
        (discard,),
    )
    return True, survivor, discard


async def consolidate_memories(station, channel, context: dict) -> dict:
    """Execute merge/archive. Returns metadata for the emit stage."""
    action = context.get("action")
    db = station.db
    executed = False
    survivor = discard = None

    try:
        if action == "ARCHIVE" and context.get("target_a") and db is not None:
            await db.execute(
                "UPDATE memory_nodes SET lock_in = 0.1, updated_at = datetime('now') "
                "WHERE id = ? AND lock_in > 0.1",
                (context["target_a"],),
            )
            executed = True
        elif (
            action == "MERGE"
            and context.get("target_a")
            and context.get("target_b")
            and db is not None
        ):
            ok, survivor, discard = await _merge_nodes(
                db, context["target_a"], context["target_b"]
            )
            if ok:
                executed = True
                context["survivor"] = survivor
                context["discard"] = discard
            else:
                # Fall back to PATTERN so the emit stage still writes something
                # useful rather than claiming a merge that never happened.
                action = "PATTERN"
    except Exception as e:
        logger.warning(f"[LUNAFM:history] consolidate failed: {e}")
        action = "PATTERN"

    return {
        "emit": True,
        "action": action,
        "description": context.get("description", ""),
        "target_a": context.get("target_a"),
        "target_b": context.get("target_b"),
        "survivor": survivor,
        "discard": discard,
        "executed": executed,
    }


async def write_consolidation(station, channel, context: dict) -> dict:
    """Write a CONSOLIDATION node summarizing the action."""
    description = context.get("description") or f"{context.get('action', 'PATTERN')} cycle"
    node_id = await station.write_artifact(
        channel_id="history",
        node_type="CONSOLIDATION",
        content=description,
        lock_in=0.25,
        importance=0.4,
        metadata={
            "action": context.get("action"),
            "target_a": context.get("target_a"),
            "target_b": context.get("target_b"),
            "survivor": context.get("survivor"),
            "discard": context.get("discard"),
            "executed": context.get("executed", False),
            "channel": "history",
        },
    )
    return {"artifact_written": bool(node_id), "node_id": node_id}
