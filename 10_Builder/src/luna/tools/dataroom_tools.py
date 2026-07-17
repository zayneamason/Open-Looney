"""
Data Room Tools for Luna Engine
=================================

Tools for searching and querying the investor data room.

Primary backend: AiBrarian Engine (hybrid FTS5 + semantic search via sqlite-vec).
Fallback: MemoryMatrix DOCUMENT nodes (legacy, if AiBrarian is unavailable).
"""

import logging
from pathlib import Path
from typing import Optional

from luna.core.paths import project_root
from .registry import Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine-owned AiBrarian instance — set by set_engine() from server.py startup
# ---------------------------------------------------------------------------

_engine = None
_engine_failed = False  # avoid retrying if standalone fallback failed
_COLLECTION_KEY = None  # auto-set from registry; overridden by set_collection_key()


def set_engine(engine_instance):
    """Attach the Engine-owned AiBrarianEngine. Called once at startup."""
    global _engine, _COLLECTION_KEY
    _engine = engine_instance
    # Auto-set default collection key from first connected collection
    if _COLLECTION_KEY is None and hasattr(engine_instance, 'connections') and engine_instance.connections:
        _COLLECTION_KEY = next(iter(engine_instance.connections.keys()))
        logger.info("Auto-set collection key to '%s'", _COLLECTION_KEY)


def set_collection_key(key: str):
    """Override the default collection key used for dataroom queries."""
    global _COLLECTION_KEY
    _COLLECTION_KEY = key


async def _get_engine():
    """Get the Engine-owned AiBrarianEngine instance.

    Falls back to standalone initialization only if the Engine hasn't
    booted yet (e.g. tools loaded before Engine boot).
    """
    global _engine, _engine_failed
    if _engine is not None:
        return _engine
    if _engine_failed:
        return None

    try:
        from luna.substrate.aibrarian_engine import AiBrarianEngine

        _root = project_root()
        registry_path = _root / "config" / "aibrarian_registry.yaml"

        if not registry_path.exists():
            logger.warning("AiBrarian registry not found at %s", registry_path)
            _engine_failed = True
            return None

        engine = AiBrarianEngine(registry_path, project_root=_root)
        await engine.initialize()
        _engine = engine
        # Auto-set default collection key from first connected collection
        if _COLLECTION_KEY is None and hasattr(engine, 'connections') and engine.connections:
            _auto_key = next(iter(engine.connections.keys()))
            set_collection_key(_auto_key)
            logger.info("Auto-set collection key to '%s'", _auto_key)
        logger.warning("AiBrarian engine fallback: standalone init (Engine not booted)")
        return _engine
    except Exception as e:
        logger.warning("Failed to initialize AiBrarian engine: %s", e)
        _engine_failed = True
        return None


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def dataroom_search(
    query: str,
    category: Optional[str] = None,
    search_type: str = "hybrid",
    limit: int = 10,
) -> list[dict]:
    """
    Search data room documents using hybrid keyword + semantic search.

    Args:
        query: Search text
        category: Optional category filter
        search_type: "hybrid", "keyword", or "semantic"
        limit: Maximum results

    Returns:
        List of matching document dicts with titles, snippets, and scores
    """
    engine = await _get_engine()

    if engine is not None:
        try:
            # Fan out across all connected collections, not just _COLLECTION_KEY
            collection_keys = list(engine.connections.keys()) if hasattr(engine, 'connections') and engine.connections else [_COLLECTION_KEY]
            all_results = []
            for coll_key in collection_keys:
                try:
                    results = await engine.search(coll_key, query, search_type, limit * 2)
                    for r in results:
                        r["_collection"] = coll_key
                    all_results.extend(results)
                except Exception as coll_err:
                    logger.debug("Search in '%s' failed: %s", coll_key, coll_err)

            # Sort merged results by score descending
            all_results.sort(key=lambda r: r.get("score", 0), reverse=True)

            # Apply category filter if requested
            filtered = []
            for r in all_results:
                if category and r.get("category") and category.lower() not in r["category"].lower():
                    continue
                filtered.append({
                    "id": r.get("doc_id", ""),
                    "name": r.get("title") or r.get("filename", ""),
                    "category": r.get("category", ""),
                    "snippet": r.get("snippet", ""),
                    "score": r.get("score", 0),
                    "search_type": r.get("search_type", search_type),
                    "collection": r.get("_collection", _COLLECTION_KEY),
                })
                if len(filtered) >= limit:
                    break

            # Enrich top results with more content so Luna can answer directly
            for entry in filtered[:3]:
                coll = entry.get("collection", _COLLECTION_KEY)
                doc = await engine.get_document(coll, entry["id"])
                if doc and doc.get("full_text"):
                    text = doc["full_text"]
                    entry["content"] = text[:2000]
                    if len(text) > 2000:
                        entry["content"] += f"\n[... {len(text):,} chars total]"

            logger.debug("dataroom_search '%s' via AiBrarian → %d results across %d collections", query, len(filtered), len(collection_keys))
            return filtered
        except Exception as e:
            logger.warning("AiBrarian search failed, falling back to MemoryMatrix: %s", e)

    # Fallback: MemoryMatrix (legacy)
    return await _memorymatrix_search(query, category, limit)


async def _memorymatrix_search(
    query: str, category: Optional[str], limit: int
) -> list[dict]:
    """Legacy fallback: search MemoryMatrix DOCUMENT nodes."""
    import json
    from .memory_tools import get_memory_matrix

    matrix = get_memory_matrix()
    if matrix is None:
        return [{"error": "No search backend available (AiBrarian failed, MemoryMatrix not initialized)"}]

    nodes = await matrix.search_nodes(query=query, node_type="DOCUMENT", limit=limit * 3)
    results = []
    for node in nodes:
        meta = node.metadata if isinstance(node.metadata, dict) else (
            json.loads(node.metadata) if node.metadata else {}
        )
        if category and meta.get("category") != category:
            continue
        results.append({
            "id": node.id,
            "name": node.summary or node.content,
            "category": meta.get("category"),
            "snippet": (node.content or "")[:200],
            "score": node.importance or 0,
            "search_type": "memory_matrix",
        })
        if len(results) >= limit:
            break
    return results


async def dataroom_status() -> dict:
    """
    Get data room statistics: total documents, chunks, words, and category breakdown.

    Returns:
        Dict with collection stats
    """
    engine = await _get_engine()

    if engine is not None:
        try:
            collection_keys = list(engine.connections.keys()) if hasattr(engine, 'connections') and engine.connections else ([_COLLECTION_KEY] if _COLLECTION_KEY else [])
            if not collection_keys:
                return {"error": "No collections available", "backend": "aibrarian"}

            combined = {"total_documents": 0, "total_chunks": 0, "total_words": 0, "by_category": {}, "backend": "aibrarian", "collections": []}
            for coll_key in collection_keys:
                try:
                    stats = await engine.stats(coll_key)
                    combined["total_documents"] += stats.get("documents", 0)
                    combined["total_chunks"] += stats.get("chunks", 0)
                    combined["total_words"] += stats.get("total_words", 0)
                    combined["collections"].append({"key": coll_key, **stats})

                    doc_list = await engine.list_documents(coll_key, 0, 500)
                    for doc in doc_list.get("documents", []):
                        cat = doc.get("category", "Unknown")
                        combined["by_category"][cat] = combined["by_category"].get(cat, 0) + 1
                except Exception as coll_err:
                    logger.debug("Stats for '%s' failed: %s", coll_key, coll_err)

            return combined
        except Exception as e:
            logger.warning("AiBrarian stats failed: %s", e)

    # Fallback: MemoryMatrix
    import json
    from .memory_tools import get_memory_matrix

    matrix = get_memory_matrix()
    if matrix is None:
        return {"error": "No backend available"}

    docs = await matrix.get_nodes_by_type("DOCUMENT", limit=1000)
    category_counts = {}
    for doc in docs:
        meta = doc.metadata if isinstance(doc.metadata, dict) else (
            json.loads(doc.metadata) if doc.metadata else {}
        )
        cat = meta.get("category", "Unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return {
        "total_documents": len(docs),
        "by_category": category_counts,
        "backend": "memory_matrix",
    }


async def dataroom_recent(days: int = 7) -> list[dict]:
    """
    Get recently added data room documents.

    Args:
        days: Look back this many days (default 7)

    Returns:
        List of recently added document dicts, newest first
    """
    engine = await _get_engine()

    if engine is not None:
        try:
            collection_keys = list(engine.connections.keys()) if hasattr(engine, 'connections') and engine.connections else ([_COLLECTION_KEY] if _COLLECTION_KEY else [])
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=days)
            recent = []
            for coll_key in collection_keys:
                try:
                    doc_list = await engine.list_documents(coll_key, 0, 50)
                    for doc in doc_list.get("documents", []):
                        created = doc.get("created_at", "")
                        if created:
                            try:
                                doc_time = datetime.fromisoformat(created)
                                if doc_time < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass
                        recent.append({
                            "id": doc.get("id", ""),
                            "name": doc.get("title") or doc.get("filename", ""),
                            "category": doc.get("category", ""),
                            "word_count": doc.get("word_count", 0),
                            "collection": coll_key,
                        })
                except Exception as coll_err:
                    logger.debug("List for '%s' failed: %s", coll_key, coll_err)

            return recent
        except Exception as e:
            logger.warning("AiBrarian list failed: %s", e)

    # Fallback: MemoryMatrix
    import json
    from datetime import datetime, timedelta
    from .memory_tools import get_memory_matrix

    matrix = get_memory_matrix()
    if matrix is None:
        return []

    docs = await matrix.get_nodes_by_type("DOCUMENT", limit=1000)
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for doc in docs:
        meta = doc.metadata if isinstance(doc.metadata, dict) else (
            json.loads(doc.metadata) if doc.metadata else {}
        )
        last_synced = meta.get("last_synced")
        if not last_synced:
            continue
        try:
            sync_time = datetime.fromisoformat(last_synced)
        except (ValueError, TypeError):
            continue
        if sync_time >= cutoff:
            recent.append({
                "id": doc.id,
                "name": doc.summary or doc.content,
                "category": meta.get("category"),
                "synced_at": last_synced,
            })

    recent.sort(key=lambda x: x.get("synced_at", ""), reverse=True)
    return recent


async def dataroom_read_document(
    doc_id: str,
    max_chars: int = 4000,
) -> dict:
    """
    Read the full contents of a data room document by its ID.

    Use this after dataroom_search to read a specific document in detail.

    Args:
        doc_id: Document ID (from search results)
        max_chars: Maximum characters to return (default 4000, max 12000)

    Returns:
        Dict with document metadata and full text content
    """
    max_chars = min(max_chars, 12000)

    engine = await _get_engine()
    if engine is None:
        return {"error": "AiBrarian engine not available"}

    try:
        # Try all connected collections until the document is found
        collection_keys = list(engine.connections.keys()) if hasattr(engine, 'connections') and engine.connections else ([_COLLECTION_KEY] if _COLLECTION_KEY else [])
        doc = None
        for coll_key in collection_keys:
            try:
                doc = await engine.get_document(coll_key, doc_id)
                if doc:
                    break
            except Exception:
                continue

        if not doc:
            return {"error": f"Document '{doc_id}' not found in any collection"}

        full_text = doc.get("full_text", "")
        truncated = len(full_text) > max_chars
        text = full_text[:max_chars]
        if truncated:
            text += f"\n\n[... truncated — {len(full_text):,} chars total, showing first {max_chars:,}]"

        return {
            "id": doc["id"],
            "title": doc.get("title") or doc.get("filename", ""),
            "category": doc.get("category", ""),
            "word_count": doc.get("word_count", 0),
            "source_path": doc.get("source_path", ""),
            "content": text,
            "truncated": truncated,
        }
    except Exception as e:
        logger.warning("dataroom_read_document failed: %s", e)
        return {"error": str(e)}


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

dataroom_search_tool = Tool(
    name="dataroom_search",
    description=(
        "Search the investor data room for documents by keyword or topic. "
        "Uses hybrid search (keyword + semantic) to find the most relevant documents. "
        "Returns document titles, snippets, and relevance scores."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text — a question, topic, or keywords to find matching documents",
            },
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g. 'Financials', 'Legal', 'Technical')",
            },
            "search_type": {
                "type": "string",
                "description": "Search mode: 'hybrid' (keyword + semantic, default), 'keyword' (exact match), or 'semantic' (meaning-based)",
                "enum": ["hybrid", "keyword", "semantic"],
                "default": "hybrid",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results",
                "default": 10,
            },
        },
        "required": ["query"],
    },
    execute=dataroom_search,
    requires_confirmation=False,
    timeout_seconds=30,
)

dataroom_status_tool = Tool(
    name="dataroom_status",
    description="Get data room statistics: total documents, chunks, words, and breakdown by category.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    execute=dataroom_status,
    requires_confirmation=False,
    timeout_seconds=15,
)

dataroom_recent_tool = Tool(
    name="dataroom_recent",
    description="Get recently added data room documents.",
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Look back this many days",
                "default": 7,
            },
        },
        "required": [],
    },
    execute=dataroom_recent,
    requires_confirmation=False,
    timeout_seconds=15,
)


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

dataroom_read_document_tool = Tool(
    name="dataroom_read_document",
    description=(
        "Read the full contents of a specific data room document by ID. "
        "Use this after dataroom_search to get the complete text of a document. "
        "The doc_id comes from the 'id' field in search results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "Document ID (UUID from search results)",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 4000, max 12000)",
                "default": 4000,
            },
        },
        "required": ["doc_id"],
    },
    execute=dataroom_read_document,
    requires_confirmation=False,
    timeout_seconds=15,
)

ALL_DATAROOM_TOOLS = [
    dataroom_search_tool,
    dataroom_status_tool,
    dataroom_recent_tool,
    dataroom_read_document_tool,
]


def register_dataroom_tools(registry) -> None:
    """Register all data room tools with a ToolRegistry."""
    for tool in ALL_DATAROOM_TOOLS:
        registry.register(tool)
    logger.info("Registered %d dataroom tools (AiBrarian-backed)", len(ALL_DATAROOM_TOOLS))
