"""
Knowledge Compiler — Phase 1
=============================

Batch compilation engine that reads Guardian source documents and
produces 6D-classified Memory Matrix nodes with typed graph edges.

Replaces the Guardian Memory Bridge's flat "concatenate and store"
approach with structured ingestion that preserves relationships,
temporal ordering, and entity connections.

Usage:
    compiler = KnowledgeCompiler(engine, local_dir() / "guardian")
    stats = await compiler.compile_all()
    # -> CompileResult(entities=25, knowledge=148, constellations=12, edges=85)
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .constellation import (
    CompiledNode,
    build_governance_record,
    build_person_briefing,
    build_project_status,
)
from .entity_index import EntityIndex

logger = logging.getLogger(__name__)

# Scope is now config-driven via the engine's active project.
# Pass scope to KnowledgeCompiler constructor; defaults to "global".

# Map guardian relationship types to graph relationship types
RELATIONSHIP_MAP = {
    "enables": "SUPPORTS",
    "supports": "SUPPORTS",
    "clarifies": "RELATES_TO",
    "related_to": "RELATES_TO",
    "leads_to": "FOLLOWED_BY",
    "corroborates": "SUPPORTS",
    "depends_on": "DEPENDS_ON",
    "contradicts": "CONTRADICTS",
    "mentored_by": "SUPPORTS",
    "works_with": "RELATES_TO",
}


@dataclass
class CompileResult:
    """Statistics from a compilation run."""

    entities: int = 0
    knowledge: int = 0
    constellations: int = 0
    edges: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entities": self.entities,
            "knowledge": self.knowledge,
            "constellations": self.constellations,
            "edges": self.edges,
            "skipped": self.skipped,
            "errors": self.errors[:10],
        }

    def __iadd__(self, other):
        if isinstance(other, int):
            return self
        self.entities += other.entities
        self.knowledge += other.knowledge
        self.constellations += other.constellations
        self.edges += other.edges
        self.skipped += other.skipped
        self.errors.extend(other.errors)
        return self


class KnowledgeCompiler:
    """
    Batch knowledge compilation for Luna's Memory Matrix.

    Reads source documents, produces 6D-classified memory nodes
    with edges. Replaces the Guardian Memory Bridge's simple sync.
    """

    def __init__(self, engine, data_root: Path, scope: str = "global"):
        self.engine = engine
        self.data_root = data_root
        # Resolve scope from engine if caller passed default
        if scope == "global" and engine and hasattr(engine, "active_scope"):
            self.scope = engine.active_scope or "global"
        else:
            self.scope = scope or "global"
        self.entity_index = EntityIndex()
        self.node_map: dict[str, str] = {}  # source_id -> matrix_node_id
        # All loaded source nodes keyed by source ID
        self._source_nodes: dict[str, dict] = {}

    async def compile_all(self) -> CompileResult:
        """Full compilation pipeline."""
        matrix = self._get_matrix()
        if not matrix:
            return CompileResult(errors=["Matrix actor not ready"])

        result = CompileResult()

        # Phase 1: Build indices
        self._build_entity_index()
        self._load_all_source_nodes()

        # Phase 2: Store entity nodes
        result.entities = await self._compile_entities(matrix)

        # Phase 3: Compile knowledge nodes (with 6D classification)
        result.knowledge = await self._compile_knowledge_nodes(matrix)

        # Phase 4: Compile timeline events not already in knowledge nodes
        result.knowledge += await self._compile_timeline_events(matrix)

        # Phase 5: Generate constellations
        result.constellations = await self._generate_constellations(matrix)

        # Phase 6: Create edges
        result.edges = await self._create_all_edges(matrix)

        logger.info(
            f"Compiler: {result.entities} entities, {result.knowledge} knowledge, "
            f"{result.constellations} constellations, {result.edges} edges"
        )
        return result

    async def clear(self) -> int:
        """Remove all compiler-produced nodes by scope."""
        matrix = self._get_matrix()
        if not matrix:
            return 0

        try:
            db = matrix._matrix.db

            await db.execute(
                "DELETE FROM graph_edges WHERE scope = ?", (self.scope,)
            )
            result = await db.execute(
                "DELETE FROM memory_nodes WHERE scope = ?", (self.scope,)
            )
            removed = result.rowcount

            if matrix._graph:
                await matrix._graph.load_from_db()

            self.node_map = {}
            logger.info(f"Compiler: cleared {removed} nodes + edges")
            return removed
        except Exception as e:
            logger.error(f"Compiler: clear failed: {e}")
            return 0

    # ── Helpers ──────────────────────────────────────────────────

    def _get_matrix(self):
        """Get the matrix actor from the engine."""
        matrix = self.engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            logger.warning("Compiler: Matrix actor not ready")
            return None
        return matrix

    # ── Phase 1: Build Indices ───────────────────────────────────

    def _build_entity_index(self):
        """Load entities and build alias resolution index."""
        entities_path = self.data_root / "entities" / "entities_updated.json"
        self.entity_index.load_entities(entities_path)

    def _load_all_source_nodes(self):
        """Load all knowledge nodes into memory for cross-referencing."""
        knowledge_dir = self.data_root / "knowledge_nodes"
        if not knowledge_dir.exists():
            return

        for json_file in sorted(knowledge_dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                for node in data.get("nodes", []):
                    nid = node.get("id", "")
                    if nid:
                        self._source_nodes[nid] = node
                        # Track entity mentions
                        self._index_entity_mentions(nid, node)
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Compiler: failed to load {json_file}: {e}")

    def _index_entity_mentions(self, node_id: str, node: dict):
        """Record which entities are mentioned in a node."""
        # Entities explicitly tagged in the source
        created_by = node.get("created_by", "")
        if created_by:
            eid = self.entity_index.resolve(created_by)
            if eid:
                self.entity_index.record_mention(eid, node_id)

        # Check content + title for entity name matches
        text = (node.get("title", "") + " " + node.get("content", "")).lower()
        for eid, profile in self.entity_index.entities.items():
            name_lower = profile.name.lower()
            if name_lower in text:
                self.entity_index.record_mention(eid, node_id)

    # ── Phase 2: Compile Entities ────────────────────────────────

    async def _compile_entities(self, matrix) -> int:
        """Store each entity as a FACT node with rich content."""
        count = 0
        for eid, profile in self.entity_index.entities.items():
            parts = [f"{profile.name} is a {profile.entity_type}."]
            if profile.role:
                parts.append(f"Role: {profile.role}.")
            if profile.profile:
                parts.append(profile.profile)
            if profile.aliases:
                parts.append(f"Also known as: {', '.join(profile.aliases)}.")
            if profile.clan:
                parts.append(f"Clan: {profile.clan}.")
            if profile.location:
                parts.append(f"Location: {profile.location}.")

            content = " ".join(parts)
            tags = [
                "guardian", "entity", "compiled",
                profile.entity_type, eid,
            ]
            if profile.scope:
                tags.append(profile.scope)

            try:
                node_id = await matrix.store_memory(
                    content=content,
                    node_type="FACT",
                    tags=tags,
                    confidence=95,
                    scope=self.scope,
                )
                self.node_map[eid] = node_id
                count += 1
            except Exception as e:
                logger.error(f"Compiler: entity '{eid}' failed: {e}")

        return count

    # ── Phase 3: Compile Knowledge Nodes ─────────────────────────

    async def _compile_knowledge_nodes(self, matrix) -> int:
        """Compile knowledge nodes with 6D classification."""
        count = 0
        for source_id, node in self._source_nodes.items():
            node_type = node.get("node_type", "FACT").upper()

            # Map to supported types (MILESTONE -> FACT since it's not
            # in the base MatrixActor types, but we keep the tag)
            is_milestone = node_type == "MILESTONE"
            matrix_type = node_type
            if matrix_type not in ("FACT", "DECISION", "ACTION", "INSIGHT", "PROBLEM"):
                matrix_type = "FACT"

            # Build rich 6D content
            title = node.get("title", "")
            content = node.get("content", "")
            created_by = node.get("created_by", "")
            created_date = node.get("created_date", "")
            lock_in = node.get("lock_in", 0.5)
            node_scope = node.get("scope", "")
            node_tags = node.get("tags", [])

            # Resolve entities mentioned
            entities_mentioned = []
            if created_by:
                resolved = self.entity_index.resolve(created_by)
                if resolved:
                    entities_mentioned.append(resolved)

            # Build structured content preserving 6D
            parts = []
            if title:
                parts.append(title + ".")
            if content:
                parts.append(content)
            if created_by:
                parts.append(f"Source: {created_by}.")
            if created_date:
                parts.append(f"Date: {created_date}.")
            if entities_mentioned:
                parts.append(f"Entities: {', '.join(entities_mentioned)}.")

            full_content = "\n".join(parts)

            tags = ["guardian", "knowledge", "compiled", node_type.lower(), source_id]
            tags.extend(node_tags[:5])
            if node_scope:
                tags.append(node_scope)
            if is_milestone:
                tags.append("milestone")
            if entities_mentioned:
                tags.extend(entities_mentioned)

            try:
                matrix_node_id = await matrix.store_memory(
                    content=full_content,
                    node_type=matrix_type,
                    tags=tags,
                    confidence=int(lock_in * 100),
                    scope=self.scope,
                )
                self.node_map[source_id] = matrix_node_id
                count += 1
            except Exception as e:
                logger.error(f"Compiler: node '{source_id}' failed: {e}")

        return count

    # ── Phase 4: Timeline Events ─────────────────────────────────

    async def _compile_timeline_events(self, matrix) -> int:
        """Compile timeline events that aren't already knowledge nodes."""
        timeline_path = self.data_root / "org_timeline" / "timeline_events.json"
        if not timeline_path.exists():
            return 0

        try:
            with open(timeline_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        for event in data.get("events", []):
            evt_id = event.get("id", "")
            if not evt_id or evt_id in self.node_map:
                continue

            title = event.get("title", "")
            description = event.get("description", "")
            timestamp = event.get("timestamp", "")
            knowledge_type = event.get("knowledge_type", "FACT")
            actors = event.get("actors", [])
            impact = event.get("impact_score", 0.5)
            evt_tags = event.get("tags", [])

            # Only compile significant events
            if impact < 0.4:
                continue

            matrix_type = knowledge_type.upper()
            if matrix_type not in ("FACT", "DECISION", "ACTION", "INSIGHT", "PROBLEM"):
                matrix_type = "FACT"

            parts = [f"{title}."]
            if description:
                parts.append(description)
            if timestamp:
                parts.append(f"Date: {timestamp[:10]}.")
            if actors:
                resolved = self.entity_index.resolve_list(actors)
                if resolved:
                    parts.append(f"Actors: {', '.join(resolved)}.")

            content = "\n".join(parts)
            tags = ["guardian", "timeline", "compiled", evt_id]
            tags.extend(evt_tags[:5])

            try:
                node_id = await matrix.store_memory(
                    content=content,
                    node_type=matrix_type,
                    tags=tags,
                    confidence=int(impact * 100),
                    scope=self.scope,
                )
                self.node_map[evt_id] = node_id
                count += 1
            except Exception as e:
                logger.error(f"Compiler: timeline event '{evt_id}' failed: {e}")

        return count

    # ── Phase 5: Generate Constellations ─────────────────────────

    async def _generate_constellations(self, matrix) -> int:
        """Generate PERSON_BRIEFING, PROJECT_STATUS, GOVERNANCE_RECORD."""
        count = 0

        # Person briefings
        count += await self._generate_person_briefings(matrix)

        # Project status
        count += await self._generate_project_status(matrix)

        # Governance record
        count += await self._generate_governance_record(matrix)

        return count

    async def _generate_person_briefings(self, matrix) -> int:
        """Generate a PERSON_BRIEFING node for each significant entity."""
        count = 0
        for profile in self.entity_index.significant_entities(min_mentions=3):
            related_nodes = self._get_nodes_mentioning(profile.id)
            compiled = build_person_briefing(profile, related_nodes)
            node_id = await self._store_compiled_node(matrix, compiled)
            if node_id:
                # BRIEFING_FOR edge
                entity_matrix_id = self.node_map.get(profile.id)
                if entity_matrix_id:
                    try:
                        await matrix._graph.add_edge(
                            from_id=node_id,
                            to_id=entity_matrix_id,
                            relationship="BELONGS_TO",
                            strength=1.0,
                            scope=self.scope,
                        )
                    except Exception:
                        pass
                count += 1

        return count

    async def _generate_project_status(self, matrix) -> int:
        """Generate PROJECT_STATUS for the main project."""
        milestones = [
            n for n in self._source_nodes.values()
            if n.get("node_type", "").upper() == "MILESTONE"
        ]
        actions = [
            n for n in self._source_nodes.values()
            if n.get("node_type", "").upper() == "ACTION"
        ]
        decisions = [
            n for n in self._source_nodes.values()
            if n.get("node_type", "").upper() == "DECISION"
        ]

        if not milestones and not actions:
            return 0

        # Enrich nodes with resolved entities for the constellation
        for node_list in [milestones, actions, decisions]:
            for node in node_list:
                created_by = node.get("created_by", "")
                if created_by:
                    resolved = self.entity_index.resolve(created_by)
                    if resolved:
                        node.setdefault("entities", []).append(resolved)

        # Derive project name/id from engine's active project or use defaults
        project_id = self.engine.active_project if self.engine and self.engine.active_project else "active-project"
        project_name = project_id.replace("-", " ").title() if project_id != "active-project" else "Active Project"
        compiled = build_project_status(
            project_name=project_name,
            project_id=project_id,
            milestones=milestones,
            actions=actions,
            decisions=decisions,
            scope="community",
        )
        node_id = await self._store_compiled_node(matrix, compiled)
        return 1 if node_id else 0

    async def _generate_governance_record(self, matrix) -> int:
        """Generate GOVERNANCE_RECORD constellation."""
        decisions = [
            n for n in self._source_nodes.values()
            if n.get("node_type", "").upper() == "DECISION"
        ]
        insights = [
            n for n in self._source_nodes.values()
            if n.get("node_type", "").upper() == "INSIGHT"
        ]

        # Load scope transitions
        scope_transitions = []
        st_path = self.data_root / "membrane" / "scope_transitions.json"
        if st_path.exists():
            try:
                with open(st_path) as f:
                    data = json.load(f)
                scope_transitions = data.get("transitions", [])
            except (json.JSONDecodeError, OSError):
                pass

        gov_decisions = [d for d in decisions if d.get("scope") == "governance"]
        if not gov_decisions and not scope_transitions:
            return 0

        # Derive project name from engine for governance record header
        gov_project_name = None
        if self.engine and getattr(self.engine, "active_project", None):
            slug = self.engine.active_project
            gov_project_name = slug.replace("-", " ").title()

        compiled = build_governance_record(
            decisions=decisions,
            insights=insights,
            scope_transitions=scope_transitions,
            entity_index=self.entity_index,
            project_name=gov_project_name,
        )
        if compiled is None:
            return 0
        node_id = await self._store_compiled_node(matrix, compiled)
        return 1 if node_id else 0

    # ── Phase 6: Create Edges ────────────────────────────────────

    async def _create_all_edges(self, matrix) -> int:
        """Create all graph edges from relationships, connections, and timeline."""
        count = 0

        # Entity-level relationships
        count += await self._create_relationship_edges(matrix)

        # Knowledge node connections
        count += await self._create_connection_edges(matrix)

        # Timeline dependency edges
        count += await self._create_timeline_edges(matrix)

        return count

    async def _create_relationship_edges(self, matrix) -> int:
        """Create edges from relationships_updated.json."""
        path = self.data_root / "entities" / "relationships_updated.json"
        if not path.exists():
            return 0

        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        for rel in data.get("relationships", []):
            from_id = rel.get("from", "")
            to_id = rel.get("to", "")
            rel_type = rel.get("type", "related_to")
            strength = rel.get("strength", 0.5)

            from_matrix = self.node_map.get(from_id)
            to_matrix = self.node_map.get(to_id)

            if from_matrix and to_matrix:
                graph_type = RELATIONSHIP_MAP.get(rel_type, "RELATES_TO")
                try:
                    await matrix._graph.add_edge(
                        from_id=from_matrix,
                        to_id=to_matrix,
                        relationship=graph_type,
                        strength=strength,
                        scope=self.scope,
                    )
                    count += 1
                except Exception:
                    pass

        return count

    async def _create_connection_edges(self, matrix) -> int:
        """Create edges from connections[] arrays in knowledge nodes."""
        count = 0
        for source_id, node in self._source_nodes.items():
            connections = node.get("connections", [])
            from_matrix = self.node_map.get(source_id)
            if not from_matrix:
                continue

            for conn_id in connections:
                to_matrix = self.node_map.get(conn_id)
                if to_matrix and to_matrix != from_matrix:
                    try:
                        await matrix._graph.add_edge(
                            from_id=from_matrix,
                            to_id=to_matrix,
                            relationship="RELATES_TO",
                            strength=0.7,
                            scope=self.scope,
                        )
                        count += 1
                    except Exception:
                        pass

        return count

    async def _create_timeline_edges(self, matrix) -> int:
        """Create FOLLOWED_BY edges from timeline event dependencies."""
        timeline_path = self.data_root / "org_timeline" / "timeline_events.json"
        if not timeline_path.exists():
            return 0

        try:
            with open(timeline_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0
        for event in data.get("events", []):
            evt_id = event.get("id", "")
            from_matrix = self.node_map.get(evt_id)
            if not from_matrix:
                continue

            connections = event.get("connections", {})
            for rel_type, targets in connections.items():
                graph_type = RELATIONSHIP_MAP.get(rel_type, "RELATES_TO")
                if isinstance(targets, list):
                    for target_id in targets:
                        to_matrix = self.node_map.get(target_id)
                        if to_matrix and to_matrix != from_matrix:
                            try:
                                await matrix._graph.add_edge(
                                    from_id=from_matrix,
                                    to_id=to_matrix,
                                    relationship=graph_type,
                                    strength=0.8,
                                    scope=self.scope,
                                )
                                count += 1
                            except Exception:
                                pass

        return count

    # ── Internal Utilities ───────────────────────────────────────

    def _get_nodes_mentioning(self, entity_id: str) -> list[dict]:
        """Get all source nodes that mention a given entity."""
        profile = self.entity_index.get_profile(entity_id)
        if not profile:
            return []

        result = []
        for node_id in profile.mentioned_in:
            node = self._source_nodes.get(node_id)
            if node:
                result.append(node)
        return result

    async def _store_compiled_node(
        self, matrix, compiled: CompiledNode
    ) -> Optional[str]:
        """Store a CompiledNode into the Memory Matrix."""
        tags = compiled.tags[:]
        if compiled.entities:
            tags.extend(compiled.entities)

        try:
            node_id = await matrix.store_memory(
                content=compiled.content,
                node_type=compiled.node_type,
                tags=tags,
                confidence=int(compiled.confidence * 100),
                scope=self.scope,
            )
            self.node_map[compiled.source_id] = node_id
            return node_id
        except Exception as e:
            logger.error(
                f"Compiler: constellation '{compiled.source_id}' failed: {e}"
            )
            return None
