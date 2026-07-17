"""
Personality Patch Storage for Luna Engine
==========================================

Manages the storage and retrieval of PersonalityPatch nodes,
which represent Luna's emergent personality evolution.

Patches are stored as memory_nodes with node_type='PERSONALITY_REFLECTION',
allowing them to participate in vector search alongside other memories.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import PersonalityPatch, PatchTopic, PatchTrigger
from ..substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


class PersonalityPatchManager:
    """
    Manages PersonalityPatch storage as memory nodes.

    Provides CRUD operations, search, reinforcement, and lifecycle management
    for personality patches that drive Luna's emergent identity.
    """

    # The node_type used for personality patches in memory_nodes
    NODE_TYPE = "PERSONALITY_REFLECTION"

    # Default lock_in for new patches
    DEFAULT_LOCK_IN = 0.7

    # Lock_in thresholds
    LOCK_IN_DECAY_THRESHOLD = 0.3      # Deactivate if below this
    LOCK_IN_ESTABLISHED = 0.7          # Considered established above this

    def __init__(self, db: MemoryDatabase, config_path: str = "config/personality.json"):
        """
        Initialize the PersonalityPatchManager.

        Args:
            db: The underlying MemoryDatabase instance
            config_path: Path to personality config JSON (default: config/personality.json)
        """
        self.db = db
        self._load_config(config_path)
        logger.info("PersonalityPatchManager initialized")

    def _load_config(self, config_path: str):
        """Load lock-in settings from personality config, falling back to class defaults."""
        config_file = Path(config_path)
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                settings = config.get("personality_patch_storage", {}).get("settings", {})
                self.DEFAULT_LOCK_IN = settings.get("initial_lock_in", self.__class__.DEFAULT_LOCK_IN)
                self.LOCK_IN_DECAY_THRESHOLD = settings.get("lock_in_deactivation_threshold", self.__class__.LOCK_IN_DECAY_THRESHOLD)
                logger.debug(f"Loaded patch storage config: lock_in={self.DEFAULT_LOCK_IN}, decay_threshold={self.LOCK_IN_DECAY_THRESHOLD}")
                return
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load config from {config_path}: {e}")
        self.DEFAULT_LOCK_IN = self.__class__.DEFAULT_LOCK_IN
        self.LOCK_IN_DECAY_THRESHOLD = self.__class__.LOCK_IN_DECAY_THRESHOLD

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def add_patch(self, patch: PersonalityPatch) -> str:
        """
        Add a new personality patch to storage.

        Stores the patch as a memory node with node_type='PERSONALITY_REFLECTION'.

        Args:
            patch: The PersonalityPatch to store

        Returns:
            The patch_id (also used as node_id)
        """
        # Generate ID if not provided
        if not patch.patch_id:
            patch.patch_id = f"patch_{uuid.uuid4().hex[:8]}"

        # Prepare the memory node data
        content = patch.to_memory_node_content()
        metadata = patch.to_memory_node_metadata()

        # Insert as memory node
        await self.db.execute(
            """
            INSERT INTO memory_nodes (
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patch.patch_id,
                self.NODE_TYPE,
                content,
                patch.subtopic,  # Use subtopic as summary
                patch.confidence,
                0.7,  # Default importance
                0,
                patch.reinforcement_count,
                patch.lock_in,
                "settled" if patch.lock_in >= self.LOCK_IN_ESTABLISHED else "fluid",
                json.dumps(metadata),
                patch.created_at.isoformat() if patch.created_at else datetime.now().isoformat(),
                patch.last_reinforced.isoformat() if patch.last_reinforced else datetime.now().isoformat(),
            )
        )

        # Create edges to evidence nodes if provided
        for evidence_id in patch.evidence_nodes:
            await self._create_evidence_edge(patch.patch_id, evidence_id)

        logger.info(f"Added personality patch: {patch.patch_id} ({patch.subtopic})")
        return patch.patch_id

    async def get_patch(self, patch_id: str) -> Optional[PersonalityPatch]:
        """
        Retrieve a personality patch by ID.

        Args:
            patch_id: The patch/node ID

        Returns:
            PersonalityPatch if found, None otherwise
        """
        row = await self.db.fetchone(
            """
            SELECT
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            FROM memory_nodes
            WHERE id = ? AND node_type = ? AND namespace = 'active'
            """,
            (patch_id, self.NODE_TYPE)
        )

        if not row:
            return None

        node = self._row_to_node_dict(row)
        return PersonalityPatch.from_memory_node(node)

    async def update_patch(self, patch: PersonalityPatch) -> bool:
        """
        Update an existing personality patch.

        Args:
            patch: The PersonalityPatch with updated values

        Returns:
            True if updated, False if not found
        """
        content = patch.to_memory_node_content()
        metadata = patch.to_memory_node_metadata()

        result = await self.db.execute(
            """
            UPDATE memory_nodes SET
                content = ?,
                summary = ?,
                confidence = ?,
                reinforcement_count = ?,
                lock_in = ?,
                lock_in_state = ?,
                metadata = ?,
                updated_at = ?
            WHERE id = ? AND node_type = ?
            """,
            (
                content,
                patch.subtopic,
                patch.confidence,
                patch.reinforcement_count,
                patch.lock_in,
                "settled" if patch.lock_in >= self.LOCK_IN_ESTABLISHED else "fluid",
                json.dumps(metadata),
                datetime.now().isoformat(),
                patch.patch_id,
                self.NODE_TYPE,
            )
        )

        if result.rowcount > 0:
            logger.debug(f"Updated patch: {patch.patch_id}")
            return True
        return False

    async def delete_patch(self, patch_id: str) -> bool:
        """
        Delete a personality patch.

        Args:
            patch_id: The patch/node ID to delete

        Returns:
            True if deleted, False if not found
        """
        # Delete edges first
        await self.db.execute(
            "DELETE FROM memory_edges WHERE from_node = ? OR to_node = ?",
            (patch_id, patch_id)
        )

        result = await self.db.execute(
            "DELETE FROM memory_nodes WHERE id = ? AND node_type = ?",
            (patch_id, self.NODE_TYPE)
        )

        if result.rowcount > 0:
            logger.info(f"Deleted patch: {patch_id}")
            return True
        return False

    # =========================================================================
    # SEARCH OPERATIONS
    # =========================================================================

    async def search_patches(
        self,
        query: str,
        limit: int = 10,
        min_lock_in: float = 0.0,
        active_only: bool = True
    ) -> list[PersonalityPatch]:
        """
        Search for relevant personality patches.

        Uses semantic search on the content field.

        Args:
            query: Search query
            limit: Maximum results
            min_lock_in: Minimum lock_in threshold
            active_only: Only return active patches

        Returns:
            List of matching PersonalityPatch objects
        """
        # For now, use simple LIKE search
        # TODO: Integrate with vector search when embeddings are available
        rows = await self.db.fetchall(
            """
            SELECT
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            FROM memory_nodes
            WHERE node_type = ?
              AND lock_in >= ?
              AND (content LIKE ? OR summary LIKE ?)
              AND namespace = 'active'
            ORDER BY lock_in DESC, updated_at DESC
            LIMIT ?
            """,
            (
                self.NODE_TYPE,
                min_lock_in,
                f"%{query}%",
                f"%{query}%",
                limit,
            )
        )

        patches = []
        for row in rows:
            node = self._row_to_node_dict(row)
            patch = PersonalityPatch.from_memory_node(node)
            if active_only and not patch.active:
                continue
            patches.append(patch)

        return patches

    async def get_patches_by_topic(
        self,
        topic: PatchTopic,
        limit: int = 10,
        active_only: bool = True
    ) -> list[PersonalityPatch]:
        """
        Get patches filtered by topic.

        Args:
            topic: The PatchTopic to filter by
            limit: Maximum results
            active_only: Only return active patches

        Returns:
            List of matching PersonalityPatch objects
        """
        topic_value = topic.value if isinstance(topic, PatchTopic) else topic

        rows = await self.db.fetchall(
            """
            SELECT
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            FROM memory_nodes
            WHERE node_type = ?
              AND json_extract(metadata, '$.topic') = ?
              AND namespace = 'active'
            ORDER BY lock_in DESC, updated_at DESC
            LIMIT ?
            """,
            (self.NODE_TYPE, topic_value, limit)
        )

        patches = []
        for row in rows:
            node = self._row_to_node_dict(row)
            patch = PersonalityPatch.from_memory_node(node)
            if active_only and not patch.active:
                continue
            patches.append(patch)

        return patches

    async def get_all_active_patches(
        self,
        limit: int = 50,
        min_lock_in: float = 0.0
    ) -> list[PersonalityPatch]:
        """
        Get all active personality patches.

        Args:
            limit: Maximum results
            min_lock_in: Minimum lock_in threshold

        Returns:
            List of active PersonalityPatch objects sorted by lock_in
        """
        rows = await self.db.fetchall(
            """
            SELECT
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            FROM memory_nodes
            WHERE node_type = ?
              AND lock_in >= ?
              AND namespace = 'active'
            ORDER BY lock_in DESC, updated_at DESC
            LIMIT ?
            """,
            (self.NODE_TYPE, min_lock_in, limit)
        )

        patches = []
        for row in rows:
            node = self._row_to_node_dict(row)
            patch = PersonalityPatch.from_memory_node(node)
            if patch.active:
                patches.append(patch)

        return patches

    # =========================================================================
    # REINFORCEMENT & LIFECYCLE
    # =========================================================================

    async def reinforce_patch(self, patch_id: str, amount: float = 0.05) -> bool:
        """
        Reinforce a patch when behavior confirms it.

        Increases lock_in and reinforcement_count.

        Args:
            patch_id: The patch to reinforce
            amount: Amount to increase lock_in (capped at 1.0)

        Returns:
            True if reinforced, False if not found
        """
        result = await self.db.execute(
            """
            UPDATE memory_nodes SET
                reinforcement_count = reinforcement_count + 1,
                lock_in = MIN(1.0, lock_in + ?),
                lock_in_state = CASE
                    WHEN lock_in + ? >= 0.7 THEN 'settled'
                    WHEN lock_in + ? >= 0.4 THEN 'fluid'
                    ELSE 'drifting'
                END,
                updated_at = ?
            WHERE id = ? AND node_type = ?
            """,
            (
                amount,
                amount,
                amount,
                datetime.now().isoformat(),
                patch_id,
                self.NODE_TYPE,
            )
        )

        if result.rowcount > 0:
            logger.debug(f"Reinforced patch: {patch_id}")
            return True
        return False

    async def decay_unused_patches(
        self,
        days_threshold: int = 30,
        decay_amount: float = 0.1
    ) -> int:
        """
        Decay patches that haven't been reinforced recently.

        Args:
            days_threshold: Days since last update to trigger decay
            decay_amount: Amount to reduce lock_in

        Returns:
            Number of patches decayed
        """
        threshold_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()

        # First decay the lock_in
        result = await self.db.execute(
            """
            UPDATE memory_nodes SET
                lock_in = MAX(0.0, lock_in - ?),
                lock_in_state = CASE
                    WHEN lock_in - ? < 0.3 THEN 'drifting'
                    WHEN lock_in - ? < 0.7 THEN 'fluid'
                    ELSE lock_in_state
                END,
                updated_at = ?
            WHERE node_type = ?
              AND updated_at < ?
              AND lock_in > 0.0
            """,
            (
                decay_amount,
                decay_amount,
                decay_amount,
                datetime.now().isoformat(),
                self.NODE_TYPE,
                threshold_date,
            )
        )

        decayed_count = result.rowcount
        if decayed_count > 0:
            logger.info(f"Decayed {decayed_count} unused patches")

        # Deactivate patches that fell below threshold
        await self._deactivate_low_lockin_patches()

        return decayed_count

    async def _deactivate_low_lockin_patches(self) -> int:
        """
        Deactivate patches with lock_in below threshold.

        Sets active=false in metadata for patches with lock_in < 0.3.

        Returns:
            Number of patches deactivated
        """
        # Get patches below threshold
        rows = await self.db.fetchall(
            """
            SELECT id, metadata FROM memory_nodes
            WHERE node_type = ?
              AND lock_in < ?
              AND namespace = 'active'
            """,
            (self.NODE_TYPE, self.LOCK_IN_DECAY_THRESHOLD)
        )

        deactivated = 0
        for row in rows:
            node_id, metadata_str = row
            try:
                metadata = json.loads(metadata_str) if metadata_str else {}
                if metadata.get("active", True):
                    metadata["active"] = False
                    await self.db.execute(
                        "UPDATE memory_nodes SET metadata = ? WHERE id = ?",
                        (json.dumps(metadata), node_id)
                    )
                    deactivated += 1
            except (json.JSONDecodeError, TypeError):
                continue

        if deactivated > 0:
            logger.info(f"Deactivated {deactivated} low lock_in patches")

        return deactivated

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_stats(self) -> dict:
        """
        Get statistics about personality patches.

        Returns:
            Dictionary with patch statistics
        """
        # Count all patches
        total = await self.db.fetchone(
            "SELECT COUNT(*) FROM memory_nodes WHERE node_type = ? AND namespace = 'active'",
            (self.NODE_TYPE,)
        )
        total_count = total[0] if total else 0

        # Count active patches
        active_rows = await self.db.fetchall(
            """
            SELECT metadata FROM memory_nodes
            WHERE node_type = ? AND namespace = 'active'
            """,
            (self.NODE_TYPE,)
        )

        active_count = 0
        for row in active_rows:
            try:
                meta = json.loads(row[0]) if row[0] else {}
                if meta.get("active", True):
                    active_count += 1
            except (json.JSONDecodeError, TypeError):
                active_count += 1  # Assume active if can't parse

        # Average lock_in
        avg_result = await self.db.fetchone(
            "SELECT AVG(lock_in) FROM memory_nodes WHERE node_type = ? AND namespace = 'active'",
            (self.NODE_TYPE,)
        )
        avg_lock_in = avg_result[0] if avg_result and avg_result[0] else 0.0

        # Count by topic
        topic_counts = {}
        for topic in PatchTopic:
            count_result = await self.db.fetchone(
                """
                SELECT COUNT(*) FROM memory_nodes
                WHERE node_type = ?
                  AND json_extract(metadata, '$.topic') = ?
                """,
                (self.NODE_TYPE, topic.value)
            )
            topic_counts[topic.value] = count_result[0] if count_result else 0

        return {
            "total_patches": total_count,
            "active_patches": active_count,
            "average_lock_in": round(avg_lock_in, 3),
            "patches_by_topic": topic_counts,
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _create_evidence_edge(self, patch_id: str, evidence_id: str) -> None:
        """Create an edge from patch to evidence node."""
        try:
            await self.db.execute(
                """
                INSERT INTO memory_edges (from_node, to_node, edge_type)
                VALUES (?, ?, 'supports')
                """,
                (patch_id, evidence_id)
            )
        except Exception as e:
            logger.debug(f"Could not create evidence edge: {e}")

    def _row_to_node_dict(self, row: tuple) -> dict:
        """Convert a database row to a node dictionary."""
        columns = [
            "id", "node_type", "content", "summary", "confidence",
            "importance", "access_count", "reinforcement_count",
            "lock_in", "lock_in_state", "metadata", "created_at", "updated_at"
        ]
        return dict(zip(columns, row))


__all__ = ["PersonalityPatchManager"]
