"""
Memory Graph for Luna Engine
============================

NetworkX graph layer for Luna's Memory Matrix.

The graph maintains relationships between memory nodes:
- In-memory graph (NetworkX MultiDiGraph) for fast traversal
- SQLite database for persistence
- Spreading activation for relevance calculation

Relationship types:
- DEPENDS_ON: Node A requires Node B
- RELATES_TO: General semantic relationship
- CAUSED_BY: Node A was caused by Node B
- FOLLOWED_BY: Temporal sequence
- CONTRADICTS: Node A contradicts Node B
- SUPPORTS: Node A provides evidence for Node B
- BELONGS_TO: Node A belongs to category/group Node B
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional, Protocol
import logging

import networkx as nx

if TYPE_CHECKING:
    from .database import MemoryDatabase

logger = logging.getLogger(__name__)


class RelationshipType(str, Enum):
    """Types of relationships between memory nodes."""
    DEPENDS_ON = "DEPENDS_ON"
    RELATES_TO = "RELATES_TO"
    CAUSED_BY = "CAUSED_BY"
    FOLLOWED_BY = "FOLLOWED_BY"
    CONTRADICTS = "CONTRADICTS"
    SUPPORTS = "SUPPORTS"
    BELONGS_TO = "BELONGS_TO"


# Phase 2 Slice 2: typed 1-hop traversal allowlist.
# Compared against relationship.upper() — the live DB contains both
# "SUPPORTS" and "supports", so case-sensitive matching would silently
# drop half of the SUPPORTS-family edges.
# DERIVED_FROM is not present in the live graph and is omitted until it is.
RETRIEVAL_EXPANSION_EDGES: frozenset[str] = frozenset({"SUPPORTS", "CONTRADICTS"})


# Phase 1 relationship normalization. Collapses obvious case/alias drift
# at the MemoryGraph boundary so storage is canonical. Narrow by design —
# this is not an ontology rewrite.
RELATIONSHIP_ALIASES: dict[str, str] = {
    "RELATED_TO": "RELATES_TO",
    "RELATES_TO": "RELATES_TO",
    "CLARIFIES": "RELATES_TO",
    "SUPPORTS": "SUPPORTS",
    "ENABLES": "SUPPORTS",
}


def normalize_relationship(relationship: Optional[str]) -> str:
    """Collapse a relationship label to its canonical form.

    Applies `.strip().upper()` then looks up in RELATIONSHIP_ALIASES.
    Unknown labels round-trip unchanged (but upper-cased).
    None / empty / whitespace-only defaults to RELATES_TO.
    """
    stripped = (relationship or "").strip() or "RELATES_TO"
    normalized = stripped.upper()
    return RELATIONSHIP_ALIASES.get(normalized, normalized)


@dataclass
class Edge:
    """
    An edge in the memory graph.

    Represents a directed relationship between two memory nodes.
    """
    from_id: str
    to_id: str
    relationship: str
    strength: float = 1.0
    created_at: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        return f"Edge({self.from_id} --{self.relationship}[{self.strength:.2f}]--> {self.to_id})"


class DatabaseProtocol(Protocol):
    """Protocol defining the interface MemoryDatabase must implement for graph operations."""

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Execute a write query."""
        ...

    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows from a query."""
        ...

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Fetch a single row from a query."""
        ...


class MemoryGraph:
    """
    In-memory graph backed by SQLite for persistence.

    Uses NetworkX MultiDiGraph for fast traversal operations:
    - O(1) neighbor lookup
    - O(V+E) shortest path
    - Spreading activation for relevance
    - Multiple edge types per node pair (MENTIONS, INVOLVES, etc.)

    All mutations update both the in-memory graph and the database.
    """

    def __init__(self, db: "MemoryDatabase"):
        """
        Initialize the memory graph.

        Args:
            db: MemoryDatabase instance for persistence
        """
        self.db = db
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._loaded = False

    @property
    def graph(self) -> nx.MultiDiGraph:
        """Access the underlying NetworkX graph."""
        return self._graph

    # =========================================================================
    # Initialization
    # =========================================================================

    async def load_from_db(self) -> None:
        """
        Load all edges from database into the NetworkX graph.

        Should be called once at startup after database is initialized.
        """
        if self._loaded:
            logger.warning("Graph already loaded from database")
            return

        logger.info("Loading graph edges from database...")

        query = """
            SELECT from_id, to_id, relationship, strength, created_at, scope
            FROM graph_edges
        """

        rows = await self.db.fetchall(query)

        for row in rows:
            from_id, to_id, relationship, strength, created_at = row[0], row[1], row[2], row[3], row[4]
            edge_scope = row[5] if len(row) > 5 else "global"
            # Defensive normalization on load — even if the DB row drifted
            # (pre-backfill or from an external write), the in-memory view
            # stays canonical.
            relationship = normalize_relationship(relationship)
            self._graph.add_edge(
                from_id,
                to_id,
                key=relationship,
                relationship=relationship,
                strength=strength,
                created_at=created_at,
                scope=edge_scope,
            )

        self._loaded = True
        logger.info(f"Loaded {len(rows)} edges into memory graph")

    # =========================================================================
    # Edge Operations
    # =========================================================================

    async def add_edge(
        self,
        from_id: str,
        to_id: str,
        relationship: str,
        strength: float = 1.0,
        scope: str = "global",
    ) -> Edge:
        """
        Add an edge between two nodes.

        Updates both the in-memory graph and the database.
        If edge already exists, updates the strength.

        Args:
            from_id: Source node ID
            to_id: Target node ID
            relationship: Type of relationship (use RelationshipType)
            strength: Edge weight 0-1 (default 1.0)
            scope: Edge scope - 'global' or 'project:{slug}'

        Returns:
            The created or updated Edge
        """
        # Canonicalize relationship at the write boundary. Every downstream
        # consumer (NetworkX, SQLite row, returned Edge) sees the same label.
        relationship = normalize_relationship(relationship)

        # Clamp strength to valid range
        strength = max(0.0, min(1.0, strength))
        created_at = datetime.now()

        # Add to NetworkX graph
        self._graph.add_edge(
            from_id,
            to_id,
            key=relationship,
            relationship=relationship,
            strength=strength,
            created_at=created_at.isoformat(),
            scope=scope,
        )

        # Persist to database (upsert)
        query = """
            INSERT INTO graph_edges (from_id, to_id, relationship, strength, created_at, scope)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_id, to_id, relationship)
            DO UPDATE SET strength = excluded.strength
        """
        await self.db.execute(query, (from_id, to_id, relationship, strength, created_at.isoformat(), scope))

        logger.info(
            f"GRAPH_EDGE_ADDED: {from_id} --{relationship}[{strength:.2f}]--> {to_id} | "
            f"total_edges={self._graph.number_of_edges()}"
        )

        return Edge(
            from_id=from_id,
            to_id=to_id,
            relationship=relationship,
            strength=strength,
            created_at=created_at,
        )

    async def remove_edge(
        self,
        from_id: str,
        to_id: str,
        relationship: Optional[str] = None,
    ) -> bool:
        """
        Remove an edge between two nodes.

        Args:
            from_id: Source node ID
            to_id: Target node ID
            relationship: Optional - if provided, only remove edge with this relationship

        Returns:
            True if edge was removed, False if it didn't exist
        """
        # Check if edge exists
        if not self._graph.has_edge(from_id, to_id):
            return False

        if relationship:
            # Callers may still pass mixed-case / alias labels; canonicalize
            # so they hit the stored edge.
            relationship = normalize_relationship(relationship)
            # Only remove the specific relationship edge
            if not self._graph.has_edge(from_id, to_id, key=relationship):
                return False

            query = """
                DELETE FROM graph_edges
                WHERE from_id = ? AND to_id = ? AND relationship = ?
            """
            await self.db.execute(query, (from_id, to_id, relationship))
            self._graph.remove_edge(from_id, to_id, key=relationship)
        else:
            query = """
                DELETE FROM graph_edges
                WHERE from_id = ? AND to_id = ?
            """
            await self.db.execute(query, (from_id, to_id))
            # Remove ALL edges between the pair
            keys_to_remove = list(self._graph[from_id][to_id].keys())
            for key in keys_to_remove:
                self._graph.remove_edge(from_id, to_id, key=key)

        logger.debug(f"Removed edge: {from_id} --> {to_id}")
        return True

    async def get_edges(self, node_id: str) -> list[Edge]:
        """
        Get all edges connected to a node (both incoming and outgoing).

        Args:
            node_id: The node to get edges for

        Returns:
            List of Edge objects
        """
        edges: list[Edge] = []

        # Outgoing edges
        for _, to_id, key, data in self._graph.out_edges(node_id, data=True, keys=True):
            edges.append(Edge(
                from_id=node_id,
                to_id=to_id,
                relationship=data.get("relationship", "RELATES_TO"),
                strength=data.get("strength", 1.0),
                created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now()),
            ))

        # Incoming edges
        for from_id, _, key, data in self._graph.in_edges(node_id, data=True, keys=True):
            edges.append(Edge(
                from_id=from_id,
                to_id=node_id,
                relationship=data.get("relationship", "RELATES_TO"),
                strength=data.get("strength", 1.0),
                created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", datetime.now()),
            ))

        return edges

    # =========================================================================
    # Graph Traversal
    # =========================================================================

    async def get_neighbors(self, node_id: str, depth: int = 1) -> list[str]:
        """
        Get all nodes within N hops of a starting node.

        Uses BFS to find all reachable nodes up to the specified depth.

        Args:
            node_id: Starting node ID
            depth: Maximum number of hops (default 1)

        Returns:
            List of node IDs (excluding the starting node)
        """
        if node_id not in self._graph:
            return []

        if depth < 1:
            return []

        neighbors: set[str] = set()
        current_layer = {node_id}

        for _ in range(depth):
            next_layer: set[str] = set()
            for n in current_layer:
                # Both successors and predecessors (undirected traversal)
                next_layer.update(self._graph.successors(n))
                next_layer.update(self._graph.predecessors(n))

            # Remove already seen nodes
            next_layer -= neighbors
            next_layer.discard(node_id)  # Don't include start node

            neighbors.update(next_layer)
            current_layer = next_layer

            if not current_layer:
                break

        return list(neighbors)

    async def get_related_nodes(
        self,
        node_id: str,
        relationship: Optional[str] = None,
    ) -> list[str]:
        """
        Get nodes related to a node by a specific relationship.

        Args:
            node_id: Starting node ID
            relationship: Optional filter by relationship type

        Returns:
            List of related node IDs
        """
        if node_id not in self._graph:
            return []

        # Canonicalize the caller's filter so "supports" still hits SUPPORTS.
        wanted = normalize_relationship(relationship) if relationship else None

        related: list[str] = []

        # Check outgoing edges
        for _, to_id, key, data in self._graph.out_edges(node_id, data=True, keys=True):
            if wanted is None or data.get("relationship") == wanted:
                related.append(to_id)

        # Check incoming edges
        for from_id, _, key, data in self._graph.in_edges(node_id, data=True, keys=True):
            if wanted is None or data.get("relationship") == wanted:
                if from_id not in related:  # Avoid duplicates
                    related.append(from_id)

        return related

    async def traverse_typed(
        self,
        seed_ids: list[str],
        *,
        allowed_edges: frozenset[str] = RETRIEVAL_EXPANSION_EDGES,
        per_seed_cap: int = 3,
        scope: Optional[str] = None,
        excluded_node_types: Optional[frozenset[str]] = None,
    ) -> list[dict]:
        """
        Typed 1-hop traversal with deterministic ranking.

        Policy:
        - 1 hop only
        - allowlist-only (compared against relationship.upper())
        - capped per seed (lower-rank edges dropped once cap is hit)
        - scope-aware (edge scope and neighbor-node scope must match)
        - retrieval-eligible only (drops excluded node types if provided)
        - deduplicated across seeds (keep higher rank_score)
        - deterministic ordering: rank_score DESC, then related_id

        Rank score: strength * lock_in (lock_in floored at 0.01 so
        default-drifting nodes are not collapsed to zero).

        Args:
            seed_ids: Starting node IDs
            allowed_edges: Relationship names (upper-cased) to allow
            per_seed_cap: Max related nodes appended per seed
            scope: Optional scope filter ("global" / "project:slug")
            excluded_node_types: Node types to drop from output

        Returns:
            List of dicts with keys:
              - related_id: neighbor node id
              - seed_id: seed that surfaced it (first if duplicated)
              - rank_score: strength * lock_in
              - edge_type: normalized relationship name (upper-case)
              - strength: raw edge strength
        """
        if not seed_ids:
            return []

        seeds_set = set(seed_ids)
        excluded = excluded_node_types or frozenset()

        # Collect candidate (seed, neighbor, edge_type, strength) tuples.
        # Walk both out-edges and in-edges; typed traversal is semantically
        # symmetric for SUPPORTS/CONTRADICTS.
        per_seed: dict[str, list[tuple[str, str, float]]] = {sid: [] for sid in seed_ids}

        def _consume(seed_id: str, neighbor: str, data: dict) -> None:
            if neighbor in seeds_set:
                return  # don't duplicate seed nodes
            rel = (data.get("relationship") or "")
            if not isinstance(rel, str):
                return
            if rel.upper() not in allowed_edges:
                return
            edge_scope = data.get("scope", "global")
            if scope and edge_scope != scope:
                return
            strength = float(data.get("strength", 1.0))
            per_seed[seed_id].append((neighbor, rel.upper(), strength))

        for seed_id in seed_ids:
            if seed_id not in self._graph:
                continue
            for _, neighbor, _, data in self._graph.out_edges(seed_id, data=True, keys=True):
                _consume(seed_id, neighbor, data)
            for neighbor, _, _, data in self._graph.in_edges(seed_id, data=True, keys=True):
                _consume(seed_id, neighbor, data)

        all_candidate_ids: set[str] = {
            n for candidates in per_seed.values() for (n, _e, _s) in candidates
        }
        if not all_candidate_ids:
            return []

        # Single batched lookup for lock_in / scope / node_type.
        # Explicit column order so we can index tuple rows — aiosqlite returns
        # tuples by default in this codebase.
        placeholders = ",".join("?" * len(all_candidate_ids))
        rows = await self.db.fetchall(
            f"SELECT id, lock_in, scope, node_type FROM memory_nodes "
            f"WHERE id IN ({placeholders})",
            tuple(all_candidate_ids),
        )

        node_meta: dict[str, dict] = {}
        for row in rows:
            node_id = row[0]
            lock_in_val = row[1]
            node_scope = row[2] if row[2] is not None else "global"
            node_type = row[3]
            if node_type in excluded:
                continue
            if scope and node_scope != scope:
                continue
            node_meta[node_id] = {
                "lock_in": float(lock_in_val or 0.0),
                "node_type": node_type,
                "scope": node_scope,
            }

        # Per-seed rank + cap.
        best_by_id: dict[str, dict] = {}
        for seed_id, candidates in per_seed.items():
            scored: list[tuple[float, str, str, float]] = []
            for related_id, edge_type, strength in candidates:
                meta = node_meta.get(related_id)
                if meta is None:
                    continue
                # Floor lock_in so default-drifting nodes still rank by strength.
                rank_score = strength * max(meta["lock_in"], 0.01)
                scored.append((rank_score, related_id, edge_type, strength))
            # Deterministic ordering: score DESC, then related_id ASC.
            scored.sort(key=lambda x: (-x[0], x[1]))
            for rank_score, related_id, edge_type, strength in scored[:per_seed_cap]:
                existing = best_by_id.get(related_id)
                if existing is None or rank_score > existing["rank_score"]:
                    best_by_id[related_id] = {
                        "related_id": related_id,
                        "seed_id": seed_id,
                        "rank_score": rank_score,
                        "edge_type": edge_type,
                        "strength": strength,
                    }

        result = list(best_by_id.values())
        result.sort(key=lambda r: (-r["rank_score"], r["related_id"]))
        return result

    # =========================================================================
    # Spreading Activation
    # =========================================================================

    async def spreading_activation(
        self,
        start_nodes: list[str],
        decay: float = 0.5,
        max_depth: int = 3,
        scope: Optional[str] = None,
    ) -> dict[str, float]:
        """
        Calculate relevance scores using spreading activation.

        Starting from seed nodes with activation 1.0, spread activation
        through the graph with decay at each hop. Edge strength weights
        the activation transfer.

        This is how Luna expands context - start from current topics
        and find semantically related memories.

        Args:
            start_nodes: List of node IDs to start activation from
            decay: Decay factor per hop (0-1, default 0.5)
            max_depth: Maximum hops to propagate (default 3)
            scope: Optional scope filter. When set, only traverse edges
                   whose scope matches or is 'global'. None = traverse all.

        Returns:
            Dict mapping node_id -> activation score (0-1)
        """
        # Initialize activations
        activations: dict[str, float] = {}

        # Start nodes get full activation
        for node_id in start_nodes:
            if node_id in self._graph:
                activations[node_id] = 1.0

        if not activations:
            return {}

        # Spread activation through graph
        current_layer = set(start_nodes)

        for depth in range(max_depth):
            next_layer: set[str] = set()
            current_decay = decay ** (depth + 1)

            for node_id in current_layer:
                if node_id not in self._graph:
                    continue

                node_activation = activations.get(node_id, 0)

                # Spread to successors
                for _, neighbor, key, data in self._graph.out_edges(node_id, data=True, keys=True):
                    # Scope boundary: skip edges outside scope (global always traversable)
                    if scope is not None:
                        edge_scope = data.get("scope", "global")
                        if edge_scope != "global" and edge_scope != scope:
                            continue

                    edge_strength = data.get("strength", 1.0)
                    spread = node_activation * current_decay * edge_strength

                    # Accumulate activation (nodes can receive from multiple sources)
                    current = activations.get(neighbor, 0)
                    activations[neighbor] = min(1.0, current + spread)
                    next_layer.add(neighbor)

                # Spread to predecessors (bidirectional activation)
                for neighbor, _, key, data in self._graph.in_edges(node_id, data=True, keys=True):
                    # Scope boundary: skip edges outside scope
                    if scope is not None:
                        edge_scope = data.get("scope", "global")
                        if edge_scope != "global" and edge_scope != scope:
                            continue

                    edge_strength = data.get("strength", 1.0)
                    spread = node_activation * current_decay * edge_strength

                    current = activations.get(neighbor, 0)
                    activations[neighbor] = min(1.0, current + spread)
                    next_layer.add(neighbor)

            # Remove already visited from next layer
            next_layer -= current_layer
            current_layer = next_layer

            if not current_layer:
                break

        return activations

    # =========================================================================
    # Statistics
    # =========================================================================

    async def get_stats(self) -> dict:
        """
        Get statistics about the memory graph.

        Returns:
            Dict with node_count, edge_count, relationship breakdown, etc.
        """
        # Count relationships
        relationship_counts: dict[str, int] = {}
        for _, _, key, data in self._graph.edges(data=True, keys=True):
            rel = data.get("relationship", "UNKNOWN")
            relationship_counts[rel] = relationship_counts.get(rel, 0) + 1

        # Calculate average degree
        degrees = [d for _, d in self._graph.degree()]
        avg_degree = sum(degrees) / len(degrees) if degrees else 0

        # Find most connected nodes
        sorted_by_degree = sorted(
            self._graph.degree(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            "node_count": self._graph.number_of_nodes(),
            "edge_count": self._graph.number_of_edges(),
            "relationship_counts": relationship_counts,
            "average_degree": round(avg_degree, 2),
            "most_connected": [
                {"node_id": node, "degree": degree}
                for node, degree in sorted_by_degree
            ],
            "is_connected": nx.is_weakly_connected(self._graph) if self._graph.number_of_nodes() > 0 else True,
            "loaded": self._loaded,
        }

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def has_node(self, node_id: str) -> bool:
        """Check if a node exists in the graph."""
        return node_id in self._graph

    def has_edge(self, from_id: str, to_id: str,
                 relationship: Optional[str] = None) -> bool:
        """Check if an edge exists between two nodes (optionally for a specific relationship)."""
        if relationship:
            return self._graph.has_edge(
                from_id, to_id, key=normalize_relationship(relationship),
            )
        return self._graph.has_edge(from_id, to_id)

    async def clear(self) -> None:
        """
        Clear all edges from the graph.

        WARNING: This also clears the database table.
        """
        self._graph.clear()
        await self.db.execute("DELETE FROM graph_edges")
        logger.warning("Memory graph cleared")

    def __repr__(self) -> str:
        return f"MemoryGraph(nodes={self._graph.number_of_nodes()}, edges={self._graph.number_of_edges()})"
