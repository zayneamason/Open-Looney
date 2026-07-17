"""
KOZMO Project Graph

Per-project graph database — a derived index built from YAML source of truth.

Uses NetworkX for in-memory graph operations and SQLite for persistence.
This is NOT Luna's Memory Matrix. Project graphs are isolated, lightweight,
and can be rebuilt from YAML at any time.

Responsibilities:
- Index entities as nodes in project graph
- Track relationships as directed edges
- Query entity neighborhoods and paths
- Rebuild graph from YAML source of truth
- Persist/load graph to/from project SQLite DB

Design Constraints:
- YAML is source of truth, graph is derived
- Graph can be nuked and rebuilt without data loss
- One graph per project, isolated from Memory Matrix
- Synchronous operations (no async needed for derived index)
"""

import sqlite3
import json
import networkx as nx
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from .types import Entity, Relationship


# =============================================================================
# Project Graph
# =============================================================================


class ProjectGraph:
    """
    Per-project entity relationship graph.

    Nodes = entities (keyed by slug)
    Edges = relationships between entities

    The graph is a derived index. It can be rebuilt from YAML at any time.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize project graph.

        Args:
            db_path: Path to SQLite database for persistence.
                    If None, graph is in-memory only.
        """
        self._graph = nx.DiGraph()
        self._db_path = db_path

        if db_path and db_path.exists():
            self._load_from_db()

    # =========================================================================
    # Node Operations
    # =========================================================================

    def add_entity(self, entity: Entity) -> None:
        """
        Add entity as node in graph.

        Args:
            entity: Entity to add
        """
        self._graph.add_node(
            entity.slug,
            type=entity.type,
            name=entity.name,
            status=entity.status,
            tags=entity.tags,
        )

    def remove_entity(self, slug: str) -> bool:
        """
        Remove entity node and all its edges.

        Args:
            slug: Entity slug

        Returns:
            True if removed, False if not found
        """
        if slug not in self._graph:
            return False

        self._graph.remove_node(slug)
        return True

    def has_entity(self, slug: str) -> bool:
        """Check if entity exists in graph."""
        return slug in self._graph

    def get_entity_data(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get entity node data.

        Args:
            slug: Entity slug

        Returns:
            Node data dict if found, None otherwise
        """
        if slug not in self._graph:
            return None
        return dict(self._graph.nodes[slug])

    def entity_count(self) -> int:
        """Return number of entities in graph."""
        return self._graph.number_of_nodes()

    # =========================================================================
    # Edge Operations
    # =========================================================================

    def add_relationship(
        self,
        from_slug: str,
        to_slug: str,
        rel_type: str,
        detail: Optional[str] = None,
    ) -> None:
        """
        Add directed relationship edge.

        Args:
            from_slug: Source entity slug
            to_slug: Target entity slug
            rel_type: Relationship type (e.g., 'family', 'works_at')
            detail: Optional relationship detail
        """
        self._graph.add_edge(
            from_slug,
            to_slug,
            type=rel_type,
            detail=detail,
        )

    def remove_relationship(self, from_slug: str, to_slug: str) -> bool:
        """
        Remove relationship edge.

        Args:
            from_slug: Source entity slug
            to_slug: Target entity slug

        Returns:
            True if removed, False if not found
        """
        if not self._graph.has_edge(from_slug, to_slug):
            return False

        self._graph.remove_edge(from_slug, to_slug)
        return True

    def has_relationship(self, from_slug: str, to_slug: str) -> bool:
        """Check if relationship exists."""
        return self._graph.has_edge(from_slug, to_slug)

    def get_relationship(
        self, from_slug: str, to_slug: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get relationship edge data.

        Returns:
            Edge data dict if found, None otherwise
        """
        if not self._graph.has_edge(from_slug, to_slug):
            return None
        return dict(self._graph.edges[from_slug, to_slug])

    def edge_count(self) -> int:
        """Return number of relationship edges in graph."""
        return self._graph.number_of_edges()

    # =========================================================================
    # Query Operations
    # =========================================================================

    def neighbors(self, slug: str) -> List[str]:
        """
        Get entities connected to given entity (outgoing edges).

        Args:
            slug: Entity slug

        Returns:
            List of connected entity slugs
        """
        if slug not in self._graph:
            return []
        return list(self._graph.successors(slug))

    def predecessors(self, slug: str) -> List[str]:
        """
        Get entities that point to given entity (incoming edges).

        Args:
            slug: Entity slug

        Returns:
            List of entity slugs pointing to this entity
        """
        if slug not in self._graph:
            return []
        return list(self._graph.predecessors(slug))

    def get_relationships_for(self, slug: str) -> List[Dict[str, Any]]:
        """
        Get all relationships for an entity (outgoing).

        Args:
            slug: Entity slug

        Returns:
            List of relationship dicts with target and edge data
        """
        if slug not in self._graph:
            return []

        relationships = []
        for target in self._graph.successors(slug):
            edge_data = dict(self._graph.edges[slug, target])
            relationships.append({
                "entity": target,
                **edge_data,
            })
        return relationships

    def entities_by_type(self, entity_type: str) -> List[str]:
        """
        Get all entity slugs of a given type.

        Args:
            entity_type: Entity type (e.g., 'characters')

        Returns:
            List of entity slugs
        """
        return [
            slug
            for slug, data in self._graph.nodes(data=True)
            if data.get("type") == entity_type
        ]

    def entities_by_tag(self, tag: str) -> List[str]:
        """
        Get all entity slugs with a given tag.

        Args:
            tag: Tag to search for

        Returns:
            List of entity slugs
        """
        return [
            slug
            for slug, data in self._graph.nodes(data=True)
            if tag in data.get("tags", [])
        ]

    def shortest_path(self, from_slug: str, to_slug: str) -> Optional[List[str]]:
        """
        Find shortest path between two entities.

        Args:
            from_slug: Source entity slug
            to_slug: Target entity slug

        Returns:
            List of slugs in path, or None if no path exists
        """
        try:
            return nx.shortest_path(self._graph, from_slug, to_slug)
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return None

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def index_entity(self, entity: Entity) -> None:
        """
        Index entity and its relationships into graph.

        Adds entity as node and all its relationships as edges.
        If entity already exists, updates its data.

        Args:
            entity: Entity to index
        """
        # Add/update node
        self.add_entity(entity)

        # Add relationship edges
        for rel in entity.relationships:
            # Ensure target node exists (may be a stub)
            if not self.has_entity(rel.entity):
                self._graph.add_node(rel.entity)

            self.add_relationship(
                from_slug=entity.slug,
                to_slug=rel.entity,
                rel_type=rel.type,
                detail=rel.detail,
            )

    def rebuild(self, entities: List[Entity]) -> None:
        """
        Rebuild graph from scratch using entity list.

        Clears existing graph and re-indexes all entities.
        This is the "nuke and rebuild" operation.

        Args:
            entities: Complete list of entities to index
        """
        self._graph.clear()

        for entity in entities:
            self.index_entity(entity)

    def clear(self) -> None:
        """Clear all nodes and edges from graph."""
        self._graph.clear()

    # =========================================================================
    # Persistence (SQLite)
    # =========================================================================

    def save(self, db_path: Optional[Path] = None) -> None:
        """
        Persist graph to SQLite database.

        Args:
            db_path: Override path (uses self._db_path if None)
        """
        path = db_path or self._db_path
        if not path:
            raise ValueError("No database path specified")

        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            conn.execute("DROP TABLE IF EXISTS graph_nodes")
            conn.execute("DROP TABLE IF EXISTS graph_edges")

            conn.execute("""
                CREATE TABLE graph_nodes (
                    slug TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE graph_edges (
                    from_slug TEXT NOT NULL,
                    to_slug TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (from_slug, to_slug)
                )
            """)

            # Insert nodes
            for slug, data in self._graph.nodes(data=True):
                conn.execute(
                    "INSERT INTO graph_nodes (slug, data) VALUES (?, ?)",
                    (slug, json.dumps(data, default=str)),
                )

            # Insert edges
            for from_slug, to_slug, data in self._graph.edges(data=True):
                conn.execute(
                    "INSERT INTO graph_edges (from_slug, to_slug, data) VALUES (?, ?, ?)",
                    (from_slug, to_slug, json.dumps(data, default=str)),
                )

            conn.commit()
        finally:
            conn.close()

    def _load_from_db(self) -> None:
        """Load graph from SQLite database."""
        if not self._db_path or not self._db_path.exists():
            return

        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            # Check if tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
            )
            if not cursor.fetchone():
                return

            # Load nodes
            cursor = conn.execute("SELECT slug, data FROM graph_nodes")
            for slug, data_json in cursor:
                data = json.loads(data_json)
                self._graph.add_node(slug, **data)

            # Load edges
            cursor = conn.execute("SELECT from_slug, to_slug, data FROM graph_edges")
            for from_slug, to_slug, data_json in cursor:
                data = json.loads(data_json)
                self._graph.add_edge(from_slug, to_slug, **data)
        finally:
            conn.close()

    # =========================================================================
    # Stats
    # =========================================================================

    def stats(self) -> Dict[str, Any]:
        """
        Get graph statistics.

        Returns:
            Dict with node count, edge count, type breakdown, etc.
        """
        type_counts = {}
        for _, data in self._graph.nodes(data=True):
            entity_type = data.get("type", "unknown")
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

        return {
            "nodes": self._graph.number_of_nodes(),
            "edges": self._graph.number_of_edges(),
            "types": type_counts,
            "connected_components": nx.number_weakly_connected_components(self._graph),
        }
