"""
Entity Index
=============

Cross-source entity resolution for the Knowledge Compiler.
Resolves aliases ("Ada", "Youth Leader", "ada_001") to
canonical entity IDs across all Guardian source documents.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EntityProfile:
    """Resolved entity with all known attributes."""

    id: str
    name: str
    entity_type: str  # person, organization, place, tradition
    role: str = ""
    profile: str = ""
    aliases: list[str] = field(default_factory=list)
    scope: str = ""
    clan: str = ""
    location: str = ""
    household: dict = field(default_factory=dict)
    thread_presence: list[str] = field(default_factory=list)
    scope_transitions: list[dict] = field(default_factory=list)
    mention_count: int = 0
    # Node IDs that mention this entity (populated during compilation)
    mentioned_in: list[str] = field(default_factory=list)


class EntityIndex:
    """Cross-source entity resolution."""

    def __init__(self):
        self.entities: dict[str, EntityProfile] = {}
        self.aliases: dict[str, str] = {}  # normalized alias -> canonical id

    def load_entities(self, path: Path):
        """Load entities from entities_updated.json."""
        if not path.exists():
            logger.warning(f"EntityIndex: file not found: {path}")
            return

        with open(path) as f:
            data = json.load(f)

        for entry in data.get("entities", []):
            eid = entry.get("id", "")
            if not eid:
                continue

            profile = EntityProfile(
                id=eid,
                name=entry.get("name", eid),
                entity_type=entry.get("type", "person"),
                role=entry.get("role", ""),
                profile=entry.get("profile", ""),
                aliases=entry.get("aliases", []),
                scope=entry.get("scope", ""),
                clan=entry.get("clan", ""),
                location=entry.get("location", ""),
                household=entry.get("household", {}),
                thread_presence=entry.get("thread_presence", []),
                scope_transitions=entry.get("scope_transitions", []),
                mention_count=entry.get("mention_count_estimate", 0),
            )
            self.entities[eid] = profile

            # Register canonical id as alias
            self._register_alias(eid, eid)
            self._register_alias(profile.name, eid)

            # Register all declared aliases
            for alias in profile.aliases:
                self._register_alias(alias, eid)

            # Register role as alias if specific enough
            if profile.role and len(profile.role.split()) <= 5:
                self._register_alias(profile.role, eid)

        logger.info(
            f"EntityIndex: loaded {len(self.entities)} entities, "
            f"{len(self.aliases)} aliases"
        )

    def _register_alias(self, alias: str, canonical_id: str):
        """Register a normalized alias mapping."""
        key = alias.lower().strip()
        if not key:
            return
        if key in self.aliases and self.aliases[key] != canonical_id:
            logger.debug(
                f"EntityIndex: alias '{key}' already maps to "
                f"'{self.aliases[key]}', overwriting with '{canonical_id}'"
            )
        self.aliases[key] = canonical_id

    def resolve(self, name: str) -> Optional[str]:
        """Resolve any name/alias to canonical entity ID."""
        key = name.lower().strip()
        return self.aliases.get(key)

    def resolve_list(self, names: list[str]) -> list[str]:
        """Resolve a list of names, returning only successfully resolved IDs."""
        resolved = []
        seen = set()
        for name in names:
            eid = self.resolve(name)
            if eid and eid not in seen:
                resolved.append(eid)
                seen.add(eid)
        return resolved

    def resolve_mentions(self, text: str) -> list[str]:
        """
        Scan free-text for entity mentions. Returns canonical IDs.

        Checks multi-word aliases first (longest match), then single words.
        Skips very short aliases (< 3 chars) to avoid false positives.
        """
        text_lower = text.lower()
        found = []
        seen = set()

        # Sort aliases by length descending (match longest first)
        sorted_aliases = sorted(self.aliases.keys(), key=len, reverse=True)

        for alias in sorted_aliases:
            if len(alias) < 3:
                continue
            if alias in text_lower:
                canonical = self.aliases[alias]
                if canonical not in seen:
                    found.append(canonical)
                    seen.add(canonical)

        return found

    def get_profile(self, entity_id: str) -> Optional[EntityProfile]:
        """Get full profile for an entity."""
        return self.entities.get(entity_id)

    def record_mention(self, entity_id: str, node_id: str):
        """Record that a node mentions an entity."""
        profile = self.entities.get(entity_id)
        if profile:
            profile.mention_count += 1
            if node_id not in profile.mentioned_in:
                profile.mentioned_in.append(node_id)

    def significant_entities(self, min_mentions: int = 3) -> list[EntityProfile]:
        """Return entities with enough mentions to warrant a briefing."""
        return [
            p for p in self.entities.values()
            if p.mention_count >= min_mentions
        ]
