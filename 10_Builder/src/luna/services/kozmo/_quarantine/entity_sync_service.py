"""
KOZMO Entity Sync Service (Phase 4)

Handles bidirectional updates between entities and scenes.
- Entity name changes → update all @mentions in scenes
- Entity status changes → validate scene continuity
- New entity mentions → suggest entity creation
"""
import re
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class EntitySyncService:
    """Manages entity-scene synchronization"""

    def __init__(self, db):
        self.db = db

    async def propagate_entity_name_change(
        self,
        project_slug: str,
        entity_slug: str,
        old_name: str,
        new_name: str
    ) -> int:
        """Update entity name references in all scenes"""

        # Pattern to match @mentions (case-insensitive)
        old_pattern = re.compile(rf'@{re.escape(old_name)}\b', re.IGNORECASE)

        updated_count = 0

        # Find all scenes in this project
        scenes = await self.db.documents.find({
            "project_slug": project_slug,
            "type": "scene",
            "deleted": {"$ne": True}
        }).to_list(length=None)

        for scene in scenes:
            body = scene.get("body", "")

            # Check if old name is mentioned
            if old_pattern.search(body):
                # Replace all occurrences
                new_body = old_pattern.sub(f"@{new_name}", body)

                # Update scene
                await self.db.documents.update_one(
                    {"_id": scene["_id"]},
                    {
                        "$set": {
                            "body": new_body,
                            "updated_at": datetime.utcnow()
                        }
                    }
                )
                updated_count += 1
                logger.info(f"Updated @mentions in scene {scene.get('slug')}")

        return updated_count

    async def mark_entity_dead(
        self,
        project_slug: str,
        entity_slug: str,
        death_scene_slug: str
    ) -> List[Dict]:
        """Mark entity as dead and find problematic scenes"""

        # Update entity status
        await self.db.entities.update_one(
            {"project_slug": project_slug, "slug": entity_slug},
            {
                "$set": {
                    "status": "dead",
                    "last_appearance": death_scene_slug,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        # Extract scene number from death scene
        death_scene = await self.db.documents.find_one({
            "project_slug": project_slug,
            "slug": death_scene_slug
        })

        if not death_scene:
            return []

        death_scene_num = death_scene.get("scene_number", 0)

        # Find scenes after death that reference this entity
        problematic_scenes = await self.db.documents.find({
            "project_slug": project_slug,
            "type": "scene",
            "scene_number": {"$gt": death_scene_num},
            "frontmatter.characters_present": entity_slug
        }).to_list(length=None)

        return [
            {
                "slug": s["slug"],
                "title": s["title"],
                "scene_number": s["scene_number"]
            }
            for s in problematic_scenes
        ]

    async def find_orphaned_mentions(
        self,
        project_slug: str,
        scene_body: str
    ) -> List[Dict[str, str]]:
        """Find @mentions that don't match any entity"""

        # Extract all @mentions from body
        mention_pattern = re.compile(r'@([A-Z][A-Za-z\s]+?)(?=\s|[.,!?]|$)')
        mentions = set(mention_pattern.findall(scene_body))

        if not mentions:
            return []

        # Load all entities for this project
        entities = await self.db.entities.find({
            "project_slug": project_slug,
            "deleted": {"$ne": True}
        }).to_list(length=None)

        entity_names = {e["name"].lower() for e in entities}

        # Find mentions without matching entities
        orphaned = []
        for mention in mentions:
            if mention.lower() not in entity_names:
                orphaned.append({
                    "name": mention,
                    "suggested_slug": self._slugify(mention),
                    "suggested_type": "character"  # Default assumption
                })

        return orphaned

    async def create_entity_from_mention(
        self,
        project_slug: str,
        entity_name: str,
        entity_type: str,
        mentioned_in_scene: str
    ) -> Optional[Dict]:
        """Auto-create entity when first mentioned in a scene"""
        from datetime import datetime

        slug = self._slugify(entity_name)

        # Check if entity already exists
        existing = await self.db.entities.find_one({
            "project_slug": project_slug,
            "slug": slug
        })

        if existing:
            return None

        # Create new entity
        entity = {
            "slug": slug,
            "name": entity_name,
            "type": entity_type,
            "color": self._default_color_for_type(entity_type),
            "status": "active",
            "profile": {},
            "tags": ["auto-created"],
            "data": {},
            "first_appearance": mentioned_in_scene,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "deleted": False,
            "project_slug": project_slug
        }

        result = await self.db.entities.insert_one(entity)
        entity["_id"] = str(result.inserted_id)

        logger.info(f"Auto-created entity {slug} from mention in {mentioned_in_scene}")

        return entity

    def _slugify(self, text: str) -> str:
        """Convert name to slug"""
        slug = text.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_-]+', '_', slug)
        return slug

    def _default_color_for_type(self, entity_type: str) -> str:
        """Get default color for entity type"""
        colors = {
            "character": "#60a5fa",  # blue
            "location": "#4ade80",   # green
            "prop": "#c084fc",       # purple
            "event": "#f87171",      # red
            "lore": "#fbbf24"        # yellow
        }
        return colors.get(entity_type, "#60a5fa")


def detect_changes(old_entity: Dict, new_entity: Dict) -> List[str]:
    """Detect which fields changed between entities"""
    changes = []

    fields = ["name", "color", "status", "profile"]
    for field in fields:
        if old_entity.get(field) != new_entity.get(field):
            changes.append(field)

    return changes


from datetime import datetime
