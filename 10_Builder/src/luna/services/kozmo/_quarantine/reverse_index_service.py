"""
KOZMO Reverse Index Service (Phase 5)

Builds and maintains reverse index for entity→scene mappings.
Enables fast lookups: "which scenes reference this entity?"

Uses filesystem-based storage (YAML entities + .scribo documents).
No external database required.
"""
import re
import time
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from pathlib import Path
import logging

from .models import (
    EntityUsageRecord,
    SceneReference,
    ReverseIndex,
)

logger = logging.getLogger(__name__)


class ReverseIndexService:
    """Manages reverse index for entity-scene relationships using filesystem."""

    # Class-level cache: project_slug -> (ReverseIndex, timestamp)
    _cache: Dict[str, Tuple[ReverseIndex, float]] = {}
    CACHE_TTL = 60  # seconds

    def __init__(self, project_root: Path):
        from .project import ProjectPaths
        self.project_root = project_root
        self.paths = ProjectPaths(project_root)

    def rebuild_index(self, project_slug: str) -> ReverseIndex:
        """Complete rebuild of reverse index from filesystem."""
        from .entity import parse_entity_safe
        from .scribo import ScriboService

        logger.info(f"Rebuilding reverse index for project: {project_slug}")

        # Load all entities from YAML files
        entity_map: Dict[str, dict] = {}
        if self.paths.entities.exists():
            for type_dir in self.paths.entities.iterdir():
                if type_dir.is_dir():
                    for yaml_file in type_dir.glob("*.yaml"):
                        result = parse_entity_safe(yaml_file)
                        if result.entity:
                            e = result.entity
                            entity_map[e.slug] = {
                                "slug": e.slug,
                                "name": e.name,
                                "type": e.type,
                                "aliases": getattr(e, 'aliases', []) or [],
                            }

        # Load all scene documents via ScriboService
        svc = ScriboService(self.project_root)
        scenes = svc.list_documents()

        # Initialize index
        index = ReverseIndex(
            project_slug=project_slug,
            total_entities=len(entity_map),
            total_scenes=len(scenes),
        )

        # Track all body mention counts per entity
        mention_counts: Dict[str, int] = {}

        # Process each scene
        for scene in scenes:
            scene_slug = scene.slug
            scene_title = scene_slug.replace("_", " ").title()
            scene_number = self._extract_scene_number(scene_slug)
            fm = scene.frontmatter
            body = scene.body or ""

            scene_entity_refs = []

            # === FRONTMATTER REFERENCES ===

            # Characters present
            for char_slug in (fm.characters_present or []):
                if char_slug in entity_map:
                    self._add_reference(
                        index, char_slug, entity_map[char_slug],
                        SceneReference(
                            scene_slug=scene_slug,
                            scene_title=scene_title,
                            scene_number=scene_number,
                            reference_type="frontmatter",
                            field="characters_present",
                        )
                    )
                    scene_entity_refs.append(char_slug)

            # Location
            loc = fm.location if hasattr(fm, 'location') else None
            if loc and loc in entity_map:
                self._add_reference(
                    index, loc, entity_map[loc],
                    SceneReference(
                        scene_slug=scene_slug,
                        scene_title=scene_title,
                        scene_number=scene_number,
                        reference_type="frontmatter",
                        field="location",
                    )
                )
                scene_entity_refs.append(loc)

            # Props
            props = fm.props if hasattr(fm, 'props') else []
            for prop_slug in (props or []):
                if prop_slug in entity_map:
                    self._add_reference(
                        index, prop_slug, entity_map[prop_slug],
                        SceneReference(
                            scene_slug=scene_slug,
                            scene_title=scene_title,
                            scene_number=scene_number,
                            reference_type="frontmatter",
                            field="props",
                        )
                    )
                    scene_entity_refs.append(prop_slug)

            # === BODY TEXT MENTIONS ===
            mentions = self._extract_body_mentions(body, entity_map)
            for entity_slug, contexts in mentions.items():
                # Track total mention count
                mention_counts[entity_slug] = mention_counts.get(entity_slug, 0) + len(contexts)

                if entity_slug not in scene_entity_refs:
                    self._add_reference(
                        index, entity_slug, entity_map[entity_slug],
                        SceneReference(
                            scene_slug=scene_slug,
                            scene_title=scene_title,
                            scene_number=scene_number,
                            reference_type="body_mention",
                            context=contexts[0] if contexts else None,
                        )
                    )
                    scene_entity_refs.append(entity_slug)

            # Update scene → entities mapping
            index.scene_entities[scene_slug] = scene_entity_refs

        # Calculate statistics for each entity
        for entity_slug, usage in index.entity_usage.items():
            usage.total_scenes = len(usage.scenes)
            usage.mention_count = mention_counts.get(entity_slug, 0)

            if usage.scenes:
                sorted_scenes = sorted(usage.scenes, key=lambda s: s.scene_number)
                usage.first_appearance = sorted_scenes[0].scene_slug
                usage.last_appearance = sorted_scenes[-1].scene_slug

                for scene in usage.scenes:
                    usage.appearance_frequency[scene.scene_number] = \
                        usage.appearance_frequency.get(scene.scene_number, 0) + 1

        logger.info(f"Index rebuilt: {index.total_entities} entities, {index.total_scenes} scenes")

        # Cache the result
        ReverseIndexService._cache[project_slug] = (index, time.time())

        return index

    def _add_reference(
        self,
        index: ReverseIndex,
        entity_slug: str,
        entity: dict,
        reference: SceneReference,
    ):
        """Add a scene reference to the index."""
        if entity_slug not in index.entity_usage:
            index.entity_usage[entity_slug] = EntityUsageRecord(
                entity_slug=entity_slug,
                entity_name=entity["name"],
                entity_type=entity["type"],
            )
        index.entity_usage[entity_slug].scenes.append(reference)

    def _extract_body_mentions(
        self,
        body: str,
        entity_map: Dict,
    ) -> Dict[str, List[str]]:
        """Extract @mentions, ALL CAPS names, and plain entity names from scene body."""
        mentions: Dict[str, List[str]] = {}

        # Pattern 1: @EntityName mentions
        at_pattern = r'@([A-Z][A-Za-z\s]+?)(?=\s|[.,!?]|$)'
        for match in re.finditer(at_pattern, body):
            entity_name = match.group(1).strip()
            for slug, entity in entity_map.items():
                if entity["name"].lower() == entity_name.lower():
                    if slug not in mentions:
                        mentions[slug] = []
                    start = max(0, match.start() - 20)
                    end = min(len(body), match.end() + 20)
                    context = body[start:end].strip()
                    mentions[slug].append(context)
                    break

        # Pattern 2: ALL CAPS character names (Fountain convention)
        caps_pattern = r'(?:^|\n)\s*([A-Z][A-Z\s]{2,})\s*(?:\n|$)'
        for match in re.finditer(caps_pattern, body):
            entity_name = match.group(1).strip()
            for slug, entity in entity_map.items():
                if entity["type"] in ("character", "characters") and entity["name"].upper() == entity_name:
                    if slug not in mentions:
                        mentions[slug] = []
                    start = max(0, match.start() - 20)
                    end = min(len(body), match.end() + 20)
                    context = body[start:end].strip()
                    mentions[slug].append(context)
                    break

        # Pattern 3: Plain entity names + aliases (word-boundary, case-insensitive)
        for slug, entity in entity_map.items():
            names_to_check = [entity["name"]]
            names_to_check.extend(entity.get("aliases", []))
            for name in names_to_check:
                if len(name) < 3:
                    continue  # Skip very short names to avoid false positives
                pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
                for m in pattern.finditer(body):
                    if slug not in mentions:
                        mentions[slug] = []
                    start = max(0, m.start() - 30)
                    end = min(len(body), m.end() + 30)
                    mentions[slug].append(body[start:end].strip())

        return mentions

    def _extract_scene_number(self, scene_slug: str) -> int:
        """Extract numeric scene order from slug."""
        match = re.search(r'(\d+)', scene_slug)
        return int(match.group(1)) if match else 0

    def get_entity_usage(
        self,
        project_slug: str,
        entity_slug: str,
    ) -> Optional[EntityUsageRecord]:
        """Get usage record for a specific entity, using cache if available."""
        index = self._get_cached_or_rebuild(project_slug)
        return index.entity_usage.get(entity_slug)

    def get_full_index(self, project_slug: str) -> ReverseIndex:
        """Get full reverse index, using cache if available."""
        return self._get_cached_or_rebuild(project_slug)

    def _get_cached_or_rebuild(self, project_slug: str) -> ReverseIndex:
        """Return cached index if fresh, otherwise rebuild."""
        cached = ReverseIndexService._cache.get(project_slug)
        if cached:
            index, ts = cached
            if time.time() - ts < self.CACHE_TTL:
                return index
        return self.rebuild_index(project_slug)
