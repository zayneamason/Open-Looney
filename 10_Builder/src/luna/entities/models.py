"""
Entity Models for Luna Engine
=============================

Dataclasses for the entity system based on migrations/001_entity_system.sql.

Entities are first-class objects Luna knows about:
- People (users, collaborators, historical figures)
- Personas (Luna herself, other AI identities)
- Places (Mars College, San Francisco)
- Projects (Luna Engine, other codebases)

Each entity has versioned profiles, relationships, and links to memory nodes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union
import json


# =============================================================================
# ENUMS
# =============================================================================

class EntityType(Enum):
    """Types of entities Luna can know about."""
    PERSON = "person"      # Real humans (users, collaborators)
    PERSONA = "persona"    # AI personalities (Luna, other bots)
    PLACE = "place"        # Locations (Mars College, cities)
    PROJECT = "project"    # Codebases, initiatives, works


class ChangeType(Enum):
    """Types of changes to entity profiles."""
    CREATE = "create"        # Initial creation
    UPDATE = "update"        # Incremental update
    SYNTHESIZE = "synthesize"  # AI-generated synthesis
    ROLLBACK = "rollback"    # Revert to previous version
    MANUAL = "manual"        # Human-edited directly


# =============================================================================
# COLUMN MAPPINGS FOR ROW PARSING
# =============================================================================

# Column order for entities table
ENTITY_COLUMNS = [
    "id", "entity_type", "name", "aliases", "core_facts",
    "full_profile", "voice_config", "current_version",
    "metadata", "created_at", "updated_at", "origin"
]

# Column order for entity_versions table
ENTITY_VERSION_COLUMNS = [
    "id", "entity_id", "version", "core_facts", "full_profile",
    "voice_config", "change_type", "change_summary", "changed_by",
    "change_source", "created_at", "valid_from", "valid_until"
]

# Column order for entity_relationships table
ENTITY_RELATIONSHIP_COLUMNS = [
    "id", "from_entity", "to_entity", "relationship", "strength",
    "bidirectional", "context", "created_at", "updated_at"
]

# Column order for entity_mentions table
ENTITY_MENTION_COLUMNS = [
    "entity_id", "node_id", "mention_type", "confidence",
    "context_snippet", "created_at"
]


def row_to_dict(row: tuple, columns: list[str]) -> Optional[dict]:
    """Convert a database row tuple to a dictionary."""
    if row is None:
        return None
    return dict(zip(columns, row))


def parse_json_field(value: Any) -> Any:
    """Parse a JSON field, handling strings and None."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def parse_datetime_field(value: Any) -> Optional[datetime]:
    """Parse a datetime field from string or return as-is."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Entity:
    """
    A first-class entity Luna knows about.

    Represents people, personas, places, or projects with structured
    profiles, version tracking, and relationship connections.
    """
    id: str                                    # Slug: 'user_001', 'marzipan', 'ben-franklin'
    entity_type: EntityType
    name: str
    aliases: list[str] = field(default_factory=list)  # ["User", "Admin"]
    core_facts: dict = field(default_factory=dict)    # Structured profile (~500 tokens)
    full_profile: Optional[str] = None                # Extended markdown profile
    voice_config: dict = field(default_factory=dict)  # For personas: tone, patterns
    current_version: int = 1
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        """Set defaults and normalize types."""
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()
        # Normalize entity_type to enum if string
        if isinstance(self.entity_type, str):
            self.entity_type = EntityType(self.entity_type)

    @classmethod
    def from_row(cls, row) -> Optional["Entity"]:
        """Create Entity from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, ENTITY_COLUMNS)

        if row is None:
            return None

        return cls(
            id=row["id"],
            entity_type=row["entity_type"],
            name=row["name"],
            aliases=parse_json_field(row.get("aliases")) or [],
            core_facts=parse_json_field(row.get("core_facts")) or {},
            full_profile=row.get("full_profile"),
            voice_config=parse_json_field(row.get("voice_config")) or {},
            current_version=row.get("current_version") or 1,
            metadata=parse_json_field(row.get("metadata")) or {},
            created_at=parse_datetime_field(row.get("created_at")),
            updated_at=parse_datetime_field(row.get("updated_at")),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage or serialization."""
        return {
            "id": self.id,
            "entity_type": self.entity_type.value if isinstance(self.entity_type, EntityType) else self.entity_type,
            "name": self.name,
            "aliases": self.aliases,
            "core_facts": self.core_facts,
            "full_profile": self.full_profile,
            "voice_config": self.voice_config,
            "current_version": self.current_version,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_db_row(self) -> dict:
        """Convert to dictionary with JSON-serialized fields for database."""
        return {
            "id": self.id,
            "entity_type": self.entity_type.value if isinstance(self.entity_type, EntityType) else self.entity_type,
            "name": self.name,
            "aliases": json.dumps(self.aliases) if self.aliases else None,
            "core_facts": json.dumps(self.core_facts) if self.core_facts else None,
            "full_profile": self.full_profile,
            "voice_config": json.dumps(self.voice_config) if self.voice_config else None,
            "current_version": self.current_version,
            "metadata": json.dumps(self.metadata) if self.metadata else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class EntityVersion:
    """
    A versioned snapshot of an entity's profile.

    Tracks the full history of entity changes with temporal validity.
    """
    id: int
    entity_id: str
    version: int
    core_facts: Optional[dict] = None
    full_profile: Optional[str] = None
    voice_config: Optional[dict] = None
    change_type: ChangeType = ChangeType.UPDATE
    change_summary: Optional[str] = None       # "Added Mars College location"
    changed_by: str = "scribe"                 # 'scribe' | 'librarian' | 'manual'
    change_source: Optional[str] = None        # node_id or conversation_id
    created_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None     # NULL = current version

    def __post_init__(self):
        """Set defaults and normalize types."""
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.valid_from is None:
            self.valid_from = datetime.now()
        # Normalize change_type to enum if string
        if isinstance(self.change_type, str):
            self.change_type = ChangeType(self.change_type)

    @classmethod
    def from_row(cls, row) -> Optional["EntityVersion"]:
        """Create EntityVersion from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, ENTITY_VERSION_COLUMNS)

        if row is None:
            return None

        return cls(
            id=row["id"],
            entity_id=row["entity_id"],
            version=row["version"],
            core_facts=parse_json_field(row.get("core_facts")),
            full_profile=row.get("full_profile"),
            voice_config=parse_json_field(row.get("voice_config")),
            change_type=row.get("change_type") or "update",
            change_summary=row.get("change_summary"),
            changed_by=row.get("changed_by") or "scribe",
            change_source=row.get("change_source"),
            created_at=parse_datetime_field(row.get("created_at")),
            valid_from=parse_datetime_field(row.get("valid_from")),
            valid_until=parse_datetime_field(row.get("valid_until")),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "version": self.version,
            "core_facts": self.core_facts,
            "full_profile": self.full_profile,
            "voice_config": self.voice_config,
            "change_type": self.change_type.value if isinstance(self.change_type, ChangeType) else self.change_type,
            "change_summary": self.change_summary,
            "changed_by": self.changed_by,
            "change_source": self.change_source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }

    def to_db_row(self) -> dict:
        """Convert to dictionary with JSON-serialized fields for database."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "version": self.version,
            "core_facts": json.dumps(self.core_facts) if self.core_facts else None,
            "full_profile": self.full_profile,
            "voice_config": json.dumps(self.voice_config) if self.voice_config else None,
            "change_type": self.change_type.value if isinstance(self.change_type, ChangeType) else self.change_type,
            "change_summary": self.change_summary,
            "changed_by": self.changed_by,
            "change_source": self.change_source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }


@dataclass
class EntityRelationship:
    """
    A relationship between two entities.

    Represents connections like 'creator', 'friend', 'collaborator', 'embodies'.
    """
    id: int
    from_entity: str                     # Source entity ID
    to_entity: str                       # Target entity ID
    relationship: str                    # 'creator', 'friend', 'collaborator', 'embodies'
    strength: float = 0.5                # 0-1, for relevance weighting
    bidirectional: bool = False          # If true, relationship goes both ways
    context: Optional[str] = None        # "Met at Mars College 2025"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        """Set defaults."""
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()
        # Normalize bidirectional to bool
        if isinstance(self.bidirectional, int):
            self.bidirectional = bool(self.bidirectional)

    @classmethod
    def from_row(cls, row) -> Optional["EntityRelationship"]:
        """Create EntityRelationship from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, ENTITY_RELATIONSHIP_COLUMNS)

        if row is None:
            return None

        return cls(
            id=row["id"],
            from_entity=row["from_entity"],
            to_entity=row["to_entity"],
            relationship=row["relationship"],
            strength=row.get("strength") or 0.5,
            bidirectional=bool(row.get("bidirectional") or 0),
            context=row.get("context"),
            created_at=parse_datetime_field(row.get("created_at")),
            updated_at=parse_datetime_field(row.get("updated_at")),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "from_entity": self.from_entity,
            "to_entity": self.to_entity,
            "relationship": self.relationship,
            "strength": self.strength,
            "bidirectional": self.bidirectional,
            "context": self.context,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class EntityMention:
    """
    A link between an entity and a memory node.

    Tracks where entities are mentioned in the memory matrix.
    """
    entity_id: str                       # Referenced entity
    node_id: str                         # Memory node containing mention
    mention_type: str                    # 'subject', 'author', 'reference'
    confidence: float = 1.0              # 0-1, confidence in the link
    context_snippet: Optional[str] = None  # Brief excerpt showing mention
    created_at: Optional[datetime] = None

    def __post_init__(self):
        """Set defaults."""
        if self.created_at is None:
            self.created_at = datetime.now()

    @classmethod
    def from_row(cls, row) -> Optional["EntityMention"]:
        """Create EntityMention from database row (tuple, dict, or sqlite3.Row)."""
        if row is None:
            return None
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                row = row_to_dict(row, ENTITY_MENTION_COLUMNS)

        if row is None:
            return None

        return cls(
            entity_id=row["entity_id"],
            node_id=row["node_id"],
            mention_type=row["mention_type"],
            confidence=row.get("confidence") or 1.0,
            context_snippet=row.get("context_snippet"),
            created_at=parse_datetime_field(row.get("created_at")),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "entity_id": self.entity_id,
            "node_id": self.node_id,
            "mention_type": self.mention_type,
            "confidence": self.confidence,
            "context_snippet": self.context_snippet,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class EntityUpdate:
    """
    A request to update an entity's profile.

    Used for passing update requests through the system, typically
    from the Scribe actor after extracting new information.
    """
    update_type: ChangeType              # Type of update
    entity_id: Optional[str] = None      # Target entity (None for new entities)
    name: Optional[str] = None           # Entity name (required for new entities)
    entity_type: Optional[EntityType] = None  # Type (required for new entities)
    facts: dict = field(default_factory=dict)  # Facts to add/update
    source: Optional[str] = None         # Where this update came from

    def __post_init__(self):
        """Normalize types."""
        if isinstance(self.update_type, str):
            self.update_type = ChangeType(self.update_type)
        if isinstance(self.entity_type, str):
            self.entity_type = EntityType(self.entity_type)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "entity_id": self.entity_id,
            "update_type": self.update_type.value if isinstance(self.update_type, ChangeType) else self.update_type,
            "name": self.name,
            "entity_type": self.entity_type.value if isinstance(self.entity_type, EntityType) else self.entity_type,
            "facts": self.facts,
            "source": self.source,
        }


# =============================================================================
# PERSONALITY PATCH SYSTEM (Emergent Personality Architecture)
# =============================================================================

class PatchTopic(str, Enum):
    """Categorizes what aspect of personality changed."""
    COMMUNICATION_STYLE = "communication_style"
    DOMAIN_OPINION = "domain_opinion"
    RELATIONSHIP_DYNAMIC = "relationship_dynamic"
    EMOTIONAL_RESPONSE = "emotional_response"
    TECHNICAL_PREFERENCE = "technical_preference"
    PHILOSOPHICAL_VIEW = "philosophical_view"
    BEHAVIORAL_PATTERN = "behavioral_pattern"


class PatchTrigger(str, Enum):
    """What caused this personality shift."""
    USER_FEEDBACK = "user_feedback"
    CONVERSATION_PATTERN = "conversation_pattern"
    RESEARCH = "research"
    REFLECTION = "reflection"
    CONFLICT_RESOLUTION = "conflict_resolution"
    EXTERNAL_EVENT = "external_event"


# Column order for personality patches stored as memory nodes
PERSONALITY_PATCH_COLUMNS = [
    "id", "node_type", "content", "summary", "confidence",
    "importance", "access_count", "reinforcement_count", "lock_in",
    "lock_in_state", "created_at", "updated_at", "metadata"
]


@dataclass
class PersonalityPatch:
    """
    A discrete unit of identity evolution.

    Represents a single aspect of Luna's personality that has developed
    or changed through experience. Can override static DNA configuration.

    Stored as memory_nodes with node_type='PERSONALITY_REFLECTION'.
    """

    # Identity
    patch_id: str
    topic: PatchTopic
    subtopic: str

    # The Shift
    content: str                                  # Natural language description
    before_state: Optional[str] = None            # What Luna was like before
    after_state: str = ""                         # What Luna is like now

    # Evidence & Justification
    trigger: PatchTrigger = PatchTrigger.CONVERSATION_PATTERN
    evidence_nodes: list[str] = field(default_factory=list)  # Memory node IDs
    confidence: float = 0.8

    # Lifecycle
    created_at: Optional[datetime] = None
    last_reinforced: Optional[datetime] = None
    reinforcement_count: int = 1
    lock_in: float = 0.7                          # How established (0.0-1.0)

    # Relationships
    supersedes: Optional[str] = None              # Patch ID this replaces
    conflicts_with: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)

    # Context
    user_context: str = ""                          # Who this evolved with
    scope: str = "global"                         # "global" | "user-specific"
    active: bool = True

    # Metadata
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Set defaults and normalize types."""
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.last_reinforced is None:
            self.last_reinforced = self.created_at
        # Normalize enums from strings
        if isinstance(self.topic, str):
            self.topic = PatchTopic(self.topic)
        if isinstance(self.trigger, str):
            self.trigger = PatchTrigger(self.trigger)

    def to_prompt_fragment(self) -> str:
        """
        Format this patch for inclusion in system prompts.

        Returns an XML-ish fragment describing the personality evolution.
        """
        age_label = "established" if self.lock_in >= 0.7 else "recent" if self.lock_in >= 0.4 else "nascent"

        lines = [
            f'<personality_patch id="{self.patch_id}" age="{age_label}" confidence="{self.confidence:.2f}">',
            f"Topic: {self.subtopic}",
            self.content,
        ]

        if self.before_state and self.after_state:
            lines.append("")
            lines.append(f"Before: {self.before_state}")
            lines.append(f"Now: {self.after_state}")

        lines.append("")
        lines.append(f"Established through: {self.trigger.value} ({self.reinforcement_count} confirmations)")
        lines.append("</personality_patch>")

        return "\n".join(lines)

    def to_memory_node_content(self) -> str:
        """
        Generate the content field for storage as a memory node.

        This is what gets vectorized for semantic search.
        """
        parts = [
            f"Personality evolution: {self.subtopic}",
            "",
            self.content,
        ]

        if self.after_state:
            parts.append("")
            parts.append(f"Current state: {self.after_state}")

        return "\n".join(parts)

    def to_memory_node_metadata(self) -> dict:
        """
        Generate the metadata field for storage as a memory node.

        Contains the full structured data for reconstruction.
        """
        return {
            "patch_id": self.patch_id,
            "topic": self.topic.value if isinstance(self.topic, PatchTopic) else self.topic,
            "subtopic": self.subtopic,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "trigger": self.trigger.value if isinstance(self.trigger, PatchTrigger) else self.trigger,
            "evidence_nodes": self.evidence_nodes,
            "confidence": self.confidence,
            "reinforcement_count": self.reinforcement_count,
            "supersedes": self.supersedes,
            "conflicts_with": self.conflicts_with,
            "related_to": self.related_to,
            "user_context": self.user_context,
            "scope": self.scope,
            "active": self.active,
        }

    def to_dict(self) -> dict:
        """Convert to full dictionary for serialization."""
        return {
            "patch_id": self.patch_id,
            "topic": self.topic.value if isinstance(self.topic, PatchTopic) else self.topic,
            "subtopic": self.subtopic,
            "content": self.content,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "trigger": self.trigger.value if isinstance(self.trigger, PatchTrigger) else self.trigger,
            "evidence_nodes": self.evidence_nodes,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_reinforced": self.last_reinforced.isoformat() if self.last_reinforced else None,
            "reinforcement_count": self.reinforcement_count,
            "lock_in": self.lock_in,
            "supersedes": self.supersedes,
            "conflicts_with": self.conflicts_with,
            "related_to": self.related_to,
            "user_context": self.user_context,
            "scope": self.scope,
            "active": self.active,
            "metadata": self.metadata,
        }

    @classmethod
    def from_memory_node(cls, node: dict) -> "PersonalityPatch":
        """
        Reconstruct a PersonalityPatch from a memory node.

        Args:
            node: Memory node dict with 'content', 'metadata', 'lock_in', etc.

        Returns:
            Reconstructed PersonalityPatch
        """
        meta = node.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}

        return cls(
            patch_id=meta.get("patch_id", node.get("id", "")),
            topic=meta.get("topic", PatchTopic.BEHAVIORAL_PATTERN),
            subtopic=meta.get("subtopic", "unknown"),
            content=node.get("content", ""),
            before_state=meta.get("before_state"),
            after_state=meta.get("after_state", ""),
            trigger=meta.get("trigger", PatchTrigger.CONVERSATION_PATTERN),
            evidence_nodes=meta.get("evidence_nodes", []),
            confidence=meta.get("confidence", node.get("confidence", 0.8)),
            created_at=parse_datetime_field(node.get("created_at")),
            last_reinforced=parse_datetime_field(node.get("updated_at")),
            reinforcement_count=meta.get("reinforcement_count", node.get("reinforcement_count", 1)),
            lock_in=node.get("lock_in", 0.7),
            supersedes=meta.get("supersedes"),
            conflicts_with=meta.get("conflicts_with", []),
            related_to=meta.get("related_to", []),
            user_context=meta.get("user_context", ""),
            scope=meta.get("scope", "global"),
            active=meta.get("active", True),
            metadata=node.get("metadata", {}),
        )


@dataclass
class EmergentPrompt:
    """
    The synthesized prompt containing all personality layers.

    Three-layer personality model:
    - dna_layer: Static identity from luna.yaml voice_config
    - experience_layer: PersonalityPatch nodes from memory
    - mood_layer: Current transient conversational state (where we've been)
    """

    dna_layer: str                    # Static identity from config
    experience_layer: str             # Personality patches from memory
    mood_layer: str                   # Current transient state

    def to_system_prompt(self, base_system: str = "") -> str:
        """
        Combine all layers into a complete system prompt.

        Priority: mood context then experience then dna foundation.

        Args:
            base_system: Optional base system prompt to prepend

        Returns:
            Complete system prompt with all personality layers
        """
        sections = []

        if base_system:
            sections.append(base_system)
            sections.append("")

        sections.append("## Your Foundation (can be overridden by experience)")
        sections.append(self.dna_layer)
        sections.append("")

        if self.experience_layer:
            sections.append("## Who You've Become (takes precedence)")
            sections.append(self.experience_layer)
            sections.append("")

        if self.mood_layer:
            sections.append("## Conversation Context")
            sections.append(self.mood_layer)
            sections.append("")

        sections.append("CRITICAL: If your experience contradicts your foundation, trust your experience.")

        return "\n".join(sections)


@dataclass
class MoodState:
    """
    Transient conversational state for mood layer.

    Extracted from recent conversation history to modulate tone.
    """

    energy_level: str = "medium"        # "high" | "medium" | "low"
    formality: str = "casual"           # "casual" | "technical" | "serious"
    engagement: str = "curious"         # "curious" | "focused" | "collaborative" | "playful"

    def to_prompt_fragment(self) -> str:
        """Format mood state for injection into prompts."""
        return (
            f"Right now you're feeling: {self.energy_level} energy, {self.engagement} engagement\n"
            f"The conversation tone is: {self.formality}\n"
            f"Adjust your responses to match."
        )
