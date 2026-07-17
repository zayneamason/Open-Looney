"""Conversation tools — time-windowed introspection of conversation_turns.

Surfaces the canonical engine.get_recent_turns() API to the AgentLoop so
the LLM can answer meta-questions like "how many times has X come up in
the last 5 minutes?" without confabulating from working context alone.

Backed by the same conversation_turns table both modalities write to via
record_conversation_turn — so results are modality-agnostic.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .registry import Tool

logger = logging.getLogger(__name__)


async def count_recent_mentions(
    term: str,
    since_minutes: float = 5.0,
    role: Optional[str] = None,
    _engine: Any = None,
) -> dict:
    """Count occurrences of `term` in conversation turns within a time window.

    Case-insensitive substring match. Returns total occurrences (a single
    turn saying the term twice counts as 2) plus turn-level breakdown.
    """
    if _engine is None or not hasattr(_engine, "get_recent_turns"):
        return {"error": "engine not available", "term": term, "total": 0}

    # Pull a generous slice; the engine API caps via since_minutes filter.
    turns = await _engine.get_recent_turns(limit=500, since_minutes=since_minutes)

    if role and role in {"user", "assistant", "system"}:
        turns = [t for t in turns if t.get("role") == role]

    needle = term.lower()
    pattern = re.compile(re.escape(needle))
    total = 0
    turns_with_hit = 0
    per_turn: list[dict] = []
    for t in turns:
        content = (t.get("content") or "").lower()
        n = len(pattern.findall(content))
        if n:
            turns_with_hit += 1
            total += n
            per_turn.append({
                "role": t.get("role"),
                "occurrences": n,
                "preview": (t.get("content") or "")[:120],
            })

    logger.info(
        "[count_recent_mentions] term=%r since=%.1fmin role=%s "
        "turns_scanned=%d turns_with_hit=%d total_occurrences=%d",
        term, since_minutes, role or "any", len(turns), turns_with_hit, total,
    )
    return {
        "term": term,
        "since_minutes": since_minutes,
        "role_filter": role,
        "turns_scanned": len(turns),
        "turns_with_hit": turns_with_hit,
        "total_occurrences": total,
        "matches": per_turn,
    }


async def search_recent_turns(
    query: str = "",
    since_minutes: float = 30.0,
    role: Optional[str] = None,
    limit: int = 50,
    _engine: Any = None,
) -> dict:
    """Return conversation turns within a time window, optionally filtered
    by case-insensitive substring `query`. When `query` is empty, returns
    all turns in window (use sparingly with `limit`)."""
    if _engine is None or not hasattr(_engine, "get_recent_turns"):
        return {"error": "engine not available", "results": []}

    turns = await _engine.get_recent_turns(limit=limit, since_minutes=since_minutes)
    if role and role in {"user", "assistant", "system"}:
        turns = [t for t in turns if t.get("role") == role]

    if query:
        needle = query.lower()
        turns = [t for t in turns if needle in (t.get("content") or "").lower()]

    return {
        "query": query,
        "since_minutes": since_minutes,
        "role_filter": role,
        "count": len(turns),
        "results": turns,
    }


def register_conversation_tools(registry, engine=None) -> None:
    """Register conversation introspection tools with AgentLoop's registry."""

    async def _count(term: str, since_minutes: float = 5.0, role: Optional[str] = None, **_):
        return await count_recent_mentions(term, since_minutes, role, _engine=engine)

    async def _search(query: str = "", since_minutes: float = 30.0, role: Optional[str] = None, limit: int = 50, **_):
        return await search_recent_turns(query, since_minutes, role, limit, _engine=engine)

    registry.register(Tool(
        name="count_recent_mentions",
        description=(
            "Count how many times a word or phrase appears in conversation "
            "turns within a recent time window. Use for meta-questions like "
            "'how many times have I said X in the last N minutes?' Returns "
            "total occurrences and per-turn breakdown."
        ),
        execute=_count,
        parameters={
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "Word or phrase to count (case-insensitive substring match)",
                },
                "since_minutes": {
                    "type": "number",
                    "description": "Rolling window in minutes (default 5)",
                    "default": 5.0,
                },
                "role": {
                    "type": "string",
                    "description": "Filter by speaker: 'user', 'assistant', or omit for both",
                    "enum": ["user", "assistant", "system"],
                },
            },
            "required": ["term"],
        },
        requires_confirmation=False,
        timeout_seconds=10,
    ))

    registry.register(Tool(
        name="search_recent_turns",
        description=(
            "Return conversation turns within a recent time window, "
            "optionally filtered by substring. Use when you need to "
            "introspect what was actually said recently rather than rely "
            "on in-context working memory."
        ),
        execute=_search,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to filter by (omit to get all turns in window)",
                },
                "since_minutes": {
                    "type": "number",
                    "description": "Rolling window in minutes (default 30)",
                    "default": 30.0,
                },
                "role": {
                    "type": "string",
                    "description": "Filter by speaker",
                    "enum": ["user", "assistant", "system"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max turns to return",
                    "default": 50,
                },
            },
            "required": [],
        },
        requires_confirmation=False,
        timeout_seconds=10,
    ))
