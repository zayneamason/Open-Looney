"""
Retrieval Manifest
==================

Builds a lightweight text manifest of available knowledge sources and asks
Haiku to select which ones are relevant to the current query.

Adapted from Claude Code's findRelevantMemories.ts pattern.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_MAX_SOURCES = 20
_MAX_DOC_TITLES = 5
_MAX_SUMMARIES = 3

SELECT_SOURCES_PROMPT = (
    "You are selecting knowledge sources for Luna's retrieval system. "
    "Given a user query and a manifest of available sources, "
    "return ONLY the sources that clearly contain relevant information.\n\n"
    "Rules:\n"
    "- Return up to 5 source keys\n"
    "- Only include collections that clearly match the query topic\n"
    "- If unsure, don't include it\n"
    "- Return ONLY valid JSON: {\"sources\": [\"collection:key1\", \"collection:key2\"]}\n"
    "- Use the exact source keys from the manifest (the text in square brackets)"
)


def build_source_manifest(aibrarian) -> str:
    """Build a text manifest of available knowledge sources.

    Returns a string with one entry per collection, each 1-2 lines.
    Designed to fit in ~500 tokens.
    """
    if not aibrarian:
        return ""

    lines: list[str] = []
    count = 0

    for key, cfg in aibrarian.registry.collections.items():
        if not cfg.enabled:
            continue
        if count >= _MAX_SOURCES:
            break

        conn_obj = aibrarian.connections.get(key)
        if not conn_obj:
            continue

        # Gather stats from the collection database
        try:
            db = conn_obj.conn
            doc_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

            # Get document titles
            title_rows = db.execute(
                "SELECT title FROM documents WHERE title IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (_MAX_DOC_TITLES,),
            ).fetchall()
            titles = [r[0] for r in title_rows if r[0]]

            # Get top extraction summaries if available
            summaries: list[str] = []
            try:
                sum_rows = db.execute(
                    "SELECT content FROM extractions "
                    "WHERE type = 'summary' LIMIT ?",
                    (_MAX_SUMMARIES,),
                ).fetchall()
                summaries = [r[0][:80] for r in sum_rows if r[0]]
            except Exception:
                pass  # extractions table may not exist in all collections

        except Exception as e:
            logger.debug("[MANIFEST] Failed to query collection %s: %s", key, e)
            doc_count = 0
            titles = []
            summaries = []

        # Format entry
        entry = f"- [collection:{key}] {cfg.name} ({doc_count} documents)"
        if titles:
            entry += f" — {', '.join(titles[:3])}"
            if len(titles) > 3:
                entry += f", +{len(titles) - 3} more"
        if summaries:
            entry += f" | Topics: {'; '.join(summaries[:2])}"

        lines.append(entry)
        count += 1

    return "\n".join(lines)


async def select_sources(
    query: str,
    manifest: str,
    backend,
) -> list[str]:
    """Ask Haiku which sources are relevant to this query.

    Returns a list of source keys like ["collection:research_library"].
    Returns empty list on any failure (caller handles fallback).
    """
    if not manifest or not backend:
        return []

    try:
        result = await backend.generate(
            user_message=f"Query: {query}\n\nAvailable sources:\n{manifest}",
            system_prompt=SELECT_SOURCES_PROMPT,
            max_tokens=200,
        )

        raw = result.text.strip()

        # Parse JSON response
        # Handle cases where Haiku wraps in markdown code blocks
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        sources = data.get("sources", [])

        if not isinstance(sources, list):
            logger.warning("[MANIFEST] Haiku returned non-list sources: %s", type(sources))
            return []

        # Validate and return up to 5
        return [s for s in sources[:5] if isinstance(s, str)]

    except json.JSONDecodeError as e:
        logger.warning("[MANIFEST] Failed to parse Haiku response as JSON: %s", e)
        return []
    except Exception as e:
        logger.warning("[MANIFEST] Source selection failed: %s", e)
        return []
