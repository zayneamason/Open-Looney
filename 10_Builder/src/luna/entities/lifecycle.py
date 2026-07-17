"""
Personality Patch Lifecycle Manager for Luna Engine
====================================================

Manages the lifecycle of PersonalityPatch nodes:
- Decay: Reduce lock_in for patches not reinforced recently
- Consolidation: Merge similar patches with same topic+subtopic
- Cleanup: Remove patches that have fallen below activity threshold

This module runs periodic maintenance to keep Luna's personality
system healthy and prevent unbounded patch accumulation.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import PersonalityPatch, PatchTopic
from .storage import PersonalityPatchManager

logger = logging.getLogger(__name__)


class LifecycleManager:
    """
    Manages personality patch lifecycle operations.

    Responsibilities:
    - Decay unused patches (delegates to PatchManager)
    - Consolidate similar patches (merge duplicates)
    - Cleanup inactive patches (remove low lock_in)
    - Track maintenance statistics
    """

    # Default configuration values
    DEFAULT_CONFIG = {
        "lifecycle": {
            "decay_enabled": True,
            "consolidation_enabled": True,
            "maintenance_interval_hours": 24,
        },
        "personality_patch_storage": {
            "settings": {
                "decay_days_threshold": 30,
                "decay_amount": 0.1,
                "lock_in_deactivation_threshold": 0.3,
                "consolidation_threshold": 50,
            }
        }
    }

    def __init__(
        self,
        patch_manager: PersonalityPatchManager,
        config_path: str = "config/personality.json"
    ):
        """
        Initialize the LifecycleManager.

        Args:
            patch_manager: The PersonalityPatchManager for patch operations
            config_path: Path to the personality configuration file
        """
        self.patch_manager = patch_manager
        self.config_path = config_path
        self.config = self._load_config()

        # Track maintenance statistics
        self._stats = {
            "last_maintenance_run": None,
            "total_decay_operations": 0,
            "total_patches_decayed": 0,
            "total_consolidation_operations": 0,
            "total_patches_consolidated": 0,
            "total_cleanup_operations": 0,
            "total_patches_cleaned": 0,
        }

        logger.info("LifecycleManager initialized")

    def _load_config(self) -> dict:
        """
        Load configuration from the personality.json file.

        Returns:
            Configuration dictionary with lifecycle settings
        """
        config_file = Path(self.config_path)

        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                    logger.debug(f"Loaded config from {self.config_path}")
                    return config
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")

        logger.info("Using default lifecycle configuration")
        return self.DEFAULT_CONFIG

    @property
    def decay_enabled(self) -> bool:
        """Check if decay is enabled in config."""
        return self.config.get("lifecycle", {}).get("decay_enabled", True)

    @property
    def consolidation_enabled(self) -> bool:
        """Check if consolidation is enabled in config."""
        return self.config.get("lifecycle", {}).get("consolidation_enabled", True)

    @property
    def maintenance_interval_hours(self) -> int:
        """Get the maintenance interval in hours."""
        return self.config.get("lifecycle", {}).get("maintenance_interval_hours", 24)

    @property
    def decay_days_threshold(self) -> int:
        """Get the days threshold for decay."""
        settings = self.config.get("personality_patch_storage", {}).get("settings", {})
        return settings.get("decay_days_threshold", 30)

    @property
    def decay_amount(self) -> float:
        """Get the decay amount per cycle."""
        settings = self.config.get("personality_patch_storage", {}).get("settings", {})
        return settings.get("decay_amount", 0.1)

    @property
    def lock_in_deactivation_threshold(self) -> float:
        """Get the lock_in threshold for cleanup."""
        settings = self.config.get("personality_patch_storage", {}).get("settings", {})
        return settings.get("lock_in_deactivation_threshold", 0.3)

    @property
    def consolidation_threshold(self) -> int:
        """Get the minimum active patch count before consolidation runs."""
        settings = self.config.get("personality_patch_storage", {}).get("settings", {})
        return settings.get("consolidation_threshold", 50)

    async def run_maintenance(self) -> dict:
        """
        Run all maintenance tasks.

        Executes decay, consolidation, and cleanup based on configuration.

        Returns:
            Dictionary with results from each maintenance operation
        """
        logger.info("Starting personality patch maintenance cycle")
        results = {
            "timestamp": datetime.now().isoformat(),
            "decay": {"enabled": self.decay_enabled, "patches_decayed": 0},
            "consolidation": {"enabled": self.consolidation_enabled, "patches_consolidated": 0},
            "cleanup": {"patches_cleaned": 0},
        }

        # 1. Decay unused patches
        if self.decay_enabled:
            try:
                decayed_count = await self.patch_manager.decay_unused_patches(
                    days_threshold=self.decay_days_threshold,
                    decay_amount=self.decay_amount
                )
                results["decay"]["patches_decayed"] = decayed_count
                self._stats["total_decay_operations"] += 1
                self._stats["total_patches_decayed"] += decayed_count
                logger.info(f"Decay: {decayed_count} patches decayed")
            except Exception as e:
                logger.error(f"Decay operation failed: {e}")
                results["decay"]["error"] = str(e)

        # 2. Consolidate similar patches
        if self.consolidation_enabled:
            try:
                consolidated_count = await self.consolidate_similar_patches()
                results["consolidation"]["patches_consolidated"] = consolidated_count
                self._stats["total_consolidation_operations"] += 1
                self._stats["total_patches_consolidated"] += consolidated_count
                logger.info(f"Consolidation: {consolidated_count} patches consolidated")
            except Exception as e:
                logger.error(f"Consolidation operation failed: {e}")
                results["consolidation"]["error"] = str(e)

        # 3. Cleanup inactive patches
        try:
            cleaned_count = await self.cleanup_inactive_patches()
            results["cleanup"]["patches_cleaned"] = cleaned_count
            self._stats["total_cleanup_operations"] += 1
            self._stats["total_patches_cleaned"] += cleaned_count
            logger.info(f"Cleanup: {cleaned_count} patches cleaned")
        except Exception as e:
            logger.error(f"Cleanup operation failed: {e}")
            results["cleanup"]["error"] = str(e)

        self._stats["last_maintenance_run"] = results["timestamp"]
        logger.info("Personality patch maintenance cycle completed")

        return results

    async def consolidate_similar_patches(self) -> int:
        """
        Find and merge patches with the same topic+subtopic.

        Only runs when active patch count exceeds consolidation_threshold.

        For each group of patches sharing topic+subtopic:
        - Keep the patch with the highest lock_in
        - Combine reinforcement_counts from all merged patches
        - Mark other patches as superseded by the winner
        - Deactivate the superseded patches

        Returns:
            Number of patches that were consolidated (superseded)
        """
        # Skip consolidation if below threshold
        try:
            stats = await self.patch_manager.get_stats()
            total = stats.get("total_patches", 0)
            if total < self.consolidation_threshold:
                logger.debug(
                    "Skipping consolidation: %d patches below threshold %d",
                    total, self.consolidation_threshold
                )
                return 0
        except Exception as e:
            logger.warning("Failed to check patch count for consolidation: %s", e)

        logger.debug("Starting patch consolidation")
        consolidated_count = 0

        # Group patches by topic+subtopic
        topic_groups: dict[str, list[PersonalityPatch]] = {}

        for topic in PatchTopic:
            patches = await self.patch_manager.get_patches_by_topic(
                topic=topic,
                limit=100,
                active_only=True
            )

            for patch in patches:
                key = f"{patch.topic.value}:{patch.subtopic}"
                if key not in topic_groups:
                    topic_groups[key] = []
                topic_groups[key].append(patch)

        # Process each group with multiple patches
        for key, patches in topic_groups.items():
            if len(patches) <= 1:
                continue

            logger.debug(f"Consolidating {len(patches)} patches for {key}")

            # Sort by lock_in descending to find winner
            patches.sort(key=lambda p: p.lock_in, reverse=True)
            winner = patches[0]
            losers = patches[1:]

            # Combine reinforcement counts
            total_reinforcements = winner.reinforcement_count
            for loser in losers:
                total_reinforcements += loser.reinforcement_count

            # Update winner with combined reinforcement count
            winner.reinforcement_count = total_reinforcements
            winner.last_reinforced = datetime.now()

            # Add losers to related_to list
            for loser in losers:
                if loser.patch_id not in winner.related_to:
                    winner.related_to.append(loser.patch_id)

            await self.patch_manager.update_patch(winner)

            # Mark losers as superseded and deactivate
            for loser in losers:
                loser.supersedes = None  # Clear any existing supersedes
                loser.active = False
                loser.metadata["superseded_by"] = winner.patch_id
                loser.metadata["superseded_at"] = datetime.now().isoformat()

                await self.patch_manager.update_patch(loser)
                consolidated_count += 1

            logger.debug(f"Consolidated {len(losers)} patches into {winner.patch_id}")

        return consolidated_count

    async def cleanup_inactive_patches(self) -> int:
        """
        Remove patches with lock_in below the deactivation threshold.

        This permanently deletes patches that have:
        - lock_in below threshold
        - Already been marked as inactive

        Returns:
            Number of patches removed
        """
        logger.debug("Starting patch cleanup")
        cleaned_count = 0
        threshold = self.lock_in_deactivation_threshold

        # Get all patches (including inactive ones)
        # We need to query the database directly for inactive patches
        rows = await self.patch_manager.db.fetchall(
            """
            SELECT
                id, node_type, content, summary, confidence,
                importance, access_count, reinforcement_count,
                lock_in, lock_in_state, metadata, created_at, updated_at
            FROM memory_nodes
            WHERE node_type = ?
              AND lock_in < ?
            """,
            (self.patch_manager.NODE_TYPE, threshold)
        )

        for row in rows:
            node = self.patch_manager._row_to_node_dict(row)
            patch = PersonalityPatch.from_memory_node(node)

            # Skip active patches that are just below threshold
            # (they should be deactivated first, then cleaned on next cycle)
            if patch.active:
                continue

            # Check if this is a core patch that should not be deleted
            metadata = patch.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            if metadata.get("core_value", False):
                logger.debug(f"Skipping core patch: {patch.patch_id}")
                continue

            # Delete the inactive, low lock_in patch
            deleted = await self.patch_manager.delete_patch(patch.patch_id)
            if deleted:
                cleaned_count += 1
                logger.debug(f"Cleaned up patch: {patch.patch_id}")

        return cleaned_count

    def get_maintenance_stats(self) -> dict:
        """
        Get statistics about maintenance operations.

        Returns:
            Dictionary with maintenance statistics including:
            - last_maintenance_run: Timestamp of last run
            - total_decay_operations: Number of decay cycles run
            - total_patches_decayed: Total patches affected by decay
            - total_consolidation_operations: Number of consolidation cycles
            - total_patches_consolidated: Total patches merged
            - total_cleanup_operations: Number of cleanup cycles
            - total_patches_cleaned: Total patches removed
            - config: Current lifecycle configuration
        """
        return {
            **self._stats,
            "config": {
                "decay_enabled": self.decay_enabled,
                "consolidation_enabled": self.consolidation_enabled,
                "maintenance_interval_hours": self.maintenance_interval_hours,
                "decay_days_threshold": self.decay_days_threshold,
                "decay_amount": self.decay_amount,
                "lock_in_deactivation_threshold": self.lock_in_deactivation_threshold,
            }
        }

    async def should_run_maintenance(self) -> bool:
        """
        Check if maintenance should run based on interval.

        Returns:
            True if maintenance is due, False otherwise
        """
        last_run = self._stats.get("last_maintenance_run")

        if last_run is None:
            return True

        try:
            last_run_dt = datetime.fromisoformat(last_run)
            hours_since = (datetime.now() - last_run_dt).total_seconds() / 3600
            return hours_since >= self.maintenance_interval_hours
        except (ValueError, TypeError):
            return True


__all__ = ["LifecycleManager"]
