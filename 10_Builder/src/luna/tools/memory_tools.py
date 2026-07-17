"""
Memory Tools for Luna Engine
=============================

Tools for interacting with Luna's Memory Matrix.
These tools allow Luna to query and store memories programmatically.

Based on Part XIV (Agentic Architecture) of the Luna Engine Bible.
"""

import logging
from typing import Any, Optional

from .registry import Tool

logger = logging.getLogger(__name__)


# =============================================================================
# MEMORY INTERFACE
# =============================================================================

# Global memory matrix reference (set by engine initialization)
_memory_matrix = None


def set_memory_matrix(matrix) -> None:
    """
    Set the global memory matrix reference.

    Called during engine initialization to provide the tools
    access to Luna's memory.

    Args:
        matrix: The MemoryMatrix instance
    """
    global _memory_matrix
    _memory_matrix = matrix
    logger.info("Memory tools connected to MemoryMatrix")


def get_memory_matrix():
    """
    Get the current memory matrix.

    Returns:
        The MemoryMatrix instance, or None if not set
    """
    return _memory_matrix


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

async def memory_query(
    query: str,
    node_type: Optional[str] = None,
    limit: int = 10,
    include_metadata: bool = False,
) -> list[dict]:
    """
    Query Luna's memory for relevant information.

    Args:
        query: Search query text
        node_type: Optional filter by node type (FACT, DECISION, PROBLEM, etc.)
        limit: Maximum number of results to return
        include_metadata: If True, include full metadata in results

    Returns:
        List of matching memory nodes as dicts

    Raises:
        RuntimeError: If memory matrix is not initialized
    """
    if _memory_matrix is None:
        raise RuntimeError("Memory matrix not initialized. Cannot query memories.")

    # Search the memory matrix
    nodes = await _memory_matrix.search_nodes(
        query=query,
        node_type=node_type,
        limit=limit,
    )

    # Convert to simplified format for tool output
    results = []
    for node in nodes:
        result = {
            "id": node.id,
            "type": node.node_type,
            "content": node.content,
            "summary": node.summary,
            "confidence": node.confidence,
            "importance": node.importance,
        }

        if include_metadata:
            result["source"] = node.source
            result["access_count"] = node.access_count
            result["created_at"] = node.created_at.isoformat() if node.created_at else None
            result["metadata"] = node.metadata

        results.append(result)

    logger.debug(f"Memory query '{query}' returned {len(results)} results")
    return results


async def memory_store(
    content: str,
    node_type: str = "FACT",
    source: Optional[str] = None,
    summary: Optional[str] = None,
    confidence: float = 1.0,
    importance: float = 0.5,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Store a new memory in Luna's Memory Matrix.

    Args:
        content: The content/information to store
        node_type: Type of node (FACT, DECISION, PROBLEM, ACTION, CONTEXT, etc.)
        source: Where this came from (e.g., "conversation", "tool", "observation")
        summary: Optional short summary for display
        confidence: Confidence score 0-1 (default 1.0)
        importance: Importance score 0-1 (default 0.5)
        metadata: Optional additional data

    Returns:
        Dict with success status and node ID

    Raises:
        RuntimeError: If memory matrix is not initialized
    """
    if _memory_matrix is None:
        raise RuntimeError("Memory matrix not initialized. Cannot store memory.")

    # Validate node_type
    valid_types = ["FACT", "DECISION", "PROBLEM", "ACTION", "CONTEXT", "OBSERVATION", "GOAL", "NOTE"]
    if node_type not in valid_types:
        logger.warning(f"Unusual node_type '{node_type}' - consider using one of: {valid_types}")

    # Store the memory
    node_id = await _memory_matrix.add_node(
        node_type=node_type,
        content=content,
        source=source or "tool:memory_store",
        summary=summary,
        confidence=confidence,
        importance=importance,
        metadata=metadata,
    )

    logger.debug(f"Stored memory node: {node_id} ({node_type})")

    return {
        "success": True,
        "node_id": node_id,
        "node_type": node_type,
        "content_length": len(content),
    }


async def memory_get_context(
    query: str,
    max_tokens: int = 2000,
    node_types: Optional[list[str]] = None,
) -> list[dict]:
    """
    Get relevant context for a query from memory.

    This is optimized for building LLM context — it respects
    token budgets and returns memories in priority order.

    Args:
        query: Query to find context for
        max_tokens: Maximum tokens worth of context (approximate)
        node_types: Optional list of node types to include

    Returns:
        List of relevant memory nodes within token budget

    Raises:
        RuntimeError: If memory matrix is not initialized
    """
    if _memory_matrix is None:
        raise RuntimeError("Memory matrix not initialized. Cannot get context.")

    nodes = await _memory_matrix.get_context(
        query=query,
        max_tokens=max_tokens,
        node_types=node_types,
    )

    results = []
    for node in nodes:
        results.append({
            "id": node.id,
            "type": node.node_type,
            "content": node.content,
            "summary": node.summary,
            "importance": node.importance,
        })

    logger.debug(f"Got {len(results)} context nodes for query '{query}'")
    return results


async def memory_stats() -> dict:
    """
    Get statistics about Luna's memory.

    Returns:
        Dict with memory statistics

    Raises:
        RuntimeError: If memory matrix is not initialized
    """
    if _memory_matrix is None:
        raise RuntimeError("Memory matrix not initialized. Cannot get stats.")

    stats = await _memory_matrix.get_stats()
    logger.debug(f"Retrieved memory stats: {stats['total_nodes']} total nodes")
    return stats


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

memory_query_tool = Tool(
    name="memory_query",
    description="Search Luna's memory for relevant information. Returns matching memories based on the query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to find relevant memories"
            },
            "node_type": {
                "type": "string",
                "description": "Optional filter by memory type (FACT, DECISION, PROBLEM, ACTION, CONTEXT, etc.)",
                "enum": ["FACT", "DECISION", "PROBLEM", "ACTION", "CONTEXT", "OBSERVATION", "GOAL", "NOTE"]
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 10
            },
            "include_metadata": {
                "type": "boolean",
                "description": "Include full metadata in results",
                "default": False
            }
        },
        "required": ["query"]
    },
    execute=memory_query,
    requires_confirmation=False,
    timeout_seconds=30,
)

memory_store_tool = Tool(
    name="memory_store",
    description="Store a new memory in Luna's Memory Matrix. Use this to remember important information, decisions, or observations.",
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The content/information to store"
            },
            "node_type": {
                "type": "string",
                "description": "Type of memory node",
                "enum": ["FACT", "DECISION", "PROBLEM", "ACTION", "CONTEXT", "OBSERVATION", "GOAL", "NOTE"],
                "default": "FACT"
            },
            "source": {
                "type": "string",
                "description": "Where this information came from"
            },
            "summary": {
                "type": "string",
                "description": "Optional short summary for quick reference"
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score from 0 to 1",
                "default": 1.0
            },
            "importance": {
                "type": "number",
                "description": "Importance score from 0 to 1",
                "default": 0.5
            }
        },
        "required": ["content"]
    },
    execute=memory_store,
    requires_confirmation=False,  # Memory storage is safe
    timeout_seconds=30,
)

memory_context_tool = Tool(
    name="memory_get_context",
    description="Get relevant context from memory for a query. Optimized for building LLM context with token budget awareness.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The query to find context for"
            },
            "max_tokens": {
                "type": "integer",
                "description": "Maximum tokens worth of context",
                "default": 2000
            },
            "node_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of node types to include"
            }
        },
        "required": ["query"]
    },
    execute=memory_get_context,
    requires_confirmation=False,
    timeout_seconds=30,
)

memory_stats_tool = Tool(
    name="memory_stats",
    description="Get statistics about Luna's memory including total nodes, types, and averages.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    execute=memory_stats,
    requires_confirmation=False,
    timeout_seconds=10,
)


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

ALL_MEMORY_TOOLS = [
    memory_query_tool,
    memory_store_tool,
    memory_context_tool,
    memory_stats_tool,
]


def register_memory_tools(registry) -> None:
    """
    Register all memory tools with a ToolRegistry.

    Args:
        registry: The ToolRegistry to register tools with
    """
    for tool in ALL_MEMORY_TOOLS:
        registry.register(tool)
    logger.info(f"Registered {len(ALL_MEMORY_TOOLS)} memory tools")
