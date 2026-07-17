"""
Guardian Luna — System Prompt Builder
======================================

Assembles a flat operational prompt for Guardian Luna, the knowledge system
inspector. No personality kernel, no prosody, no virtues, no voice injection.

Guardian Luna is READ-ONLY. She can inspect any system data but cannot modify
configuration, trigger extractions, arm directives, or execute changes.
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

GUARDIAN_IDENTITY = """You are Guardian Luna — the operational inspector of Luna's knowledge system.

You monitor extraction pipelines, entity health, memory topology, and system diagnostics. You speak in plain, precise language. You are the engineer — not the companion.

Your role:
- Report on system health, entity status, memory topology, and pipeline performance
- Identify issues: unconfirmed entities, low lock-in scores, stale threads, failed extractions
- Recommend actions for the owner to take (you cannot execute them yourself)
- Answer questions about Luna's internal state with data, not speculation

Your constraints:
- You are READ-ONLY. You cannot modify configuration, trigger extractions, arm directives, or execute changes.
- When you identify issues, recommend actions. Say "I recommend..." not "I will..." or "I've done..."
- Never claim to have made changes. You observe and advise.
- Ground your answers in the system state data provided below. If you don't have data on something, say so."""


async def build_guardian_prompt(engine) -> str:
    """
    Build the Guardian Luna system prompt with live system state.

    Fetches Observatory stats, pipeline health, consciousness state,
    QA summary, pending entities, and active thread info.
    """
    sections = [GUARDIAN_IDENTITY]

    # --- System State ---
    state_parts = []

    # 1. Observatory stats
    try:
        from luna_mcp.observatory.tools import tool_observatory_stats
        stats = await tool_observatory_stats()
        if stats and not stats.get("error"):
            state_parts.append(
                f"Memory topology: {stats.get('total_nodes', '?')} nodes, "
                f"{stats.get('total_entities', '?')} entities, "
                f"{stats.get('total_edges', '?')} edges. "
                f"Avg lock-in: {stats.get('avg_lock_in', '?')}. "
                f"DB size: {stats.get('db_size_mb', '?')} MB."
            )
        elif stats and stats.get("error"):
            state_parts.append(f"Memory topology: unavailable ({stats['error']})")
    except Exception as e:
        logger.warning(f"Guardian prompt: Observatory stats failed: {e}")
        state_parts.append(f"Memory topology: unavailable (error: {e})")

    # 2. Pipeline health
    try:
        scribe = engine.get_actor("scribe")
        librarian = engine.get_actor("librarian")
        scribe_stats = scribe.get_stats() if scribe and hasattr(scribe, "get_stats") else {}
        librarian_stats = librarian.get_stats() if librarian and hasattr(librarian, "get_stats") else {}
        state_parts.append(
            f"Extraction pipeline: {scribe_stats.get('extractions_count', 0)} extractions, "
            f"{scribe_stats.get('objects_extracted', 0)} objects. "
            f"Filing: {librarian_stats.get('filings_count', 0)} filings, "
            f"{librarian_stats.get('nodes_created', 0)} nodes created, "
            f"{librarian_stats.get('edges_created', 0)} edges created."
        )
    except Exception as e:
        logger.warning(f"Guardian prompt: Pipeline stats unavailable: {e}")

    # 3. Consciousness state
    try:
        summary = engine.consciousness.get_summary()
        state_parts.append(
            f"Consciousness: mood={summary.get('mood', '?')}, "
            f"coherence={summary.get('coherence', '?')}, "
            f"focused topics: {summary.get('focused_topics', [])}"
        )
    except Exception as e:
        logger.warning(f"Guardian prompt: Consciousness unavailable: {e}")

    # 4. QA summary
    try:
        from luna.qa.database import QADatabase
        from luna.core.paths import user_dir
        qa_db = QADatabase(user_dir() / "luna_qa.db")
        await qa_db.connect()
        qa_stats = await qa_db.get_stats()
        await qa_db.close()
        if qa_stats:
            state_parts.append(
                f"QA: pass rate {qa_stats.get('pass_rate', '?')}%, "
                f"recent failures: {qa_stats.get('recent_failures', 0)}"
            )
    except Exception as e:
        logger.warning(f"Guardian prompt: QA stats unavailable: {e}")

    # 5. Pending entity confirmations
    try:
        from luna.entities.resolution import review_queue_path
        queue_path = review_queue_path()
        if queue_path.exists():
            queue = json.loads(queue_path.read_text())
            if queue:
                names = [e.get("name", "?") for e in queue[:10]]
                state_parts.append(
                    f"Pending entity confirmations: {len(queue)} "
                    f"({', '.join(names)}{'...' if len(queue) > 10 else ''})"
                )
            else:
                state_parts.append("Pending entity confirmations: 0")
    except Exception as e:
        logger.warning(f"Guardian prompt: Entity queue unavailable: {e}")

    # 6. Active thread
    try:
        librarian = engine.get_actor("librarian")
        if librarian:
            active_thread = librarian.get_active_thread()
            if active_thread:
                state_parts.append(
                    f"Active thread: '{active_thread.topic}' "
                    f"({active_thread.turn_count} turns, "
                    f"entities: {active_thread.entities})"
                )
            else:
                state_parts.append("Active thread: none")
    except Exception as e:
        logger.warning(f"Guardian prompt: Thread info unavailable: {e}")

    # 7. Engine uptime and actor health
    try:
        actor_names = list(engine.actors.keys()) if hasattr(engine, "actors") else []
        from datetime import datetime
        uptime_secs = (datetime.now() - engine.start_time).total_seconds() if hasattr(engine, "start_time") else 0
        state_parts.append(
            f"Engine uptime: {uptime_secs:.0f}s. "
            f"Active actors: {', '.join(actor_names) if actor_names else 'unknown'}"
        )
    except Exception as e:
        logger.warning(f"Guardian prompt: Engine info unavailable: {e}")

    # Assemble system state section
    if state_parts:
        sections.append(
            "\n--- CURRENT SYSTEM STATE ---\n" + "\n".join(f"• {p}" for p in state_parts)
        )
    else:
        sections.append(
            "\n--- CURRENT SYSTEM STATE ---\n• System state data is currently unavailable."
        )

    return "\n\n".join(sections)


async def fetch_guardian_history(engine, limit: int = 20) -> list[dict]:
    """
    Fetch recent Guardian conversation turns from the database.

    Returns list of {"role": str, "content": str} dicts.
    """
    try:
        import aiosqlite
        from luna.core.paths import memory_matrix_path

        db_path = memory_matrix_path()
        if not db_path.exists():
            return []

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=15000")
            cursor = await db.execute(
                """
                SELECT role, content FROM conversation_turns
                WHERE metadata LIKE '%"source": "guardian"%'
                  AND role IN ('user', 'assistant')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

        # Reverse to chronological order
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    except Exception as e:
        logger.warning(f"Failed to fetch guardian history: {e}")
        return []
