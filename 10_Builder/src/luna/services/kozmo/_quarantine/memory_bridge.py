"""
KOZMO Memory Bridge

Syncs KOZMO entity data into Luna's scoped MemoryMatrix.
When a project is active, entity facts get stored as project-scoped
memory nodes so Luna has awareness of characters, locations, etc.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class KozmoMemoryBridge:
    """Syncs KOZMO entities into project-scoped memory nodes."""

    def __init__(self, engine):
        """
        Args:
            engine: LunaEngine instance (for accessing MatrixActor)
        """
        self.engine = engine

    async def sync_entity_to_memory(
        self,
        project_slug: str,
        entity: dict,
    ) -> Optional[str]:
        """
        Store a KOZMO entity's core facts as a project-scoped memory node.

        Args:
            project_slug: The project slug (e.g. 'crooked-nail')
            entity: Entity dict with keys: slug, name, type, data, tags, etc.

        Returns:
            The memory node ID, or None on failure
        """
        matrix = self.engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            logger.warning("Memory bridge: Matrix not ready, skipping entity sync")
            return None

        scope = f"project:{project_slug}"
        entity_slug = entity.get("slug", "unknown")
        entity_name = entity.get("name", entity_slug)
        entity_type = entity.get("type", "unknown")
        data = entity.get("data", {})

        # Build a concise fact string from entity data
        facts = [f"{entity_name} is a {entity_type} in project '{project_slug}'."]

        if isinstance(data, dict):
            # Extract key traits
            traits = data.get("traits", [])
            if traits:
                facts.append(f"Traits: {', '.join(traits[:5])}.")

            dialogue_style = data.get("dialogue_style")
            if dialogue_style:
                facts.append(f"Dialogue style: {dialogue_style}.")

            atmosphere = data.get("atmosphere")
            if atmosphere:
                facts.append(f"Atmosphere: {atmosphere}.")

            description = data.get("description")
            if description:
                facts.append(description[:200])

        tags = entity.get("tags", [])
        if tags:
            facts.append(f"Tags: {', '.join(tags[:5])}.")

        content = " ".join(facts)

        try:
            node_id = await matrix.store_memory(
                content=content,
                node_type="FACT",
                tags=["kozmo", "entity", entity_type, entity_slug],
                confidence=90,
                scope=scope,
            )
            logger.info(f"Memory bridge: synced entity '{entity_name}' -> node {node_id} (scope={scope})")
            return node_id
        except Exception as e:
            logger.error(f"Memory bridge: failed to sync entity '{entity_name}': {e}")
            return None

    async def sync_project_manifest(
        self,
        project_slug: str,
        manifest: dict,
    ) -> Optional[str]:
        """
        Store project manifest as a project-scoped memory node.

        Args:
            project_slug: The project slug
            manifest: Project manifest dict (title, genre, etc.)

        Returns:
            The memory node ID, or None on failure
        """
        matrix = self.engine.get_actor("matrix")
        if not matrix or not matrix.is_ready:
            return None

        scope = f"project:{project_slug}"
        title = manifest.get("title", project_slug)
        genre = manifest.get("genre", "unknown")
        logline = manifest.get("logline", "")

        content = f"Project '{title}' (genre: {genre})."
        if logline:
            content += f" {logline}"

        try:
            node_id = await matrix.store_memory(
                content=content,
                node_type="FACT",
                tags=["kozmo", "project", "manifest"],
                confidence=100,
                scope=scope,
            )
            logger.info(f"Memory bridge: synced project manifest '{title}' -> {node_id}")
            return node_id
        except Exception as e:
            logger.error(f"Memory bridge: failed to sync manifest: {e}")
            return None
