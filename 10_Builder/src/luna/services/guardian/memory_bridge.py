"""
Guardian Memory Bridge
======================

Syncs Guardian demo data into Luna's project-scoped MemoryMatrix.
When activated, Luna *knows* about the community — the elders, the
resources, the governance decisions — without needing context injection.

Follows the Kozmo MemoryBridge pattern (quarantined at
services/kozmo/_quarantine/memory_bridge.py) but actually works.

Usage:
    bridge = GuardianMemoryBridge(engine)
    stats = await bridge.sync_all()
    # -> {"entities": 23, "knowledge": 163, "edges": 40}

Called from server.py on Guardian project activation.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default demo data location
from luna.core.paths import local_dir
GUARDIAN_DATA_ROOT = local_dir() / "guardian"

# Scope is now derived from the engine's active project at init time.


class GuardianMemoryBridge:
    """Syncs Guardian fixture data into project-scoped memory nodes."""

    def __init__(self, engine, data_root: Optional[Path] = None):
        """
        Args:
            engine: LunaEngine instance
            data_root: Path to Guardian data directory (default: data/guardian/)
        """
        self.engine = engine
        self.data_root = data_root or GUARDIAN_DATA_ROOT
        # Compute scope from engine's active project
        self._scope = (
            f"project:{engine.active_project}"
            if engine and getattr(engine, "active_project", None)
            else "global"
        )
        self._synced = False
        self._node_map = {}  # guardian_id -> matrix_node_id

    @property
    def is_synced(self) -> bool:
        return self._synced

    async def sync_all(self) -> dict:
        """
        Sync all Guardian data into the Memory Matrix.

        Returns:
            Stats dict with counts of synced items.
        """
        matrix = self.engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            logger.warning("Guardian bridge: Matrix not ready, cannot sync")
            return {"error": "matrix_not_ready"}

        stats = {"entities": 0, "knowledge": 0, "edges": 0, "skipped": 0}

        # Idempotency: skip if a substantial sync already exists (by scope, not content)
        existing = await self._count_scoped_nodes(matrix)
        if existing > 100:
            logger.info(f"Guardian bridge: {existing} scoped nodes exist, skipping sync")
            self._synced = True
            return {"already_synced": True, "existing_nodes": existing}

        # 1. Sync entities
        entity_count = await self._sync_entities(matrix)
        stats["entities"] = entity_count

        # 2. Sync knowledge nodes
        knowledge_count = await self._sync_knowledge(matrix)
        stats["knowledge"] = knowledge_count

        # 3. Sync relationships as edges
        edge_count = await self._sync_relationships(matrix)
        stats["edges"] = edge_count

        self._synced = True
        logger.info(
            f"Guardian bridge: Synced {stats['entities']} entities, "
            f"{stats['knowledge']} knowledge nodes, {stats['edges']} edges "
            f"into scope '{self._scope}'"
        )
        return stats

    async def _check_existing(self, matrix) -> int:
        """Check if Guardian data is already in the matrix."""
        try:
            results = await matrix._matrix.search_nodes(
                query="community",
                limit=5,
                scope=self._scope,
            )
            return len(results)
        except Exception:
            return 0

    async def _sync_entities(self, matrix) -> int:
        """Sync Guardian entities as FACT nodes."""
        path = self.data_root / "entities" / "entities_updated.json"
        if not path.exists():
            logger.warning(f"Guardian bridge: Entity file not found: {path}")
            return 0

        with open(path) as f:
            data = json.load(f)

        count = 0
        for entity in data.get("entities", []):
            entity_id = entity.get("id", "unknown")
            name = entity.get("name", entity_id)
            role = entity.get("role", "")
            profile = entity.get("profile", "")
            entity_type = entity.get("type", "person")
            aliases = entity.get("aliases", [])
            scope_tag = entity.get("scope", "")
            threads = entity.get("thread_presence", [])

            # Build concise fact string
            parts = [f"{name} is a {entity_type} in the community."]
            if role:
                parts.append(f"Role: {role}.")
            if profile:
                # Truncate long profiles
                parts.append(profile[:300])
            if aliases:
                parts.append(f"Also known as: {', '.join(aliases)}.")
            if threads:
                parts.append(f"Present in conversations with: {', '.join(threads)}.")

            content = " ".join(parts)

            tags = ["guardian", "entity", entity_type, entity_id]
            if scope_tag:
                tags.append(scope_tag)

            try:
                node_id = await matrix.store_memory(
                    content=content,
                    node_type="FACT",
                    tags=tags,
                    confidence=95,
                    scope=self._scope,
                )
                self._node_map[entity_id] = node_id
                count += 1
            except Exception as e:
                logger.error(f"Guardian bridge: Failed to sync entity '{name}': {e}")

        return count

    async def _sync_knowledge(self, matrix) -> int:
        """Sync Guardian knowledge nodes."""
        knowledge_dir = self.data_root / "knowledge_nodes"
        if not knowledge_dir.exists():
            logger.warning(f"Guardian bridge: Knowledge dir not found: {knowledge_dir}")
            return 0

        count = 0
        for json_file in sorted(knowledge_dir.glob("*.json")):
            with open(json_file) as f:
                data = json.load(f)

            for node in data.get("nodes", []):
                node_id = node.get("id", "unknown")
                node_type = node.get("node_type", "FACT")
                title = node.get("title", "")
                content = node.get("content", "")
                created_by = node.get("created_by", "")
                created_date = node.get("created_date", "")
                lock_in = node.get("lock_in", 0.5)
                node_tags = node.get("tags", [])
                scope_tag = node.get("scope", "")

                # Build rich content string
                parts = []
                if title:
                    parts.append(f"{title}.")
                if content:
                    parts.append(content[:500])
                if created_by:
                    parts.append(f"Source: {created_by}.")
                if created_date:
                    parts.append(f"Date: {created_date}.")

                full_content = " ".join(parts)

                tags = ["guardian", "knowledge", node_type.lower(), node_id]
                tags.extend(node_tags[:5])  # Keep top 5 original tags
                if scope_tag:
                    tags.append(scope_tag)

                # Map node_type to Matrix types
                matrix_type = node_type.upper()
                if matrix_type not in ("FACT", "DECISION", "ACTION", "INSIGHT", "PROBLEM"):
                    matrix_type = "FACT"  # milestones -> FACT

                try:
                    matrix_node_id = await matrix.store_memory(
                        content=full_content,
                        node_type=matrix_type,
                        tags=tags,
                        confidence=int(lock_in * 100),
                        scope=self._scope,
                    )
                    self._node_map[node_id] = matrix_node_id
                    count += 1
                except Exception as e:
                    logger.error(f"Guardian bridge: Failed to sync node '{title[:50]}': {e}")

        return count

    async def _sync_relationships(self, matrix) -> int:
        """Sync Guardian relationships as memory graph edges."""
        path = self.data_root / "entities" / "relationships_updated.json"
        if not path.exists():
            logger.warning(f"Guardian bridge: Relationships file not found: {path}")
            return 0

        with open(path) as f:
            data = json.load(f)

        # Also sync knowledge node connections
        edge_count = 0

        # Entity-level relationships
        for rel in data.get("relationships", []):
            from_id = rel.get("from", "")
            to_id = rel.get("to", "")
            rel_type = rel.get("type", "related_to")
            strength = rel.get("strength", 0.5)
            description = rel.get("description", "")

            # Look up matrix node IDs
            from_matrix = self._node_map.get(from_id)
            to_matrix = self._node_map.get(to_id)

            if from_matrix and to_matrix:
                try:
                    await matrix._graph.add_edge(
                        from_id=from_matrix,
                        to_id=to_matrix,
                        relationship=rel_type,
                        strength=strength,
                        scope=self._scope,
                    )
                    edge_count += 1
                except Exception as e:
                    logger.debug(f"Guardian bridge: Edge failed {from_id}->{to_id}: {e}")

        # Knowledge node connections (from the connections field)
        knowledge_dir = self.data_root / "knowledge_nodes"
        if knowledge_dir.exists():
            for json_file in knowledge_dir.glob("*.json"):
                with open(json_file) as f:
                    kn_data = json.load(f)
                for node in kn_data.get("nodes", []):
                    node_id = node.get("id", "")
                    connections = node.get("connections", [])
                    from_matrix = self._node_map.get(node_id)
                    if not from_matrix:
                        continue
                    for conn_id in connections:
                        to_matrix = self._node_map.get(conn_id)
                        if to_matrix:
                            try:
                                await matrix._graph.add_edge(
                                    from_id=from_matrix,
                                    to_id=to_matrix,
                                    relationship="related_to",
                                    strength=0.7,
                                    scope=self._scope,
                                )
                                edge_count += 1
                            except Exception:
                                pass  # Duplicate edges are fine

        return edge_count

    async def _count_scoped_nodes(self, matrix) -> int:
        """Count nodes that have the guardian scope."""
        try:
            db = matrix._matrix.db
            row = await db.fetchone(
                "SELECT COUNT(*) FROM memory_nodes WHERE scope = ?",
                (self._scope,)
            )
            return row[0] if row else 0
        except Exception:
            return 0

    async def clear(self) -> int:
        """
        Remove all Guardian-synced nodes and edges from the matrix.

        Returns:
            Number of nodes removed.
        """
        matrix = self.engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            return 0

        try:
            db = matrix._matrix.db

            # Delete scoped edges first (FK-safe order)
            await db.execute(
                "DELETE FROM graph_edges WHERE scope = ?",
                (self._scope,)
            )

            # Delete scoped nodes
            result = await db.execute(
                "DELETE FROM memory_nodes WHERE scope = ?",
                (self._scope,)
            )
            removed = result.rowcount

            # Reload in-memory graph to drop deleted edges
            if matrix._graph:
                await matrix._graph.load()

            self._synced = False
            self._node_map = {}
            logger.info(f"Guardian bridge: Cleared {removed} nodes + edges")
            return removed
        except Exception as e:
            logger.error(f"Guardian bridge: Clear failed: {e}")
            return 0
