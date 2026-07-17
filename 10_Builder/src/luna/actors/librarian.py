"""
Librarian Actor (The Dude) for Luna Engine
==========================================

The Librarian files extractions into the Memory Matrix and wires
knowledge together. He doesn't just store; he connects.

Persona: The Dude from The Big Lebowski. Chill, competent, cuts
through bullshit. The Dude abides... and files things where they belong.

CRITICAL: The Dude has personality in PROCESS (logs), but OUTPUTS
are NEUTRAL (clean context packets, properly filed nodes).

> "The Dude abides." — The Dude
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, TYPE_CHECKING
import asyncio
import inspect
import json
import logging
import time

from .base import Actor, Message
from luna.core.tasks import TaskStatus
from luna.extraction.types import (
    ExtractionOutput,
    ExtractedObject,
    ExtractedEdge,
    ExtractionType,
    FilingResult,
    ConversationMode,
    FlowSignal,
    Thread,
    ThreadStatus,
    ContinuityDecision,
    ContinuityDisposition,
)
from luna.entities.models import (
    Entity,
    EntityType,
    ChangeType,
    EntityUpdate,
    EntityVersion,
)

if TYPE_CHECKING:
    from luna.engine import LunaEngine
    from luna.substrate.memory import MemoryMatrix, MemoryNode
    from luna.entities.resolution import EntityResolver

logger = logging.getLogger(__name__)


# =============================================================================
# CONTEXT BUDGET PRESETS
# =============================================================================

BUDGET_PRESETS = {
    "minimal": 1800,   # Voice mode, fast response needed
    "balanced": 3800,  # Normal conversation
    "rich": 7200,      # Deep research, complex query
}


# ── First-Run Lock-In Boost (configurable) ──────────────────────────────────
EARLY_RELATIONSHIP_TURN_THRESHOLD = 50
EARLY_IMPORTANCE_BOOST = 0.3        # default importance 0.5 → 0.8
EARLY_OWNER_IMPORTANCE_BOOST = 0.15  # extra boost for owner-mentioned nodes


# =============================================================================
# LIBRARIAN ACTOR
# =============================================================================

class LibrarianActor(Actor):
    """
    The Dude: Files extractions into Memory Matrix.

    Core functions:
    - Entity Resolution: Deduplicate ("Mark" = "Zuck" = "The Landlord")
    - Knowledge Wiring: Create explicit edges from extractions
    - Context Retrieval: Get relevant context for queries
    - Synaptic Pruning: Clean low-value edges (periodic)

    Message Types:
    - file: File an extraction into Memory Matrix
    - get_context: Retrieve context for a query
    - resolve_entity: Resolve entity to existing node or create new
    - prune: Run synaptic pruning
    - get_stats: Get filing statistics
    """

    def __init__(self, engine: Optional["LunaEngine"] = None):
        super().__init__("librarian", engine)

        # Entity resolution cache: name_lower -> node_id
        self.alias_cache: dict[str, str] = {}

        # Entity resolver (lazy init)
        self._entity_resolver: Optional["EntityResolver"] = None

        # Inference queue for deferred processing
        self.inference_queue: list[str] = []
        self.batch_threshold = 10

        # Stats
        self._filings_count = 0
        self._nodes_created = 0
        self._last_filed_node_ids: list[str] = []
        self._nodes_merged = 0
        self._edges_created = 0
        self._edges_failed = 0
        self._edges_skipped_duplicate = 0
        self._entity_resolve_failures = 0
        self._context_retrievals = 0
        self._entity_updates_filed = 0
        self._entity_versions_created = 0
        self._total_filing_time_ms = 0

        # Consciousness reference (Layer 6)
        self._consciousness = None

        # Thread management state (Layer 3)
        self._active_thread: Optional[Thread] = None
        self._active_project_slug: Optional[str] = None
        self._thread_cache: dict[str, Thread] = {}
        self._max_cached_threads: int = 20
        self._threads_created: int = 0
        self._threads_parked: int = 0
        self._threads_resumed: int = 0
        self._threads_closed: int = 0

        logger.info("Librarian (The Dude) initialized")

    # =========================================================================
    # MESSAGE HANDLING
    # =========================================================================

    async def handle(self, msg: Message) -> Optional[dict]:
        """Process messages from mailbox."""
        logger.debug(f"The Dude received: {msg.type}")

        match msg.type:
            case "file":
                await self._handle_file(msg)

            case "entity_update":
                await self._handle_entity_update(msg)

            case "prune":
                return await self._handle_prune(msg)

            case "set_project_context":
                await self._handle_set_project_context(msg)

            case "clear_project_context":
                await self._handle_clear_project_context(msg)

            case "get_active_thread":
                await self._handle_get_active_thread(msg)

            case "get_parked_threads":
                await self._handle_get_parked_threads(msg)

            case _:
                logger.warning(f"The Dude: Unknown message type: {msg.type}")

    async def _handle_file(self, msg: Message) -> None:
        """
        File an extraction into Memory Matrix.

        Payload: ExtractionOutput dict (from Scribe)
        """
        payload = msg.payload or {}

        # Parse extraction from dict
        extraction = ExtractionOutput.from_dict(payload)

        # File extraction content (safe on empty — just returns empty result)
        result = await self._wire_extraction(extraction)
        self._last_filed_node_ids = list(result.nodes_created)

        if not extraction.is_empty():
            logger.info(
                f"The Dude: Filed {len(result.nodes_created)} new nodes, "
                f"merged {len(result.nodes_merged)}, "
                f"created {len(result.edges_created)} edges"
            )

            # Emit extraction_batch summary for live knowledge feed
            from luna.core.event_bus import event_bus, KnowledgeEvent
            event_bus.emit("knowledge", KnowledgeEvent(
                type="extraction_batch",
                payload={
                    "session_id": extraction.source_id,
                    "facts_count": len(result.nodes_created),
                    "edges_count": len(result.edges_created),
                    "entities_count": len(extraction.objects),
                },
            ))

        # Process flow signal (Layer 3 — thread management)
        # MUST run even on empty extractions — the Scribe sends flow-only
        # messages for RECALIBRATION/AMEND that carry no objects or edges.
        if extraction.flow_signal:
            try:
                await self._process_flow_signal(extraction.flow_signal, extraction, result)
            except Exception as e:
                logger.error(f"The Dude: Flow signal processing failed: {e}")

        # Send result back if requested
        if msg.reply_to:
            await self.send_to_engine("filing_result", result.to_dict())

    async def _handle_entity_update(self, msg: Message) -> None:
        """
        Handle entity update from Scribe.

        Payload: EntityUpdate dict (from Scribe)
        - update_type: create, update, synthesize
        - entity_id: Optional entity ID (for updates to existing)
        - name: Entity name (required for create)
        - entity_type: person, persona, place, project
        - facts: Dictionary of facts to add/update
        - source: Source of the update
        """
        payload = msg.payload or {}

        # Parse the update type
        update_type_str = payload.get("update_type", "update")
        try:
            update_type = ChangeType(update_type_str) if update_type_str in [e.value for e in ChangeType] else ChangeType.UPDATE
        except ValueError:
            update_type = ChangeType.UPDATE

        # Parse entity type
        entity_type_str = payload.get("entity_type", "person")
        try:
            entity_type = EntityType(entity_type_str) if entity_type_str in [e.value for e in EntityType] else EntityType.PERSON
        except ValueError:
            entity_type = EntityType.PERSON

        entity_update = EntityUpdate(
            update_type=update_type,
            entity_id=payload.get("entity_id"),
            name=payload.get("name"),
            entity_type=entity_type,
            facts=payload.get("facts", {}),
            source=payload.get("source"),
        )

        # File the entity update
        result = await self._file_entity_update(entity_update)

        if result:
            logger.info(f"The Dude: Filed entity update for '{entity_update.name}'")
            self._entity_updates_filed += 1
        else:
            logger.warning(f"The Dude: Failed to file entity update for '{entity_update.name}'")

        # Send result if requested
        if msg.reply_to:
            await self.send_to_engine("entity_update_result", {
                "success": result is not None,
                "entity_id": result.id if result else None,
                "name": entity_update.name,
            })

    async def _handle_prune(self, msg: Message) -> dict:
        """Run synaptic pruning on low-value connections and drifting nodes."""
        payload = msg.payload or {}
        confidence_threshold = payload.get("confidence_threshold", 0.3)
        age_days = payload.get("age_days", 30)
        prune_nodes = payload.get("prune_nodes", True)
        max_prune_nodes = payload.get("max_prune_nodes", 100)

        # Prune edges
        edge_result = await self._prune_edges(confidence_threshold, age_days)
        logger.info(f"The Dude: Pruned {edge_result['pruned']} edges")

        # Prune drifting nodes if requested
        node_result = {"pruned": 0, "preserved": 0, "candidates": 0}
        if prune_nodes:
            node_result = await self._prune_drifting_nodes(age_days, max_prune_nodes)

        # Close stale parked threads (>7 days, no open tasks)
        threads_closed = 0
        stale_cutoff = datetime.now().timestamp() - (7 * 24 * 3600)
        for thread_id, thread in list(self._thread_cache.items()):
            if thread.status != ThreadStatus.PARKED:
                continue
            if thread.parked_at and thread.parked_at.timestamp() < stale_cutoff:
                if not thread.open_tasks:
                    thread.status = ThreadStatus.CLOSED
                    thread.closed_at = datetime.now()
                    await self._update_thread_node(thread)
                    self._threads_closed += 1
                    threads_closed += 1
                    logger.info(f"The Dude: Closed stale thread '{thread.topic}'")

        combined_result = {
            "edges_pruned": edge_result["pruned"],
            "edges_preserved": edge_result["preserved"],
            "nodes_pruned": node_result["pruned"],
            "nodes_preserved": node_result["preserved"],
            "drifting_candidates": node_result["candidates"],
            "threads_closed": threads_closed,
        }

        await self.send_to_engine("prune_result", combined_result)
        return combined_result

    # =========================================================================
    # ENTITY RESOLUTION
    # =========================================================================

    async def _resolve_entity(
        self,
        name: str,
        entity_type: str,
        source_id: str = "",
    ) -> str:
        """
        Resolve entity to existing node or create new.

        Resolution order:
        1. Alias cache (O(1))
        2. Exact DB match
        3. Create new if not found
        """
        name_lower = name.lower().strip()

        # 1. Check alias cache
        if name_lower in self.alias_cache:
            logger.debug(f"The Dude: Cache hit for '{name}'")
            entity_id = self.alias_cache[name_lower]
            # Bump access_count on repeated mention → drives lock-in
            matrix = await self._get_matrix()
            if matrix:
                try:
                    await matrix.record_access(entity_id)
                except Exception:
                    pass  # Non-critical; don't block entity resolution
            return entity_id

        # 2. Check exact DB match via Matrix actor
        matrix = await self._get_matrix()
        if matrix:
            existing = await self._find_existing_node(matrix, name, entity_type)
            if existing:
                self.alias_cache[name_lower] = existing
                self._nodes_merged += 1
                # Bump access_count on re-discovered entity → drives lock-in
                try:
                    await matrix.record_access(existing)
                except Exception:
                    pass
                logger.debug(f"The Dude: Found existing node for '{name}'")
                return existing

        # 3. Create new node
        try:
            node_id = await self._create_node(name, entity_type, source_id)
            self.alias_cache[name_lower] = node_id
            self._nodes_created += 1
            logger.debug(f"ENTITY_NEW: '{name}' -> {node_id} (type={entity_type})")
            return node_id
        except Exception as e:
            logger.error(
                f"ENTITY_FAIL: Could not create node for '{name}' | "
                f"type={entity_type} source={source_id} | error={e}"
            )
            self._entity_resolve_failures += 1
            # Return a fallback ID so edge creation can still attempt
            import uuid
            fallback_id = f"unresolved_{uuid.uuid4().hex[:8]}"
            logger.warning(f"ENTITY_FALLBACK: Using {fallback_id} for '{name}'")
            return fallback_id

    async def _find_existing_node(
        self,
        matrix: "MemoryMatrix",
        name: str,
        entity_type: str,
    ) -> Optional[str]:
        """Find existing node by name and type."""
        # Search for exact match
        nodes = await matrix.search_nodes(name, node_type=entity_type, limit=1)

        for node in nodes:
            # Check for exact content match
            if node.content.lower() == name.lower():
                return node.id

        return None

    async def _create_node(
        self,
        content: str,
        node_type: str,
        source_id: str = "",
    ) -> str:
        """Create a new memory node."""
        matrix = await self._get_matrix()
        if not matrix:
            # Fallback: generate ID but can't persist
            import uuid
            return str(uuid.uuid4())[:12]

        node_id = await matrix.add_node(
            node_type=node_type,
            content=content,
            source=source_id,
            confidence=1.0,
            importance=0.5,
        )

        return node_id

    # =========================================================================
    # ENTITY FILING
    # =========================================================================

    async def _get_entity_resolver(self) -> Optional["EntityResolver"]:
        """Get or create EntityResolver instance."""
        if self._entity_resolver is not None:
            return self._entity_resolver

        # Try to get database from Matrix actor
        matrix = await self._get_matrix()
        if not matrix or not hasattr(matrix, 'db'):
            logger.warning("The Dude: Can't create EntityResolver, no database available")
            return None

        try:
            from luna.entities.resolution import EntityResolver
            self._entity_resolver = EntityResolver(matrix.db)
            logger.debug("The Dude: EntityResolver initialized")
            return self._entity_resolver
        except Exception as e:
            logger.error(f"The Dude: Failed to create EntityResolver: {e}")
            return None

    async def _file_entity_update(self, update: EntityUpdate) -> Optional[Entity]:
        """
        File an entity update to the database.

        Creates or updates an entity based on the update type.
        Always creates a new version record (append-only).

        Args:
            update: EntityUpdate from Scribe

        Returns:
            Updated Entity if successful, None otherwise
        """
        import json
        from datetime import datetime

        resolver = await self._get_entity_resolver()
        if not resolver:
            logger.error("The Dude: No EntityResolver available for filing")
            return None

        try:
            # Resolve or create the entity
            name = update.name or ""
            entity_type = update.entity_type.value if hasattr(update.entity_type, 'value') else str(update.entity_type)

            if update.update_type == ChangeType.CREATE or not update.entity_id:
                # Use resolve_or_create for new entities
                entity = await resolver.resolve_or_create(
                    name=name,
                    entity_type=entity_type,
                    source=update.source or "scribe",
                )
            else:
                # Get existing entity by ID
                entity = await resolver.get_entity(update.entity_id)
                if not entity:
                    # Try to resolve by name
                    entity = await resolver.resolve_entity(name)

                if not entity:
                    logger.warning(f"The Dude: Entity not found: {update.entity_id or name}")
                    return None

                # resolver.get_entity / resolve_entity return EntityDict; promote to Entity
                # so the dataclass-attribute access below is uniform with the create branch.
                entity = Entity.from_row(entity)

            # Merge new facts with existing
            merged_facts = dict(entity.core_facts)
            merged_facts.update(update.facts)

            # Update entity record
            now = datetime.now().isoformat()
            new_version = entity.current_version + 1

            await resolver.db.execute(
                """
                UPDATE entities
                SET core_facts = ?, current_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(merged_facts),
                    new_version,
                    now,
                    entity.id,
                )
            )

            # Create version record (append-only)
            change_type = update.update_type.value if hasattr(update.update_type, 'value') else str(update.update_type)
            change_summary = f"Added facts: {', '.join(update.facts.keys())}" if update.facts else "Update"

            await resolver.db.execute(
                """
                INSERT INTO entity_versions (
                    entity_id, version, core_facts, full_profile, voice_config,
                    change_type, change_summary, changed_by, change_source,
                    created_at, valid_from, valid_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    new_version,
                    json.dumps(merged_facts),
                    entity.full_profile,
                    json.dumps(entity.voice_config) if entity.voice_config else None,
                    change_type,
                    change_summary,
                    "scribe",
                    update.source,
                    now,
                    now,
                    None,  # valid_until = NULL (current version)
                )
            )

            # Close out previous version
            await resolver.db.execute(
                """
                UPDATE entity_versions
                SET valid_until = ?
                WHERE entity_id = ? AND version = ?
                """,
                (now, entity.id, entity.current_version)
            )

            self._entity_versions_created += 1

            # Return updated entity
            entity.core_facts = merged_facts
            entity.current_version = new_version
            entity.updated_at = now

            logger.debug(f"The Dude: Filed entity update for '{entity.id}' v{new_version}")
            return entity

        except Exception as e:
            logger.error(f"The Dude: Failed to file entity update: {e}")
            return None

    async def _rollback_entity(
        self,
        entity_id: str,
        target_version: Optional[int] = None,
        reason: str = "Rollback requested",
    ) -> bool:
        """
        Rollback an entity to a previous version.

        Args:
            entity_id: Entity ID to rollback
            target_version: Target version (None = previous version)
            reason: Reason for rollback

        Returns:
            True if rollback successful
        """
        import json
        from datetime import datetime

        resolver = await self._get_entity_resolver()
        if not resolver:
            logger.error("The Dude: No EntityResolver available for rollback")
            return False

        try:
            # Get current entity
            entity = await resolver.get_entity(entity_id)
            if not entity:
                logger.warning(f"The Dude: Entity not found for rollback: {entity_id}")
                return False

            # resolver.get_entity returns EntityDict; promote to Entity for attribute access
            entity = Entity.from_row(entity)

            # Determine target version
            if target_version is None:
                target_version = entity.current_version - 1

            if target_version < 1:
                logger.warning(f"The Dude: Invalid target version {target_version}")
                return False

            # Get target version record
            row = await resolver.db.fetchone(
                """
                SELECT core_facts, full_profile, voice_config
                FROM entity_versions
                WHERE entity_id = ? AND version = ?
                """,
                (entity_id, target_version)
            )

            if not row:
                logger.warning(f"The Dude: Version {target_version} not found for {entity_id}")
                return False

            # Parse version data
            target_core_facts = json.loads(row[0]) if row[0] else {}
            target_full_profile = row[1]
            target_voice_config = json.loads(row[2]) if row[2] else {}

            # Create new version with rollback data
            now = datetime.now().isoformat()
            new_version = entity.current_version + 1

            # Update entity to rolled-back state
            await resolver.db.execute(
                """
                UPDATE entities
                SET core_facts = ?, full_profile = ?, voice_config = ?,
                    current_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(target_core_facts),
                    target_full_profile,
                    json.dumps(target_voice_config) if target_voice_config else None,
                    new_version,
                    now,
                    entity_id,
                )
            )

            # Create rollback version record
            await resolver.db.execute(
                """
                INSERT INTO entity_versions (
                    entity_id, version, core_facts, full_profile, voice_config,
                    change_type, change_summary, changed_by, change_source,
                    created_at, valid_from, valid_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    new_version,
                    json.dumps(target_core_facts),
                    target_full_profile,
                    json.dumps(target_voice_config) if target_voice_config else None,
                    "rollback",
                    f"Rolled back to v{target_version}: {reason}",
                    "librarian",
                    None,
                    now,
                    now,
                    None,
                )
            )

            # Close out previous version
            await resolver.db.execute(
                """
                UPDATE entity_versions
                SET valid_until = ?
                WHERE entity_id = ? AND version = ?
                """,
                (now, entity_id, entity.current_version)
            )

            self._entity_versions_created += 1
            logger.info(f"The Dude: Rolled back '{entity_id}' to v{target_version}")
            return True

        except Exception as e:
            logger.error(f"The Dude: Failed to rollback entity: {e}")
            return False

    # =========================================================================
    # KNOWLEDGE WIRING
    # =========================================================================

    async def _wire_extraction(
        self,
        extraction: ExtractionOutput,
    ) -> FilingResult:
        """
        Wire extraction into Memory Matrix.

        1. Resolve/create nodes for each extracted object
        2. Create edges between entities
        """
        start_time = time.monotonic()

        result = FilingResult()

        # 1. Process objects - store as memory nodes, resolve mentioned entities
        matrix = await self._get_matrix()

        # Early-relationship importance boost
        early = await self._is_early_relationship()

        # Build canonical `luna:{modality}:{session_id}` source tag so memory
        # nodes carry their origin modality. Falls back to the raw source_id
        # when modality wasn't stamped (legacy callers) — preserves existing
        # provenance without losing the session id.
        modality = getattr(extraction, "modality", None) or "text"
        source_tag = f"luna:{modality}:{extraction.source_id}" if extraction.source_id else extraction.source_id

        for obj in extraction.objects:
            node_type = obj.type.value if hasattr(obj.type, 'value') else str(obj.type)

            # Store the extracted object as a memory node (not as an entity)
            if matrix:
                importance = 0.5  # default
                if early:
                    importance = min(1.0, importance + EARLY_IMPORTANCE_BOOST)
                    from luna.core.owner import get_owner
                    owner_name = (get_owner().display_name or "").lower()
                    if owner_name and owner_name in obj.content.lower():
                        importance = min(1.0, importance + EARLY_OWNER_IMPORTANCE_BOOST)

                node_id = await matrix.add_node(
                    node_type=node_type,
                    content=obj.content,
                    source=source_tag,
                    confidence=obj.confidence,
                    importance=importance,
                    metadata=obj.metadata if hasattr(obj, 'metadata') else None,
                )
                result.nodes_created.append(node_id)
                self._nodes_created += 1

                # Emit fact_extracted event for live knowledge feed
                from luna.core.event_bus import event_bus, KnowledgeEvent
                event_bus.emit("knowledge", KnowledgeEvent(
                    type="fact_extracted",
                    payload={
                        "node_id": node_id,
                        "node_type": node_type,
                        "content": obj.content,
                        "confidence": obj.confidence,
                        "entities": obj.entities,
                    },
                ))

                # Layer 4: Track ACTION and OUTCOME node IDs for task ledger
                if obj.type == ExtractionType.ACTION:
                    result.action_node_ids.append(node_id)
                elif obj.type == ExtractionType.OUTCOME:
                    result.outcome_node_ids.append(node_id)
            else:
                import uuid
                node_id = str(uuid.uuid4())[:12]
                result.nodes_created.append(node_id)

                # Layer 4: Track ACTION and OUTCOME node IDs for task ledger
                if obj.type == ExtractionType.ACTION:
                    result.action_node_ids.append(node_id)
                elif obj.type == ExtractionType.OUTCOME:
                    result.outcome_node_ids.append(node_id)

            # Resolve mentioned entities (these ARE entity names)
            for entity_name in obj.entities:
                entity_id = await self._resolve_entity(
                    name=entity_name,
                    entity_type="ENTITY",
                    source_id=extraction.source_id,
                )
                # Create MENTIONS edge from the memory node to the entity
                await self._create_edge(
                    from_id=node_id,
                    to_id=entity_id,
                    edge_type="MENTIONS",
                    confidence=obj.confidence,
                )

        # 2. Create edges
        for edge in extraction.edges:
            try:
                from_id = await self._resolve_entity(
                    edge.from_ref,
                    "ENTITY",
                    extraction.source_id,
                )
                to_id = await self._resolve_entity(
                    edge.to_ref,
                    "ENTITY",
                    extraction.source_id,
                )

                # Create edge in graph
                edge_created = await self._create_edge(
                    from_id=from_id,
                    to_id=to_id,
                    edge_type=edge.edge_type,
                    confidence=edge.confidence,
                )

                if edge_created:
                    result.edges_created.append(f"{from_id}->{to_id}")
                    self._edges_created += 1

                    # Emit edge_created event for live knowledge feed
                    from luna.core.event_bus import event_bus, KnowledgeEvent
                    event_bus.emit("knowledge", KnowledgeEvent(
                        type="edge_created",
                        payload={
                            "from_id": from_id,
                            "to_id": to_id,
                            "edge_type": edge.edge_type,
                        },
                    ))
                else:
                    result.edges_skipped.append(
                        f"duplicate: {edge.from_ref}->{edge.to_ref}"
                    )

            except Exception as e:
                logger.error(
                    f"EDGE_WIRE_FAIL: Exception in edge wiring | "
                    f"from_ref={edge.from_ref} to_ref={edge.to_ref} "
                    f"type={edge.edge_type} | error={type(e).__name__}: {e}"
                )
                self._edges_failed += 1
                result.edges_skipped.append(f"error: {type(e).__name__}: {e}")

        # Update stats
        filing_time_ms = int((time.monotonic() - start_time) * 1000)
        result.filing_time_ms = filing_time_ms
        self._filings_count += 1
        self._total_filing_time_ms += filing_time_ms

        return result

    async def _create_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        confidence: float = 1.0,
    ) -> bool:
        """
        Create edge between nodes.

        Returns True if created, False if duplicate.
        Logs all failures with full context for debugging.
        """
        # Get matrix actor to access graph
        if not self.engine:
            logger.error(
                "EDGE_FAIL: No engine reference | "
                f"from={from_id} to={to_id} type={edge_type}"
            )
            self._edges_failed += 1
            return False

        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor:
            logger.error(
                "EDGE_FAIL: Matrix actor not found | "
                f"from={from_id} to={to_id} type={edge_type}"
            )
            self._edges_failed += 1
            return False

        # Use matrix actor's graph if available
        if not hasattr(matrix_actor, "_graph") or not matrix_actor._graph:
            logger.error(
                "EDGE_FAIL: Matrix actor has no graph | "
                f"from={from_id} to={to_id} type={edge_type} "
                f"has_attr={hasattr(matrix_actor, '_graph')} "
                f"graph_val={getattr(matrix_actor, '_graph', 'MISSING')}"
            )
            self._edges_failed += 1
            return False

        graph = matrix_actor._graph

        # Check if edge already exists.
        # Compatibility note:
        # - Some graph implementations expose has_edge(from_id, to_id)
        # - Others support relationship-scoped duplicate checks
        # Prefer relationship-aware checks when available, otherwise fallback
        # to a 2-arg has_edge() probe.
        edge_exists = False
        get_edges_fn = getattr(graph, "get_edges", None)
        if inspect.iscoroutinefunction(get_edges_fn):
            try:
                current_edges = await get_edges_fn(from_id)
                target_rel = (edge_type or "").strip().upper()
                edge_exists = any(
                    e.to_id == to_id and str(getattr(e, "relationship", "")).strip().upper() == target_rel
                    for e in current_edges
                )
            except Exception as e:
                logger.debug(
                    f"EDGE_WARN: Relationship duplicate check failed, falling back to has_edge | "
                    f"from={from_id} to={to_id} type={edge_type} "
                    f"error_type={type(e).__name__} error={e}"
                )

        if not edge_exists:
            try:
                edge_exists = graph.has_edge(from_id, to_id)
            except TypeError:
                # Fallback for older/alternate signatures.
                edge_exists = graph.has_edge(from_id, to_id, relationship=edge_type)

        if edge_exists:
            logger.debug(
                f"EDGE_SKIP: Duplicate | from={from_id} to={to_id} type={edge_type}"
            )
            self._edges_skipped_duplicate += 1
            return False

        # Create the edge
        try:
            await graph.add_edge(
                from_id=from_id,
                to_id=to_id,
                relationship=edge_type,
                strength=confidence,
            )
            logger.info(
                f"EDGE_OK: {from_id} --[{edge_type} @ {confidence:.2f}]--> {to_id}"
            )
            return True
        except TypeError as e:
            # This is the exact error class that killed edges before.
            logger.critical(
                f"EDGE_CRITICAL: TypeError in graph.add_edge — API CONTRACT VIOLATION | "
                f"from={from_id} to={to_id} type={edge_type} confidence={confidence} | "
                f"error={e}"
            )
            self._edges_failed += 1
            return False
        except Exception as e:
            logger.error(
                f"EDGE_FAIL: Unexpected error in graph.add_edge | "
                f"from={from_id} to={to_id} type={edge_type} | "
                f"error_type={type(e).__name__} error={e}"
            )
            self._edges_failed += 1
            return False

    # =========================================================================
    # CONTEXT RETRIEVAL
    # =========================================================================

    async def _get_context(
        self,
        query: str,
        max_tokens: int,
        node_types: Optional[list[str]] = None,
    ) -> list["MemoryNode"]:
        """
        Get relevant context for a query within token budget.

        Uses MatrixActor for retrieval.
        """
        matrix = await self._get_matrix()
        if not matrix:
            return []

        # Use matrix's get_context method
        nodes = await matrix.get_context(
            query=query,
            max_tokens=max_tokens,
            node_types=node_types,
        )

        return nodes

    # =========================================================================
    # SYNAPTIC PRUNING
    # =========================================================================

    async def _prune_edges(
        self,
        confidence_threshold: float = 0.3,
        age_days: int = 30,
    ) -> dict:
        """
        Remove stale, low-confidence edges.

        Prunes edges that:
        - Have confidence below threshold
        - Are older than age_days
        - Connect to drifting nodes
        """
        result = {"pruned": 0, "preserved": 0}

        # Get matrix actor's graph
        if not self.engine:
            return result

        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor or not hasattr(matrix_actor, "_graph"):
            return result

        graph = matrix_actor._graph
        if not graph:
            return result

        # Iterate NetworkX edges via graph.graph property
        cutoff = datetime.now().timestamp() - (age_days * 24 * 3600)
        edges_to_prune = []

        for u, v, key, data in graph.graph.edges(data=True, keys=True):
            strength = data.get("strength", 1.0)
            # created_at is stored as ISO string by add_edge, parse it
            created_raw = data.get("created_at")
            if isinstance(created_raw, str):
                try:
                    created = datetime.fromisoformat(created_raw).timestamp()
                except ValueError:
                    created = datetime.now().timestamp()
            elif isinstance(created_raw, (int, float)):
                created = float(created_raw)
            else:
                created = datetime.now().timestamp()

            if strength >= confidence_threshold:
                result["preserved"] += 1
                continue

            if created > cutoff:
                result["preserved"] += 1
                continue

            edges_to_prune.append((u, v, key))

        for from_id, to_id, relationship in edges_to_prune:
            try:
                await graph.remove_edge(from_id, to_id, relationship)
                result["pruned"] += 1
            except Exception:
                result["preserved"] += 1

        return result

    async def _prune_drifting_nodes(
        self,
        age_days: int = 30,
        max_prune: int = 100,
    ) -> dict:
        """
        Remove nodes that are drifting and old.

        Only prunes nodes that:
        - Have lock_in_state = 'drifting'
        - Are older than age_days
        - Have zero reinforcement_count (never explicitly marked important)

        Args:
            age_days: Minimum age for pruning
            max_prune: Maximum nodes to prune in one pass

        Returns:
            Dict with 'pruned' and 'preserved' counts
        """
        result = {"pruned": 0, "preserved": 0, "candidates": 0}

        matrix = await self._get_matrix()
        if not matrix:
            return result

        # Get drifting nodes
        drifting = await matrix.get_drifting_nodes(limit=max_prune * 2)
        result["candidates"] = len(drifting)

        cutoff = datetime.now().timestamp() - (age_days * 24 * 3600)

        for node in drifting:
            # Never prune reinforced nodes
            if node.reinforcement_count > 0:
                result["preserved"] += 1
                continue

            # Skip recent nodes
            if node.created_at:
                node_ts = node.created_at.timestamp()
                if node_ts > cutoff:
                    result["preserved"] += 1
                    continue

            # Prune this node
            try:
                deleted = await matrix.delete_node(node.id)
                if deleted:
                    result["pruned"] += 1
                    logger.debug(f"The Dude: Pruned drifting node {node.id}")
                else:
                    result["preserved"] += 1
            except Exception as e:
                logger.warning(f"The Dude: Failed to prune {node.id}: {e}")
                result["preserved"] += 1

            # Respect max_prune limit
            if result["pruned"] >= max_prune:
                break

        logger.info(
            f"The Dude: Pruning complete - {result['pruned']} pruned, "
            f"{result['preserved']} preserved out of {result['candidates']} drifting"
        )
        return result

    # =========================================================================
    # THREAD MANAGEMENT (Layer 3)
    # =========================================================================

    async def _process_flow_signal(
        self,
        signal: FlowSignal,
        extraction: ExtractionOutput,
        filing_result: FilingResult,
    ) -> None:
        """
        React to Ben's flow signal. Create, park, or resume threads.

        Runs the continuity pre-gate first so thread attachment is an explicit,
        testable decision rather than a side effect of conversational mode.
        """
        decision = await self._detect_continuity(signal)

        logger.info(
            f"The Dude: continuity decision — disposition={decision.disposition.value} "
            f"mode={signal.mode.value} entity_overlap={signal.entity_overlap:.2f} "
            f"parked_overlap={decision.parked_overlap:.2f} "
            f"target={decision.target_thread_id} | {decision.rationale}"
        )

        if decision.disposition == ContinuityDisposition.ATTACH_ACTIVE:
            if signal.mode == ConversationMode.AMEND:
                await self._handle_amend(signal, extraction, filing_result)
            else:
                await self._handle_flow_continue(signal, extraction, filing_result)
        else:
            # RESUME_PARKED or CREATE_NEW — route through recalibration handler
            # with the pre-gate decision so no second resume-vs-new choice is made.
            await self._handle_recalibration(signal, extraction, filing_result, decision)

    async def _detect_continuity(self, signal: FlowSignal) -> ContinuityDecision:
        """
        Continuity pre-gate. Yields an explicit thread-attachment disposition
        separate from Scribe's conversational mode.

        Compares against signal.entity_overlap (pure Jaccard), not continuity_score
        — they are equal today, but entity_overlap is the contract we want to
        preserve if continuity_score becomes composite later.

        Lazy on parked lookup: only probes _find_resumable_thread in rules 1 and 4.
        """
        mode = signal.mode
        entity_overlap = signal.entity_overlap

        # Rule 1: No active thread comes first.
        # AMEND-with-no-active is handled here too — AMEND is only meaningful
        # relative to an existing flow, so without one it degenerates to the
        # same question as any other mode: resume something parked, or start
        # fresh.
        if self._active_thread is None:
            parked, parked_overlap = await self._find_resumable_thread(signal.topic_entities)
            if parked is not None and parked_overlap >= 0.4:
                return ContinuityDecision(
                    disposition=ContinuityDisposition.RESUME_PARKED,
                    signal_mode=mode,
                    entity_overlap=entity_overlap,
                    parked_overlap=parked_overlap,
                    target_thread_id=parked.id,
                    rationale=(
                        f"rule 1: no active thread, parked_overlap {parked_overlap:.2f} >= 0.4"
                    ),
                )
            return ContinuityDecision(
                disposition=ContinuityDisposition.CREATE_NEW,
                signal_mode=mode,
                entity_overlap=entity_overlap,
                parked_overlap=parked_overlap,
                target_thread_id=None,
                rationale=(
                    f"rule 1: no active thread, no parked match >= 0.4 "
                    f"(best parked_overlap {parked_overlap:.2f})"
                ),
            )

        # Rule 2: AMEND with an active thread sticks — course corrections are
        # conservative inside the current flow.
        if mode == ConversationMode.AMEND:
            return ContinuityDecision(
                disposition=ContinuityDisposition.ATTACH_ACTIVE,
                signal_mode=mode,
                entity_overlap=entity_overlap,
                parked_overlap=0.0,
                target_thread_id=self._active_thread.id,
                rationale="rule 2: AMEND + active thread",
            )

        # Rule 3: FLOW with enough overlap continues on the active thread.
        # 0.3 matches Scribe's flow-vs-shift cutoff in _assess_flow.
        if mode == ConversationMode.FLOW and entity_overlap >= 0.3:
            return ContinuityDecision(
                disposition=ContinuityDisposition.ATTACH_ACTIVE,
                signal_mode=mode,
                entity_overlap=entity_overlap,
                parked_overlap=0.0,
                target_thread_id=self._active_thread.id,
                rationale=(
                    f"rule 3: FLOW + active + entity_overlap {entity_overlap:.2f} >= 0.3"
                ),
            )

        # Rule 4: Recalibration (or FLOW-with-low-overlap) can resume parked
        # only if parked beats active continuity.
        parked, parked_overlap = await self._find_resumable_thread(signal.topic_entities)
        if parked is not None and parked_overlap >= 0.4 and parked_overlap > entity_overlap:
            return ContinuityDecision(
                disposition=ContinuityDisposition.RESUME_PARKED,
                signal_mode=mode,
                entity_overlap=entity_overlap,
                parked_overlap=parked_overlap,
                target_thread_id=parked.id,
                rationale=(
                    f"rule 4: parked_overlap {parked_overlap:.2f} > "
                    f"entity_overlap {entity_overlap:.2f}"
                ),
            )

        # Rule 5: Otherwise create new.
        return ContinuityDecision(
            disposition=ContinuityDisposition.CREATE_NEW,
            signal_mode=mode,
            entity_overlap=entity_overlap,
            parked_overlap=parked_overlap,
            target_thread_id=None,
            rationale=(
                f"rule 5: no parked match >= 0.4 beating entity_overlap "
                f"{entity_overlap:.2f} (best parked_overlap {parked_overlap:.2f})"
            ),
        )

    async def _handle_flow_continue(
        self,
        signal: FlowSignal,
        extraction: ExtractionOutput,
        filing_result: FilingResult,
    ) -> None:
        """Conversation continuing on-topic. Accumulate into active thread."""
        if not self._active_thread:
            # First substantive turn — create initial thread
            self._active_thread = await self._create_thread(
                topic=signal.current_topic,
                entities=signal.topic_entities,
            )
            logger.info(
                f"The Dude: Started thread '{self._active_thread.topic}' "
                f"({self._active_thread.id})"
            )
            await self._maybe_auto_attach_active_thread_to_topology()
            self._notify_consciousness()
            return

        # Update active thread
        self._active_thread.turn_count += 1

        # Merge new entities (additive) and create INVOLVES edges
        for entity in signal.topic_entities:
            if entity not in self._active_thread.entities:
                self._active_thread.entities.append(entity)
                # Resolve and create INVOLVES edge
                try:
                    entity_id = await self._resolve_entity(entity, "ENTITY")
                    if entity_id not in self._active_thread.entity_node_ids:
                        self._active_thread.entity_node_ids.append(entity_id)
                        await self._create_edge(
                            from_id=self._active_thread.id,
                            to_id=entity_id,
                            edge_type="INVOLVES",
                            confidence=1.0,
                        )
                except Exception as e:
                    logger.debug(f"The Dude: Could not create INVOLVES edge: {e}")

        # Track open tasks from filing result (real node IDs — Layer 4)
        for action_id in filing_result.action_node_ids:
            if action_id not in self._active_thread.open_tasks:
                self._active_thread.open_tasks.append(action_id)
                # Create HAS_OPEN_TASK edge: THREAD → ACTION
                try:
                    await self._create_edge(
                        from_id=self._active_thread.id,
                        to_id=action_id,
                        edge_type="HAS_OPEN_TASK",
                        confidence=1.0,
                    )
                except Exception as e:
                    logger.debug(f"The Dude: Could not create HAS_OPEN_TASK edge: {e}")
                # Dual-write: mirror the action into the canonical tasks table
                # so the dual-agent board shows live conversational work.
                # Thread.open_tasks stays as the shadow until a retirement slice.
                await self._create_canonical_task_for_action(action_id)

        # Resolve open tasks when outcomes arrive
        if filing_result.outcome_node_ids and self._active_thread.open_tasks:
            await self._resolve_open_tasks(filing_result)

        logger.debug(
            f"The Dude: Flow continues in '{self._active_thread.topic}' "
            f"(turn {self._active_thread.turn_count}, "
            f"{len(self._active_thread.entities)} entities, "
            f"{len(self._active_thread.open_tasks)} open tasks)"
        )
        self._notify_consciousness()

    async def _handle_recalibration(
        self,
        signal: FlowSignal,
        extraction: ExtractionOutput,
        filing_result: FilingResult,
        decision: ContinuityDecision,
    ) -> None:
        """
        Park current thread, then resume or create based on the pre-gate decision.

        Trusts the ContinuityDecision from _detect_continuity — does NOT re-run
        parked-thread matching. On RESUME_PARKED the handler rehydrates the
        thread by id (cache → Matrix) and raises loudly on miss. Silent fallback
        to CREATE_NEW is a bug, not a feature: the pre-gate already committed to
        a specific id.
        """
        parked_thread = None

        # 1. Park current thread (if one exists)
        if self._active_thread:
            parked_thread = self._active_thread
            await self._park_thread(self._active_thread)
            logger.info(
                f"The Dude: Parked thread '{self._active_thread.topic}' "
                f"({self._active_thread.turn_count} turns, "
                f"{len(self._active_thread.open_tasks)} open tasks)"
            )

        resumed = False

        # 2. Honour the pre-gate decision — no second resume-vs-new choice here.
        if decision.disposition == ContinuityDisposition.RESUME_PARKED:
            target_id = decision.target_thread_id
            if not target_id:
                raise RuntimeError(
                    "The Dude: RESUME_PARKED decision missing target_thread_id — "
                    "continuity pre-gate produced an invalid decision"
                )

            thread = self._thread_cache.get(target_id)
            if thread is None:
                matrix = await self._get_matrix()
                if matrix is None:
                    logger.error(
                        f"The Dude: cannot rehydrate parked thread {target_id} — "
                        f"no matrix available"
                    )
                    raise RuntimeError(
                        f"The Dude: RESUME_PARKED target {target_id} not in cache "
                        f"and no matrix to fetch from"
                    )
                node = await matrix.get_node(target_id)
                if node is None:
                    logger.error(
                        f"The Dude: cannot rehydrate parked thread {target_id} — "
                        f"node not found in matrix"
                    )
                    raise RuntimeError(
                        f"The Dude: RESUME_PARKED target {target_id} missing from matrix"
                    )
                try:
                    thread = Thread.from_dict(json.loads(node.content))
                    thread.id = node.id
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.error(
                        f"The Dude: cannot rehydrate parked thread {target_id} — "
                        f"malformed node content: {e}"
                    )
                    raise RuntimeError(
                        f"The Dude: RESUME_PARKED target {target_id} has malformed content"
                    ) from e
                self._thread_cache[thread.id] = thread

            await self._resume_thread(thread)
            self._active_thread = thread
            resumed = True
            logger.info(
                f"The Dude: Resumed thread '{thread.topic}' "
                f"(was parked, {thread.resume_count} resumes)"
            )

        elif decision.disposition == ContinuityDisposition.CREATE_NEW:
            self._active_thread = await self._create_thread(
                topic=signal.current_topic,
                entities=signal.topic_entities,
            )
            logger.info(
                f"The Dude: New thread '{self._active_thread.topic}' "
                f"({self._active_thread.id})"
            )

        else:
            # ATTACH_ACTIVE should never reach this handler — _process_flow_signal
            # routes it to _handle_flow_continue / _handle_amend instead.
            raise RuntimeError(
                f"The Dude: _handle_recalibration received unexpected disposition "
                f"{decision.disposition.value}"
            )

        # 3. Create thread-to-thread edges (Level 2)
        if parked_thread and self._active_thread and parked_thread.id != self._active_thread.id:
            parked_set = set(e.lower() for e in parked_thread.entities)
            active_set = set(e.lower() for e in self._active_thread.entities)
            if parked_set and active_set:
                overlap = len(parked_set & active_set) / len(parked_set | active_set)
                if overlap > 0:
                    await self._create_thread_edge(
                        parked_thread.id, self._active_thread.id,
                        "RELATES_TO", strength=overlap,
                    )

            # If resumed, also create CONTINUED_BY edge
            if resumed:
                await self._create_thread_edge(
                    parked_thread.id, self._active_thread.id,
                    "CONTINUED_BY", strength=1.0,
                )

        await self._maybe_auto_attach_active_thread_to_topology()
        self._notify_consciousness()

    async def _handle_amend(
        self,
        signal: FlowSignal,
        extraction: ExtractionOutput,
        filing_result: FilingResult,
    ) -> None:
        """Course correction within current flow. No thread change needed."""
        if not self._active_thread:
            self._active_thread = await self._create_thread(
                topic=signal.current_topic,
                entities=signal.topic_entities,
            )
            self._notify_consciousness()
            return

        self._active_thread.turn_count += 1
        logger.debug(
            f"The Dude: Amend in '{self._active_thread.topic}' "
            f"(correction_target: {signal.correction_target[:50]})"
        )

        # Persist turn count to Matrix so it survives restarts
        await self._update_thread_node(self._active_thread)

        self._notify_consciousness()

    # --- Task Resolution (Layer 4) ---

    async def _resolve_open_tasks(self, filing_result: FilingResult) -> None:
        """
        Resolve open tasks when OUTCOME nodes match ACTION nodes by entity overlap.

        Uses Jaccard similarity >= 0.5 between outcome entities and action entities.
        Creates RESOLVES edge: OUTCOME -> ACTION.
        """
        if not self._active_thread or not self._active_thread.open_tasks:
            return

        matrix = await self._get_matrix()
        if not matrix:
            return

        for outcome_id in filing_result.outcome_node_ids:
            # Get outcome node entities
            try:
                outcome_node = await matrix.get_node(outcome_id)
                if not outcome_node:
                    continue
                outcome_content = outcome_node.content or ""
                # Extract entity names from the outcome content
                outcome_entities: set[str] = set()
                try:
                    parsed = json.loads(outcome_content) if outcome_content.startswith("{") else {}
                    outcome_entities = set(e.lower() for e in parsed.get("entities", []))
                except Exception:
                    pass
                if not outcome_entities:
                    # Fallback: use entities from extraction objects that produced outcomes
                    for obj in filing_result._extraction_objects if hasattr(filing_result, '_extraction_objects') else []:
                        if hasattr(obj, 'entities'):
                            outcome_entities.update(e.lower() for e in obj.entities)
            except Exception as e:
                logger.debug(f"The Dude: Could not load outcome node {outcome_id}: {e}")
                continue

            resolved = []
            for action_id in self._active_thread.open_tasks:
                try:
                    action_node = await matrix.get_node(action_id)
                    if not action_node:
                        continue
                    action_content = action_node.content or ""
                    action_entities: set[str] = set()
                    try:
                        parsed = json.loads(action_content) if action_content.startswith("{") else {}
                        action_entities = set(e.lower() for e in parsed.get("entities", []))
                    except Exception:
                        pass

                    # Jaccard similarity
                    if action_entities and outcome_entities:
                        intersection = action_entities & outcome_entities
                        union = action_entities | outcome_entities
                        jaccard = len(intersection) / len(union) if union else 0
                    else:
                        # If no entities to compare, cannot resolve
                        jaccard = 0.0

                    if jaccard >= 0.5:
                        # Create RESOLVES edge
                        try:
                            await self._create_edge(
                                from_id=outcome_id,
                                to_id=action_id,
                                edge_type="RESOLVES",
                                confidence=jaccard,
                            )
                        except Exception as e:
                            logger.debug(f"The Dude: Could not create RESOLVES edge: {e}")
                        resolved.append(action_id)
                        logger.info(
                            f"The Dude: Resolved task {action_id} "
                            f"(Jaccard={jaccard:.2f})"
                        )
                except Exception as e:
                    logger.debug(f"The Dude: Could not check action {action_id}: {e}")

            # Remove resolved tasks
            for task_id in resolved:
                self._active_thread.open_tasks.remove(task_id)
                # Mirror the resolution into the canonical tasks table.
                await self._complete_canonical_task_for_action(
                    task_id, outcome_node_id=outcome_id,
                )

            # Update thread node in Matrix if tasks were resolved
            if resolved:
                try:
                    await matrix.update_node(
                        node_id=self._active_thread.id,
                        content=json.dumps(self._active_thread.to_dict()),
                    )
                except Exception as e:
                    logger.debug(f"The Dude: Could not update thread node: {e}")

    # --- Canonical Task Writer (Dual-Write Bridge to TaskManager) ---

    async def _create_canonical_task_for_action(self, action_id: str) -> None:
        """Mirror an ACTION memory_node into the canonical tasks table.

        Fault-isolated: never raises, logs at debug on failure. Dedup is
        by stable task_id (``thread_action_<action_id>``) — repeat calls
        are no-ops. Thread linkage relies on the `threads` table row that
        Librarian/backfill already populates.
        """
        if not self.engine or not getattr(self.engine, "task_manager", None):
            return
        if not self._active_thread:
            return
        task_id = f"thread_action_{action_id}"
        try:
            tm = self.engine.task_manager
            if await tm.get(task_id) is not None:
                return

            # Pull the ACTION node for a title/description since the append
            # loop receives only node IDs, not the extracted object.
            title = "(untitled action)"
            description: Optional[str] = None
            entities: list[str] = []
            try:
                matrix = await self._get_matrix()
                if matrix is not None:
                    action_node = await matrix.get_node(action_id)
                    if action_node and action_node.content:
                        raw = action_node.content
                        # ACTION content may be a JSON blob or a bare string.
                        parsed: Optional[dict] = None
                        if raw.lstrip().startswith("{"):
                            try:
                                candidate = json.loads(raw)
                                if isinstance(candidate, dict):
                                    parsed = candidate
                            except (json.JSONDecodeError, TypeError):
                                parsed = None
                        if parsed is not None:
                            description = parsed.get("content") or parsed.get("text") or raw
                            ents = parsed.get("entities") or []
                            if isinstance(ents, list):
                                entities = [e for e in ents if isinstance(e, str)]
                        else:
                            description = raw
                        first_line = (description or "").strip().split("\n", 1)[0]
                        title = first_line[:120] or "(untitled action)"
            except Exception as e:
                logger.warning(f"The Dude: could not enrich task from ACTION {action_id}: {e}")

            await tm.create(
                task_id=task_id,
                title=title,
                description=description,
                kind="workspace_followup",
                priority=5,
                status=TaskStatus.READY.value,
                owner="luna",
                source="librarian",
                thread_id=self._active_thread.id,
                metadata={
                    "action_node_id": action_id,
                    "thread_id": self._active_thread.id,
                },
                entities=entities or None,
            )
        except Exception as e:
            logger.debug(f"The Dude: canonical task mirror failed for {action_id}: {e}")

    async def _complete_canonical_task_for_action(
        self,
        action_id: str,
        *,
        outcome_node_id: Optional[str] = None,
    ) -> None:
        """Complete the mirrored task when its ACTION resolves. Best-effort."""
        if not self.engine or not getattr(self.engine, "task_manager", None):
            return
        task_id = f"thread_action_{action_id}"
        try:
            tm = self.engine.task_manager
            task = await tm.get(task_id)
            if task is None:
                return
            if task.status in (
                TaskStatus.COMPLETED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.ARCHIVED.value,
            ):
                return
            if task.status == TaskStatus.READY.value:
                await tm.start(task_id, actor="librarian")
            await tm.complete(
                task_id,
                result={"resolved_by_outcome": outcome_node_id},
            )
        except Exception as e:
            logger.debug(f"The Dude: canonical task complete failed for {action_id}: {e}")

    # --- Public Accessors (Layer 5) ---

    def get_active_thread(self):
        """Get the currently active thread (if any)."""
        return self._active_thread

    def get_parked_threads(self) -> list:
        """Get all parked threads from cache."""
        return [t for t in self._thread_cache.values() if t.status == ThreadStatus.PARKED]

    # --- Consciousness Wiring (Layer 6) ---

    def set_consciousness(self, consciousness) -> None:
        """Set consciousness reference for thread-aware updates (Layer 6)."""
        self._consciousness = consciousness

    def _notify_consciousness(self) -> None:
        """Notify consciousness of thread state change (Layer 6)."""
        if self._consciousness:
            self._consciousness.update_from_thread(
                active_thread=self._active_thread,
                parked_threads=self.get_parked_threads(),
            )

    # --- Topology Auto-Attach (observational; must never raise) ---

    async def _maybe_auto_attach_active_thread_to_topology(self) -> None:
        """Run the first automatic topology attachment rule on the active thread.

        Observational / augmenting logic. Never raises into the turn flow:
        any exception is swallowed and logged. Designed to be called on
        active-thread identity changes only — not on same-thread
        continuation or amendment.

        See Docs/bible/Handoffs/HANDOFF_ADD_RULE_DRIVEN_TOPOLOGY_ATTACHMENT.md.
        """
        try:
            engine = getattr(self, "engine", None)
            if engine is None:
                return
            if self._active_thread is None:
                return
            matrix = engine.get_actor("matrix") if hasattr(engine, "get_actor") else None
            db = getattr(matrix, "_db", None) if matrix else None
            if db is None:
                return

            from luna.topology import TopologyRuleAttachmentService

            svc = TopologyRuleAttachmentService(db)
            result = await svc.attach_thread_by_rules(self._active_thread)
            logger.info(
                f"[TOPOLOGY] auto-attach-active-thread rule={result.rule_name} "
                f"status={result.status.value} "
                f"project={result.project_slug or 'none'} "
                f"anchor={result.anchor_entity_id or 'none'} "
                f"thread={result.thread_id} "
                f"cluster={result.cluster_id or 'none'} "
                f"existing={result.existing_cluster_id or 'none'}"
            )
        except Exception as e:
            logger.warning(
                "[TOPOLOGY] auto-attach-active-thread failed soft: "
                f"{type(e).__name__}: {e}"
            )

    # --- Thread Operations ---

    async def _create_thread(
        self,
        topic: str,
        entities: list[str],
    ) -> Thread:
        """Create a new THREAD node in the Matrix."""
        thread = Thread(
            id="",
            topic=topic,
            status=ThreadStatus.ACTIVE,
            entities=list(entities),
            turn_count=1,
            project_slug=self._active_project_slug,
        )

        matrix = await self._get_matrix()
        if matrix:
            node_id = await matrix.add_node(
                node_type="THREAD",
                content=json.dumps(thread.to_dict()),
                source="librarian",
                confidence=1.0,
                metadata={"tags": self._thread_tags(thread)},
            )
            thread.id = node_id

            # Create INVOLVES edges to entities
            for entity_name in entities:
                try:
                    entity_id = await self._resolve_entity(entity_name, "ENTITY")
                    thread.entity_node_ids.append(entity_id)
                    await self._create_edge(
                        from_id=node_id,
                        to_id=entity_id,
                        edge_type="INVOLVES",
                        confidence=1.0,
                    )
                except Exception as e:
                    logger.warning(f"The Dude: Could not create entity edge: {e}")

            # Create IN_PROJECT edge if project context active
            if self._active_project_slug:
                try:
                    project_id = await self._resolve_entity(
                        self._active_project_slug, "PROJECT"
                    )
                    await self._create_edge(
                        from_id=node_id,
                        to_id=project_id,
                        edge_type="IN_PROJECT",
                        confidence=1.0,
                    )
                except Exception as e:
                    logger.debug(f"The Dude: Could not create project edge: {e}")
        else:
            import uuid
            thread.id = f"thread_{uuid.uuid4().hex[:8]}"

        self._thread_cache[thread.id] = thread
        self._threads_created += 1

        # Emit thread_updated for live knowledge feed
        from luna.core.event_bus import event_bus, KnowledgeEvent
        event_bus.emit("knowledge", KnowledgeEvent(
            type="thread_updated",
            payload={
                "thread_id": thread.id,
                "topic": thread.topic,
                "status": "active",
                "turn_count": thread.turn_count,
                "entities": thread.entities,
            },
        ))

        # Evict oldest cached threads if over limit
        if len(self._thread_cache) > self._max_cached_threads:
            oldest_key = next(iter(self._thread_cache))
            del self._thread_cache[oldest_key]

        # Study context section awareness (Phase 4c)
        if thread.project_slug and thread.entities:
            try:
                from luna.context.study_context import load_raw_config, get_matching_sections
                raw_config = load_raw_config(thread.project_slug)
                if raw_config:
                    sections = get_matching_sections(raw_config, thread.entities)
                    thread.study_context_sections = sections
                    if sections:
                        logger.info(
                            f"The Dude: Thread '{thread.topic}' matched "
                            f"{len(sections)} study context sections: {sections}"
                        )
            except Exception as e:
                logger.debug(f"The Dude: Study context matching failed: {e}")

        await self._sync_thread_to_canonical_threads(thread)
        return thread

    async def _park_thread(self, thread: Thread) -> None:
        """Snapshot and park a thread."""
        thread.status = ThreadStatus.PARKED
        thread.parked_at = datetime.now()

        await self._update_thread_node(thread)
        await self._sync_thread_to_canonical_threads(thread)
        self._thread_cache[thread.id] = thread
        self._threads_parked += 1

        # Emit thread_updated (parked) for live knowledge feed
        from luna.core.event_bus import event_bus, KnowledgeEvent
        event_bus.emit("knowledge", KnowledgeEvent(
            type="thread_updated",
            payload={
                "thread_id": thread.id,
                "topic": thread.topic,
                "status": "parked",
                "turn_count": thread.turn_count,
                "entities": thread.entities,
            },
        ))

        # Reinforce entity-to-entity edges (Level 3) for non-trivial threads
        if thread.turn_count >= 3:
            await self._reinforce_entity_edges(thread)

    async def _resume_thread(self, thread: Thread) -> None:
        """Resume a parked thread."""
        thread.status = ThreadStatus.ACTIVE
        thread.resumed_at = datetime.now()
        thread.resume_count += 1

        await self._update_thread_node(thread)
        await self._sync_thread_to_canonical_threads(thread)
        self._thread_cache[thread.id] = thread
        self._threads_resumed += 1

        # Emit thread_updated (resumed) for live knowledge feed
        from luna.core.event_bus import event_bus, KnowledgeEvent
        event_bus.emit("knowledge", KnowledgeEvent(
            type="thread_updated",
            payload={
                "thread_id": thread.id,
                "topic": thread.topic,
                "status": "resumed",
                "turn_count": thread.turn_count,
                "entities": thread.entities,
            },
        ))

    async def _find_resumable_thread(
        self,
        new_entities: list[str],
    ) -> tuple[Optional[Thread], float]:
        """
        Find the best parked thread by entity overlap with the new topic.

        Returns (thread, overlap) where thread is the best match and overlap is the
        Jaccard score. thread is None when overlap < 0.4 (the resume threshold) or
        when nothing parked matches. Overlap is still returned below threshold so
        callers can log the score for continuity decisions.

        Tie-break: on exactly equal overlap, prefer the thread with the more
        recent parked_at. A None parked_at loses to any concrete timestamp.
        """
        if not new_entities:
            return None, 0.0

        new_set = set(e.lower() for e in new_entities)
        best_match: Optional[Thread] = None
        best_overlap: float = 0.0

        def _prefer_candidate(candidate: Thread, current: Optional[Thread]) -> bool:
            # Exact-tie refinement only. Called when overlap == best_overlap.
            if current is None:
                return True
            cand_ts = candidate.parked_at
            curr_ts = current.parked_at
            if cand_ts is None:
                return False
            if curr_ts is None:
                return True
            return cand_ts > curr_ts

        # Check cache (fast path)
        for thread in self._thread_cache.values():
            if thread.status != ThreadStatus.PARKED:
                continue

            thread_set = set(e.lower() for e in thread.entities)
            if not thread_set:
                continue

            overlap = len(new_set & thread_set) / len(new_set | thread_set)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = thread
            elif overlap == best_overlap and overlap > 0.0 and _prefer_candidate(thread, best_match):
                best_match = thread

        # If no cache hit, search Matrix for THREAD nodes
        if best_overlap < 0.4:
            matrix = await self._get_matrix()
            if matrix:
                for entity in new_entities[:3]:
                    try:
                        nodes = await matrix.search_nodes(
                            entity,
                            node_type="THREAD",
                            limit=5,
                        )
                        for node in nodes:
                            try:
                                thread_data = json.loads(node.content)
                                if thread_data.get("status") != "parked":
                                    continue

                                thread = Thread.from_dict(thread_data)
                                thread.id = node.id

                                thread_set = set(e.lower() for e in thread.entities)
                                overlap = len(new_set & thread_set) / len(new_set | thread_set) if thread_set else 0

                                if overlap > best_overlap:
                                    best_overlap = overlap
                                    best_match = thread
                                    self._thread_cache[thread.id] = thread
                                elif overlap == best_overlap and overlap > 0.0 and _prefer_candidate(thread, best_match):
                                    best_match = thread
                                    self._thread_cache[thread.id] = thread
                            except (json.JSONDecodeError, KeyError):
                                continue
                    except Exception as e:
                        logger.debug(f"The Dude: Thread search failed for '{entity}': {e}")

        if best_overlap >= 0.4 and best_match:
            logger.info(
                f"The Dude: Found resumable thread '{best_match.topic}' "
                f"(overlap={best_overlap:.2f})"
            )
            return best_match, best_overlap

        return None, best_overlap

    async def _update_thread_node(self, thread: Thread) -> None:
        """Update a THREAD node's content in the Matrix."""
        matrix = await self._get_matrix()
        if not matrix:
            return

        try:
            await matrix.update_node(
                node_id=thread.id,
                content=json.dumps(thread.to_dict()),
                metadata={"tags": self._thread_tags(thread)},
            )
        except Exception as e:
            logger.error(f"The Dude: Failed to update thread node {thread.id}: {e}")

    # --- Canonical Threads Live Sync (observational; must never raise) ---

    async def _sync_thread_to_canonical_threads(self, thread: Thread) -> None:
        """Mirror a live Thread into the canonical ``threads`` table.

        Observational / non-fatal. Never raises into turn flow — any
        failure becomes a ``logger.warning`` and returns cleanly. Safe
        to call repeatedly for the same ``thread.id`` (upserts on id).

        Called from ``_create_thread``, ``_park_thread``, and
        ``_resume_thread``. Boot-time ``backfill_threads(...)`` uses
        ``INSERT OR IGNORE``, so it will skip rows this helper has
        already written — the two paths compose without overwrites.

        See Docs/bible/Handoffs/HANDOFF_SYNC_LIVE_THREADS_TO_CANONICAL_THREADS.md.
        """
        try:
            if not getattr(thread, "id", None):
                return
            engine = getattr(self, "engine", None)
            if engine is None:
                return
            matrix = engine.get_actor("matrix") if hasattr(engine, "get_actor") else None
            db = getattr(matrix, "_db", None) if matrix else None
            if db is None:
                return

            status = (
                thread.status.value
                if hasattr(thread.status, "value")
                else str(thread.status)
            )

            def _iso(dt):
                if dt is None:
                    return None
                return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

            # Mirror backfill's metadata contract — no open_tasks.
            metadata: dict = {}
            for key in (
                "entities",
                "entity_node_ids",
                "parent_thread_id",
                "study_context_sections",
                "turn_count",
            ):
                val = getattr(thread, key, None)
                if val not in (None, [], ""):
                    metadata[key] = val

            await db.execute(
                """
                INSERT INTO threads (
                    id, topic, status, project_slug,
                    started_at, parked_at, resumed_at, closed_at,
                    resume_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    topic = excluded.topic,
                    status = excluded.status,
                    project_slug = excluded.project_slug,
                    started_at = excluded.started_at,
                    parked_at = excluded.parked_at,
                    resumed_at = excluded.resumed_at,
                    closed_at = excluded.closed_at,
                    resume_count = excluded.resume_count,
                    metadata_json = excluded.metadata_json
                """,
                (
                    thread.id,
                    thread.topic or "",
                    status,
                    getattr(thread, "project_slug", None),
                    _iso(getattr(thread, "started_at", None)),
                    _iso(getattr(thread, "parked_at", None)),
                    _iso(getattr(thread, "resumed_at", None)),
                    _iso(getattr(thread, "closed_at", None)),
                    int(getattr(thread, "resume_count", 0) or 0),
                    json.dumps(metadata),
                ),
            )
        except Exception as e:
            logger.warning(
                "[CANONICAL] thread sync failed soft: "
                f"{type(e).__name__}: {e}"
            )

    def _thread_tags(self, thread: Thread) -> list[str]:
        """Generate searchable tags for a thread node."""
        tags = ["thread", f"status:{thread.status.value}"]
        if thread.project_slug:
            tags.append(f"project:{thread.project_slug}")
        if thread.open_tasks:
            tags.append("has_open_tasks")
        return tags

    async def _create_thread_edge(
        self,
        from_id: str,
        to_id: str,
        relationship: str,
        strength: float = 1.0,
    ) -> None:
        """Create edge between thread nodes, bypassing has_edge duplicate check."""
        if not self.engine:
            return

        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor or not hasattr(matrix_actor, "_graph") or not matrix_actor._graph:
            return

        try:
            await matrix_actor._graph.add_edge(
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                strength=strength,
            )
            logger.info(
                f"THREAD_EDGE: {from_id} --[{relationship} @ {strength:.2f}]--> {to_id}"
            )
        except Exception as e:
            logger.error(f"The Dude: Thread edge creation failed: {e}")

    async def _reinforce_entity_edges(self, thread: Thread) -> None:
        """
        Strengthen edges between entities that co-occur in this thread.

        Level 3 edge strategy: entities that appear together across threads
        get CO_OCCURS edges with growing strength.
        """
        if len(thread.entity_node_ids) < 2:
            return

        if not self.engine:
            return

        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor or not hasattr(matrix_actor, "_graph") or not matrix_actor._graph:
            return

        graph = matrix_actor._graph

        for i, from_id in enumerate(thread.entity_node_ids):
            for to_id in thread.entity_node_ids[i + 1:]:
                try:
                    current_edges = await graph.get_edges(from_id)
                    existing = [
                        e for e in current_edges
                        if e.to_id == to_id and e.relationship == "CO_OCCURS"
                    ]

                    if existing:
                        new_strength = min(1.0, existing[0].strength + 0.1)
                    else:
                        new_strength = 0.3

                    await graph.add_edge(
                        from_id=from_id,
                        to_id=to_id,
                        relationship="CO_OCCURS",
                        strength=new_strength,
                    )
                except Exception as e:
                    logger.debug(f"The Dude: CO_OCCURS edge failed: {e}")

    # --- Project Context ---

    async def _handle_set_project_context(self, msg: Message) -> None:
        """Set active Kozmo project context for thread tagging."""
        payload = msg.payload or {}
        slug = payload.get("slug", "")

        if slug:
            self._active_project_slug = slug
            logger.info(f"The Dude: Project context set to '{slug}'")

            # Check for parked threads in this project
            project_threads = [
                t for t in self._thread_cache.values()
                if t.project_slug == slug and t.status == ThreadStatus.PARKED
            ]

            if project_threads:
                topics = [t.topic for t in project_threads[:3]]
                logger.info(
                    f"The Dude: Found {len(project_threads)} parked threads "
                    f"for project '{slug}': {topics}"
                )

                await self.send_to_engine("project_threads_available", {
                    "project_slug": slug,
                    "threads": [t.to_dict() for t in project_threads[:5]],
                })

    async def _handle_clear_project_context(self, msg: Message) -> None:
        """Clear active Kozmo project context."""
        if self._active_thread and self._active_project_slug:
            await self._park_thread(self._active_thread)
            logger.info(
                f"The Dude: Auto-parked thread '{self._active_thread.topic}' "
                f"on project deactivate"
            )
            self._active_thread = None

        self._active_project_slug = None
        logger.info("The Dude: Project context cleared")

    async def _handle_get_active_thread(self, msg: Message) -> None:
        """Return the active thread info."""
        result = self._active_thread.to_dict() if self._active_thread else None
        await self.send_to_engine("active_thread", {"thread": result})

    async def _handle_get_parked_threads(self, msg: Message) -> None:
        """Return all parked threads."""
        parked = [
            t.to_dict() for t in self._thread_cache.values()
            if t.status == ThreadStatus.PARKED
        ]
        await self.send_to_engine("parked_threads", {"threads": parked})

    # HELPERS
    # =========================================================================

    async def _get_matrix(self) -> Optional["MemoryMatrix"]:
        """Get MemoryMatrix from MatrixActor."""
        if not self.engine:
            return None

        matrix_actor = self.engine.get_actor("matrix")
        if not matrix_actor:
            return None

        # MatrixActor stores MemoryMatrix in _matrix (or _memory for backwards compat)
        if hasattr(matrix_actor, "_matrix") and matrix_actor._matrix:
            return matrix_actor._matrix
        if hasattr(matrix_actor, "_memory"):
            return matrix_actor._memory

        return None

    async def _is_early_relationship(self) -> bool:
        """True if < EARLY_RELATIONSHIP_TURN_THRESHOLD conversation turns."""
        if not self.engine:
            return False
        try:
            matrix_actor = self.engine.actors.get("matrix")
            if matrix_actor and matrix_actor._matrix:
                count = await matrix_actor._matrix.db.fetchone(
                    "SELECT COUNT(*) FROM conversation_turns"
                )
                return (count[0] if count else 0) < EARLY_RELATIONSHIP_TURN_THRESHOLD
        except Exception:
            return False

    # =========================================================================
    # STATS & LIFECYCLE
    # =========================================================================

    def get_stats(self) -> dict:
        """Get filing statistics."""
        avg_time = (
            self._total_filing_time_ms / self._filings_count
            if self._filings_count > 0
            else 0
        )
        return {
            "filings_count": self._filings_count,
            "nodes_created": self._nodes_created,
            "nodes_merged": self._nodes_merged,
            "edges_created": self._edges_created,
            "edges_failed": self._edges_failed,
            "edges_skipped_duplicate": self._edges_skipped_duplicate,
            "entity_resolve_failures": self._entity_resolve_failures,
            "context_retrievals": self._context_retrievals,
            "entity_updates_filed": self._entity_updates_filed,
            "entity_versions_created": self._entity_versions_created,
            "avg_filing_time_ms": avg_time,
            "cache_size": len(self.alias_cache),
            "inference_queue_size": len(self.inference_queue),
            "threads_created": self._threads_created,
            "threads_parked": self._threads_parked,
            "threads_resumed": self._threads_resumed,
            "threads_closed": self._threads_closed,
            "active_thread": self._active_thread.topic if self._active_thread else None,
            "cached_threads": len(self._thread_cache),
        }

    async def snapshot(self) -> dict:
        """Return state for serialization."""
        base = await super().snapshot()
        base.update({
            "stats": self.get_stats(),
            "cache_size": len(self.alias_cache),
        })
        return base

    def _load_alias_cache(self) -> None:
        """Load alias cache from disk."""
        import json
        import os
        from luna.core.paths import user_dir
        cache_path = str(user_dir() / "alias_cache.json")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r") as f:
                    self.alias_cache = json.load(f)
                logger.info(f"The Dude: Loaded {len(self.alias_cache)} alias cache entries")
        except Exception as e:
            logger.warning(f"The Dude: Failed to load alias cache: {e}")

    def _save_alias_cache(self) -> None:
        """Save alias cache to disk."""
        import json
        import os
        from luna.core.paths import user_dir
        cache_path = str(user_dir() / "alias_cache.json")
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(self.alias_cache, f)
            logger.debug(f"The Dude: Saved {len(self.alias_cache)} alias cache entries")
        except Exception as e:
            logger.warning(f"The Dude: Failed to save alias cache: {e}")

    async def on_start(self) -> None:
        """Initialize on start."""
        self._load_alias_cache()
        logger.info("The Dude: Yeah, well, you know, that's just like, my opinion, man.")

    async def on_stop(self) -> None:
        """Cleanup on stop."""
        # Park active thread on shutdown
        if self._active_thread:
            try:
                await self._park_thread(self._active_thread)
                logger.info(
                    f"The Dude: Parked active thread '{self._active_thread.topic}' "
                    f"on shutdown"
                )
                self._active_thread = None
            except Exception as e:
                logger.error(f"The Dude: Failed to park thread on shutdown: {e}")

        self._save_alias_cache()
        # Process any remaining inference queue
        if self.inference_queue:
            logger.info(f"The Dude: {len(self.inference_queue)} items in inference queue at shutdown")
