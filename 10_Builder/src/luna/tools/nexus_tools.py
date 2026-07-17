"""
Nexus Tools — Agentic document search for Luna's AgentLoop.

These tools let the LLM request additional Nexus searches during
the agentic execution loop. When Luna encounters a knowledge gap
mid-generation, she can search for more context.

Tools:
- nexus_search: Search extractions and chunks across collections
- nexus_lookup_section: Find content by chapter/section label
- nexus_get_summary: Get the document summary for a collection
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def nexus_search(
    query: str,
    collection: str = "",
    search_type: str = "hybrid",
    limit: int = 5,
    _engine: Any = None,
) -> dict:
    """
    Search Nexus collections for document knowledge.

    Use this when you need more information from ingested documents
    to answer a question. Searches both extractions (summaries, claims)
    and raw document chunks.
    """
    if not _engine or not hasattr(_engine, "aibrarian") or not _engine.aibrarian:
        return {"error": "Nexus not available", "results": []}

    results = []

    # Determine which collections to search
    if collection:
        collections = [collection]
    else:
        collections = [
            key
            for key, cfg in _engine.aibrarian.registry.collections.items()
            if cfg.enabled and key in _engine.aibrarian.connections
        ]

    for key in collections:
        conn = _engine.aibrarian.connections.get(key)
        if not conn:
            continue

        # Search extractions first
        try:
            from luna.substrate.aibrarian_engine import AiBrarianEngine

            fts_query = AiBrarianEngine._sanitize_fts_query(query)
            ext_rows = conn.conn.execute(
                "SELECT node_type, content, confidence "
                "FROM extractions_fts "
                "JOIN extractions e ON extractions_fts.rowid = e.rowid "
                "WHERE extractions_fts MATCH ? "
                "ORDER BY e.confidence DESC LIMIT ?",
                (fts_query, limit),
            ).fetchall()
            for row in ext_rows:
                results.append(
                    {
                        "content": row[1],
                        "node_type": row[0],
                        "confidence": row[2],
                        "source": f"nexus/{key}",
                    }
                )
        except Exception as e:
            logger.warning(f"[NEXUS-TOOL] Extraction search failed: {e}")

        # Also search chunks if extractions are sparse
        if len(results) < 2:
            try:
                chunk_results = await _engine.aibrarian.search(
                    key, query, search_type, limit=limit
                )
                for r in chunk_results:
                    content = r.get("snippet") or r.get("content", "")
                    if content:
                        results.append(
                            {
                                "content": content,
                                "node_type": "CHUNK",
                                "source": f"nexus/{key}",
                            }
                        )
            except Exception as e:
                logger.warning(f"[NEXUS-TOOL] Chunk search failed: {e}")

    return {
        "query": query,
        "result_count": len(results),
        "results": results[:limit],
    }


async def nexus_lookup_section(
    section_label: str,
    collection: str = "",
    _engine: Any = None,
) -> dict:
    """
    Find content by chapter or section label.

    Use this when you know which section of a document you need
    (e.g., "Chapter 2", "Introduction", "Conclusion").
    """
    if not _engine or not hasattr(_engine, "aibrarian") or not _engine.aibrarian:
        return {"error": "Nexus not available", "results": []}

    results = []
    collections = (
        [collection] if collection else list(_engine.aibrarian.connections.keys())
    )

    for key in collections:
        conn = _engine.aibrarian.connections.get(key)
        if not conn:
            continue

        try:
            rows = conn.conn.execute(
                "SELECT node_type, content, confidence "
                "FROM extractions "
                "WHERE metadata LIKE ? "
                "ORDER BY confidence DESC LIMIT 10",
                (f"%{section_label}%",),
            ).fetchall()
            for row in rows:
                results.append(
                    {
                        "content": row[1],
                        "node_type": row[0],
                        "confidence": row[2],
                        "source": f"nexus/{key}",
                        "section": section_label,
                    }
                )
        except Exception:
            pass

        # Also get chunks tagged with this section
        try:
            rows = conn.conn.execute(
                "SELECT chunk_text, section_label "
                "FROM chunks "
                "WHERE section_label LIKE ? "
                "ORDER BY chunk_index LIMIT 5",
                (f"%{section_label}%",),
            ).fetchall()
            for row in rows:
                results.append(
                    {
                        "content": row[0],
                        "node_type": "CHUNK",
                        "source": f"nexus/{key}",
                        "section": row[1],
                    }
                )
        except Exception:
            pass

    return {
        "section": section_label,
        "result_count": len(results),
        "results": results,
    }


async def nexus_get_summary(
    collection: str = "",
    _engine: Any = None,
) -> dict:
    """
    Get the document summary and table of contents for a collection.

    Use this when you need an overview of what a document covers
    before searching for specific details.
    """
    if not _engine or not hasattr(_engine, "aibrarian") or not _engine.aibrarian:
        return {"error": "Nexus not available", "results": []}

    results = []
    collections = (
        [collection] if collection else list(_engine.aibrarian.connections.keys())
    )

    for key in collections:
        conn = _engine.aibrarian.connections.get(key)
        if not conn:
            continue

        try:
            rows = conn.conn.execute(
                "SELECT node_type, content FROM extractions "
                "WHERE node_type IN ('DOCUMENT_SUMMARY', 'TABLE_OF_CONTENTS') "
                "ORDER BY node_type"
            ).fetchall()
            for row in rows:
                results.append(
                    {
                        "content": row[1],
                        "node_type": row[0],
                        "source": f"nexus/{key}",
                    }
                )
        except Exception:
            pass

    return {
        "result_count": len(results),
        "results": results,
    }


def register_nexus_tools(registry, engine=None) -> None:
    """Register Nexus tools with the AgentLoop's tool registry."""
    from luna.tools.registry import Tool

    async def _search(query: str, collection: str = "", **kwargs):
        return await nexus_search(query, collection, _engine=engine)

    async def _lookup(section_label: str, collection: str = "", **kwargs):
        return await nexus_lookup_section(section_label, collection, _engine=engine)

    async def _summary(collection: str = "", **kwargs):
        return await nexus_get_summary(collection, _engine=engine)

    registry.register(Tool(
        name="nexus_search",
        description=(
            "Search ingested documents for knowledge. Use when you need more "
            "information from books, reports, or other documents to answer a question."
        ),
        execute=_search,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "collection": {"type": "string", "description": "Specific collection to search (optional)"},
            },
            "required": ["query"],
        },
    ))

    registry.register(Tool(
        name="nexus_lookup_section",
        description=(
            "Find content by chapter or section label. Use when you know which "
            "part of a document you need."
        ),
        execute=_lookup,
        parameters={
            "type": "object",
            "properties": {
                "section_label": {"type": "string", "description": "Section to find (e.g., 'CHAPTER TWO', 'Introduction')"},
                "collection": {"type": "string", "description": "Specific collection (optional)"},
            },
            "required": ["section_label"],
        },
    ))

    registry.register(Tool(
        name="nexus_get_summary",
        description=(
            "Get document overview and table of contents. Use before detailed "
            "searches to understand document structure."
        ),
        execute=_summary,
        parameters={
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Which collection (optional)"},
            },
        },
    ))

    logger.info("[NEXUS-TOOLS] Registered 3 Nexus tools for agentic retrieval")
