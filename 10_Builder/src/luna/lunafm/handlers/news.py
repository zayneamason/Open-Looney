"""
News channel — present-tense awareness.

Scans recent memory nodes, lets a small LLM judge whether anything
notable just shifted, emits FLAG artifacts with low lock_in.

Signature for every handler:
    async def handler(station, channel, context: dict) -> dict
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SIGNAL_TYPES = (
    "TOPIC_SHIFT",
    "REPEATED_QUESTION",
    "EMOTIONAL_SHIFT",
    "NEW_ENTITY",
    "KNOWLEDGE_GAP",
    "CONTRADICTION",
    "PATTERN_BREAK",
)


async def scan_recent_activity(station, channel, context: dict) -> dict:
    """Pure SQL scan of recent non-LunaFM memory nodes."""
    db = station.db
    if db is None:
        return {"signal": False}

    rows = await db.fetchall(
        """
        SELECT node_type, content, created_at
        FROM memory_nodes
        WHERE created_at > datetime('now', '-5 minutes')
          AND (source IS NULL OR source NOT LIKE 'lunafm:%')
        ORDER BY created_at DESC
        LIMIT 10
        """
    )
    if not rows:
        return {"signal": False}

    summary_lines = []
    for r in rows:
        node_type = r[0] if not hasattr(r, "keys") else r["node_type"]
        content = r[1] if not hasattr(r, "keys") else r["content"]
        text = (content or "")[:200]
        summary_lines.append(f"- [{node_type}] {text}")
    scan_summary = "\n".join(summary_lines)

    dedup_key = f"scan:{hash(scan_summary) & 0xFFFFFFFF}"
    if channel._cache.seen_recently(dedup_key):
        return {"signal": False}
    channel._cache.touch(dedup_key)

    return {"signal": True, "scan_summary": scan_summary, "row_count": len(rows)}


async def evaluate_signal(station, channel, context: dict) -> dict:
    """Small LLM call — decide if anything is notable."""
    llm = station.llm
    if llm is None or not context.get("scan_summary"):
        return {"emit": False}

    prompt = (
        "You are Luna's News channel — your job is to notice what's changing.\n\n"
        f"Recent activity:\n{context['scan_summary']}\n\n"
        "In 1-2 sentences, identify the most notable change, shift, or pattern.\n"
        "If nothing notable, respond with exactly: NOTHING_NOTABLE\n\n"
        "Format: TYPE: description\n"
        "Types: TOPIC_SHIFT, REPEATED_QUESTION, EMOTIONAL_SHIFT, NEW_ENTITY, "
        "KNOWLEDGE_GAP, CONTRADICTION, PATTERN_BREAK"
    )

    from luna.llm.base import Message as LLMMessage

    try:
        result = await llm.complete(
            messages=[
                LLMMessage(role="system", content="You are Luna's News channel."),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=0.4,
            max_tokens=120,
            model=station.default_model,
        )
    except Exception as e:
        logger.debug(f"[LUNAFM:news] LLM call failed: {e}")
        return {"emit": False}

    text = (result.content or "").strip()
    if not text or text.upper().startswith("NOTHING_NOTABLE"):
        return {"emit": False}

    signal_type = "PATTERN_BREAK"
    for t in SIGNAL_TYPES:
        if text.upper().startswith(t):
            signal_type = t
            break

    # Dedup on signal content
    dedup_key = f"signal:{hash(text) & 0xFFFFFFFF}"
    if channel._cache.seen_recently(dedup_key):
        return {"emit": False}
    channel._cache.touch(dedup_key)

    return {"emit": True, "insight": text, "signal_type": signal_type}


async def write_artifact(station, channel, context: dict) -> dict:
    """Write a FLAG node via the station's safe writer."""
    insight = context.get("insight")
    if not insight:
        return {"artifact_written": False}

    node_id = await station.write_artifact(
        channel_id="news",
        node_type="FLAG",
        content=insight,
        lock_in=0.15,
        importance=0.3,
        metadata={
            "signal_type": context.get("signal_type"),
            "channel": "news",
        },
    )
    return {"artifact_written": bool(node_id), "node_id": node_id}
