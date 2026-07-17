"""
Matrix Actor - Memory Substrate Interface
==========================================

The Matrix is Luna's long-term memory - her soul lives here.

Powered by Luna Engine's native memory substrate:
- SQLite with WAL mode for concurrent access
- NetworkX graph for relationship traversal
- Lock-in coefficient for memory persistence
- Full-text search via LIKE queries

The engine queries the Matrix directly for context before generation.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, List

from luna.actors.base import Actor
from luna.core.paths import memory_matrix_path
from luna.substrate import MemoryDatabase, MemoryMatrix, MemoryGraph, Turn

logger = logging.getLogger(__name__)


class MatrixActor(Actor):
    """
    Memory substrate actor.

    Manages Luna's long-term memory through:
    - SQLite database with WAL mode
    - MemoryMatrix for CRUD operations
    - MemoryGraph for relationship traversal
    - Lock-in coefficient for memory persistence
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the Matrix actor.

        Args:
            db_path: Path to SQLite database.
                     Defaults to memory_matrix_path() (data/user/luna_engine.db).
        """
        super().__init__(name="matrix")

        if db_path:
            self.db_path = db_path
        else:
            # Default: project data directory
            self.db_path = memory_matrix_path()

        self._db: Optional[MemoryDatabase] = None
        self._matrix: Optional[MemoryMatrix] = None
        self._graph: Optional[MemoryGraph] = None
        self._initialized = False

    @property
    def matrix(self):
        """Access the memory matrix."""
        return self._matrix

    @property
    def graph(self):
        """Access the NetworkX graph."""
        if self._graph:
            return self._graph._graph
        return None

    @property
    def is_ready(self) -> bool:
        """Check if the matrix is initialized."""
        return self._initialized

    async def initialize(self) -> None:
        """Initialize database and load graph."""
        if self._initialized:
            return

        logger.info(f"Matrix actor starting with db: {self.db_path}")

        # Ensure data directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database
        self._db = MemoryDatabase(self.db_path)
        await self._db.connect()

        # Initialize memory matrix
        self._matrix = MemoryMatrix(self._db)

        # Initialize graph
        self._graph = MemoryGraph(self._db)
        await self._graph.load_from_db()

        # Wire graph into matrix for spreading activation in get_context()
        self._matrix.graph = self._graph

        self._initialized = True

        stats = await self._matrix.get_stats()
        graph_stats = await self._graph.get_stats()
        node_count = stats.get('total_nodes', 0)
        edge_count = graph_stats.get('edge_count', 0)
        logger.info(f"Matrix actor ready: {node_count} nodes, {edge_count} edges")

        # Warn if memory looks unusually low (but not on fresh installs)
        if node_count == 0:
            logger.info("Matrix is empty — fresh install or new database.")
        elif node_count < 100:
            logger.warning(
                "Memory node count unusually low (%d). Possible data loss.",
                node_count
            )

    async def start(self) -> None:
        """Start the actor's mailbox processing loop."""
        if not self._initialized:
            await self.initialize()
        await super().start()

    async def stop(self) -> None:
        """Close database connection."""
        await super().stop()

        if self._db:
            await self._db.close()
            self._db = None
            self._matrix = None
            self._graph = None

        self._initialized = False
        logger.info("Matrix actor stopped")

    async def handle(self, msg) -> None:
        # All access is via direct method calls — mailbox unused
        logger.warning(f"MatrixActor: unexpected mailbox message: {msg.type if hasattr(msg, 'type') else msg}")

    # =========================================================================
    # Core Memory Operations
    # =========================================================================

    async def store_memory(
        self,
        content: str,
        node_type: str = "FACT",
        tags: Optional[List[str]] = None,
        confidence: int = 100,
        session_id: Optional[str] = None,
        scope: str = "global",
    ) -> str:
        """
        Store a memory node.

        Args:
            content: The memory content
            node_type: Type (FACT, DECISION, PROBLEM, ASSUMPTION, ACTION, OUTCOME)
            tags: Optional semantic tags (stored in metadata)
            confidence: Confidence level 0-100
            session_id: Optional session ID (stored in metadata)
            scope: Memory scope - 'global' or 'project:{slug}'

        Returns:
            The node ID
        """
        if not self._initialized:
            raise RuntimeError("Matrix not initialized")

        # Build metadata
        metadata = {}
        if tags:
            metadata["tags"] = tags
        if session_id:
            metadata["session_id"] = session_id

        return await self._matrix.add_node(
            node_type=node_type,
            content=content,
            confidence=confidence / 100.0,
            importance=0.5,
            metadata=metadata if metadata else None,
            scope=scope,
        )

    async def store_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        tokens: Optional[int] = None,
    ) -> int:
        """
        Archive a raw conversation turn in the `conversation_turns` table.

        Raw turns are table rows, not graph nodes. Extraction (Scribe ->
        Librarian) is the only path that writes knowledge nodes.

        Returns:
            The conversation_turns row id.
        """
        if not self._initialized:
            raise RuntimeError("Matrix not initialized")

        return await self._matrix.add_conversation_turn(
            session_id=session_id,
            role=role,
            content=content,
            tokens=tokens,
        )

    async def get_recent_turns(
        self,
        session_id: Optional[str] = None,
        limit: int = 10,
        since_minutes: Optional[float] = None,
    ) -> list:
        """Get recent conversation turns from the `conversation_turns` table.

        When `since_minutes` is set, restrict to turns created within the
        rolling window (uses SQLite's `datetime('now', '-N seconds')`, so the
        clock is the DB host's wall clock — same as the `datetime('now')`
        DEFAULT used at insert time).
        """
        if not self._initialized:
            return []

        # Single SQL path so session_id + since_minutes compose cleanly.
        # Bypasses the MemoryMatrix.get_recent_turns delegation when a time
        # window is requested, since that path doesn't accept since_minutes.
        if since_minutes is None and session_id:
            return await self._matrix.get_recent_turns(session_id=session_id, limit=limit)

        conditions: list[str] = []
        params: list = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if since_minutes is not None:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f"-{int(round(since_minutes * 60))} seconds")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT * FROM conversation_turns
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))

        turns = [Turn.from_row(row) for row in rows]
        turns.reverse()
        return turns

    async def get_context(
        self,
        query: str,
        max_tokens: int = 3800,
        budget_preset: str = "balanced",
        scope: Optional[str] = None,
        scopes: Optional[list] = None,
    ) -> str:
        """
        Get relevant context for a query.

        Args:
            query: The query to find context for
            max_tokens: Maximum tokens of context to return
            budget_preset: Ignored (for API compatibility)
            scope: Single scope filter ('global', 'project:slug'). None = all.
            scopes: Multi-scope list for merged context.

        Returns:
            Formatted context string
        """
        if not self._initialized:
            return ""

        # Get memory nodes
        nodes = await self._matrix.get_context(
            query, max_tokens=max_tokens, scope=scope, scopes=scopes
        )

        if not nodes:
            return ""

        # Format as markdown for Luna
        return self._format_context(nodes)

    def _format_context(self, nodes: list) -> str:
        """Format nodes as markdown for Luna."""
        parts = []

        # Group nodes by type
        by_type = {}
        for node in nodes:
            node_type = node.node_type
            if node_type not in by_type:
                by_type[node_type] = []
            by_type[node_type].append(node)

        # Format each type group
        type_order = ["FACT", "DECISION", "PROBLEM", "ACTION", "OUTCOME", "ASSUMPTION"]
        for node_type in type_order:
            if node_type in by_type:
                type_nodes = by_type[node_type]
                section = f"## {node_type}s\n"
                for node in type_nodes[:10]:  # Limit per type
                    # Show lock-in state if settled
                    lock_indicator = " 🔒" if node.lock_in_state == "settled" else ""
                    section += f"- {node.content[:500]}{lock_indicator}\n"
                parts.append(section)

        # Add remaining types
        for node_type, type_nodes in by_type.items():
            if node_type not in type_order:
                section = f"## {node_type}s\n"
                for node in type_nodes[:5]:
                    section += f"- {node.content[:500]}\n"
                parts.append(section)

        result = "\n".join(parts)
        logger.debug(f"Context: {len(nodes)} nodes")
        return result

    async def search(
        self,
        query: str,
        limit: int = 10,
        use_hybrid: bool = True,  # Ignored, for API compatibility
        scope: Optional[str] = None,
    ) -> list:
        """
        Search for memory nodes.

        Args:
            query: Search text
            limit: Max results
            use_hybrid: Ignored (for API compatibility)
            scope: Optional scope filter. None = all scopes.

        Returns:
            List of matching nodes
        """
        if not self._initialized:
            return []

        return await self._matrix.search_nodes(query=query, limit=limit, scope=scope)

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        if not self._initialized:
            return {"initialized": False}

        matrix_stats = await self._matrix.get_stats()
        graph_stats = {"nodes": 0, "edges": 0}

        if self._graph:
            g_stats = await self._graph.get_stats()
            graph_stats = {
                "nodes": g_stats.get("node_count", 0),
                "edges": g_stats.get("edge_count", 0),
            }

        return {
            "backend": "luna_substrate",
            "db_path": str(self.db_path),
            **matrix_stats,
            "graph": graph_stats,
        }

    # =========================================================================
    # Advanced Operations
    # =========================================================================

    async def reinforce_memory(self, node_id: str, amount: int = 1) -> None:
        """
        Reinforce a memory (increases lock-in coefficient).

        Args:
            node_id: Node to reinforce
            amount: Reinforcement amount (each call = +1)
        """
        if not self._matrix:
            return

        for _ in range(amount):
            await self._matrix.reinforce_node(node_id)

        logger.debug(f"Reinforced memory {node_id} by {amount}")

    async def find_related(self, node_id: str, depth: int = 2) -> List[str]:
        """
        Find related nodes via graph traversal.

        Args:
            node_id: Starting node
            depth: How many hops to traverse

        Returns:
            List of related node IDs
        """
        if not self._graph:
            return []

        return self._graph.get_neighbors(node_id, depth=depth)

    async def get_central_concepts(self, limit: int = 10) -> list:
        """
        Get most central/influential memory nodes.

        Returns:
            List of (node_id, score) tuples
        """
        if not self._graph:
            return []

        return self._graph.get_central_nodes(limit=limit)

    # =========================================================================
    # Additional Methods for API Compatibility
    # =========================================================================

    async def add_node(
        self,
        node_type: str,
        content: str,
        source: Optional[str] = None,
        confidence: float = 1.0,
        importance: float = 0.5,
        scope: str = "global",
    ) -> str:
        """Add a memory node (direct passthrough to matrix)."""
        if not self._matrix:
            raise RuntimeError("Matrix not initialized")
        return await self._matrix.add_node(
            node_type=node_type,
            content=content,
            source=source,
            confidence=confidence,
            importance=importance,
            scope=scope,
        )

    async def get_node(self, node_id: str):
        """Get a node by ID (direct passthrough to matrix)."""
        if not self._matrix:
            return None
        return await self._matrix.get_node(node_id)
