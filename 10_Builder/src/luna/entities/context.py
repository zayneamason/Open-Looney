"""
Entity Context Injection for Luna Engine
=========================================

This module handles loading entity profiles into Luna's context.
It bridges the entity system with Luna's prompt context, providing:

- IdentityBuffer: Always-loaded context (~2048 tokens)
- EntityContext: On-demand entity loading at various depths
- Persona activation for voice/behavior switching

The entity context is Luna's working knowledge of who she's talking to
and who she knows. It flows into every generation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union, TYPE_CHECKING
from enum import Enum
import json
import logging
import os
from pathlib import Path

from ..substrate.database import MemoryDatabase
from ..core.owner import owner_entity_id, owner_configured, get_owner

# Import personality models for emergent prompt system
try:
    from .models import EmergentPrompt, MoodState, PersonalityPatch
    PERSONALITY_MODELS_AVAILABLE = True
except ImportError:
    PERSONALITY_MODELS_AVAILABLE = False


if TYPE_CHECKING:
    from .storage import PersonalityPatchManager

logger = logging.getLogger(__name__)

# Module-level personality config cache (mtime-based)
_personality_config_cache: dict = {}
_personality_config_mtime: float = 0
from luna.core.paths import config_dir
_PERSONALITY_CONFIG_PATH = config_dir() / "personality.json"


def _load_personality_config() -> dict:
    """Load personality.json with mtime caching."""
    global _personality_config_cache, _personality_config_mtime
    try:
        if not _PERSONALITY_CONFIG_PATH.exists():
            return {}
        mtime = _PERSONALITY_CONFIG_PATH.stat().st_mtime
        if mtime == _personality_config_mtime and _personality_config_cache:
            return _personality_config_cache
        _personality_config_mtime = mtime
        with open(_PERSONALITY_CONFIG_PATH, "r") as f:
            _personality_config_cache = json.load(f)
        return _personality_config_cache
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to load personality config: %s", e)
        return _personality_config_cache or {}


# =============================================================================
# ENTITY TYPES (inline since models.py may not exist yet)
# =============================================================================

class EntityType(str, Enum):
    """Types of entities Luna can know about."""
    PERSON = "person"
    PERSONA = "persona"
    PLACE = "place"
    PROJECT = "project"


# =============================================================================
# ENTITY DATACLASS
# =============================================================================

@dataclass
class Entity:
    """
    A first-class entity in Luna's knowledge.

    Represents people, personas, places, and projects with structured
    profiles, version history, and relationship tracking.
    """
    id: str                          # Slug: 'user_001', 'marzipan', 'ben-franklin'
    entity_type: str                 # person, persona, place, project
    name: str
    aliases: list[str] = field(default_factory=list)
    core_facts: dict = field(default_factory=dict)  # Structured, ~500 tokens max
    full_profile: Optional[str] = None              # Extended markdown
    voice_config: Optional[dict] = None             # For personas
    current_version: int = 1
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Union[tuple, dict]) -> "Entity":
        """Create Entity from database row."""
        if row is None:
            return None

        # Handle non-dict rows (tuple, sqlite3.Row, aiosqlite.Row)
        if not isinstance(row, dict):
            try:
                row = dict(row)
            except (TypeError, ValueError):
                columns = [
                    "id", "entity_type", "name", "aliases", "core_facts",
                    "full_profile", "voice_config", "current_version",
                    "metadata", "created_at", "updated_at"
                ]
                row = dict(zip(columns, row))

        # Parse JSON fields
        def parse_json(val, default):
            if val is None:
                return default
            if isinstance(val, (dict, list)):
                return val
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return default

        # Parse datetime fields
        def parse_datetime(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                return None

        return cls(
            id=row["id"],
            entity_type=row["entity_type"],
            name=row["name"],
            aliases=parse_json(row.get("aliases"), []),
            core_facts=parse_json(row.get("core_facts"), {}),
            full_profile=row.get("full_profile"),
            voice_config=parse_json(row.get("voice_config"), None),
            current_version=row.get("current_version") or 1,
            metadata=parse_json(row.get("metadata"), {}),
            created_at=parse_datetime(row.get("created_at")),
            updated_at=parse_datetime(row.get("updated_at")),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "entity_type": self.entity_type,
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


# =============================================================================
# IDENTITY BUFFER
# =============================================================================

@dataclass
class IdentityBuffer:
    """
    The Identity Buffer is a ~2048 token prefix always loaded into Luna's context.

    Contains:
    - self_entity: Luna's self-knowledge
    - user_entity: The current user entity
    - key_relationships: Top entities by interaction frequency
    - active_personas: Currently active personas
    """
    self_entity: Entity                                # Luna's self-knowledge
    user_entity: Optional[Entity] = None               # The current user entity
    key_relationships: list[tuple[Entity, str]] = field(default_factory=list)  # (entity, relationship_type)
    active_personas: list[Entity] = field(default_factory=list)

    def to_prompt(self) -> str:
        """
        Format the identity buffer as a prompt context block.

        Target: ~2048 tokens. Provides Luna with:
        - Her own identity and purpose
        - Her voice and communication style
        - Who she's talking to
        - Key people she knows
        - Active personas

        Returns:
            Formatted string suitable for system prompt injection
        """
        sections = []

        # Self-knowledge section
        sections.append("## Luna's Identity\n")
        if self.self_entity.core_facts:
            for key, value in self.self_entity.core_facts.items():
                sections.append(f"- {key}: {value}")
        sections.append("")

        # Voice configuration section (CRITICAL for personality)
        if self.self_entity.voice_config:
            sections.append("## Luna's Voice\n")
            voice = self.self_entity.voice_config

            if voice.get('tone'):
                sections.append(f"**Tone**: {voice['tone']}")
                sections.append("")

            if voice.get('patterns'):
                sections.append("**Communication Patterns**:")
                for pattern in voice['patterns']:
                    sections.append(f"- {pattern}")
                sections.append("")

            if voice.get('constraints'):
                sections.append("**Core Principles** (never violate):")
                for constraint in voice['constraints']:
                    sections.append(f"- {constraint}")
                sections.append("")

        # Current user section
        if self.user_entity:
            sections.append(f"## Current User: {self.user_entity.name}\n")
            if self.user_entity.aliases:
                sections.append(f"Also known as: {', '.join(self.user_entity.aliases)}")
            if self.user_entity.core_facts:
                for key, value in self.user_entity.core_facts.items():
                    sections.append(f"- {key}: {value}")
            sections.append("")

        # Key relationships section
        if self.key_relationships:
            sections.append("## Key Relationships\n")
            for entity, relationship in self.key_relationships[:5]:  # Top 5
                entity_facts = []
                if entity.core_facts:
                    # Take top 2-3 facts per entity
                    for i, (key, value) in enumerate(entity.core_facts.items()):
                        if i >= 3:
                            break
                        entity_facts.append(f"{key}: {value}")

                facts_str = "; ".join(entity_facts) if entity_facts else "No details"
                sections.append(f"- **{entity.name}** ({relationship}): {facts_str}")
            sections.append("")

        # Active personas section
        if self.active_personas:
            sections.append("## Active Personas\n")
            for persona in self.active_personas:
                status = "active" if persona.voice_config else "background"
                sections.append(f"- {persona.name}: {persona.core_facts.get('role', 'N/A')} ({status})")
            sections.append("")

        return "\n".join(sections)

    def token_estimate(self) -> int:
        """
        Estimate token count for this buffer.

        Uses rough heuristic of 4 characters per token.
        """
        text = self.to_prompt()
        return len(text) // 4

    # =========================================================================
    # EMERGENT PERSONALITY (Three-Layer System)
    # =========================================================================

    async def get_emergent_prompt(
        self,
        query: str,
        conversation_history: list,
        patch_manager: Optional["PersonalityPatchManager"] = None,
        limit: int = 10
    ) -> Optional["EmergentPrompt"]:
        """
        Generate an EmergentPrompt with four personality layers.

        This synthesizes Luna's personality from:
        1. DNA layer: Static identity from voice_config
        2. Experience layer: Relevant PersonalityPatch nodes from memory
        3. Mood layer: Current conversational state (where we've been)
        4. Voice Lock: Query-specific voice tuning (where this response needs to go)

        Args:
            query: The user's current message (for semantic patch search)
            conversation_history: Recent conversation turns for mood analysis
            patch_manager: Optional PersonalityPatchManager for experience layer
            limit: Max patches to include in experience layer

        Returns:
            EmergentPrompt with all four layers, or None if models unavailable
        """
        if not PERSONALITY_MODELS_AVAILABLE:
            logger.debug("Personality models not available, skipping emergent prompt")
            return None

        # Check emergent_prompt.enabled from config
        config = _load_personality_config()
        ep_config = config.get("emergent_prompt", {})
        if not ep_config.get("enabled", True):
            logger.debug("Emergent prompt disabled in config")
            return None

        # Layer 1: DNA (Static from voice_config)
        dna_layer = self._build_dna_layer()

        # Layer 2: Experience (Dynamic from personality patches)
        experience_layer = ""
        if patch_manager:
            max_patches = ep_config.get("max_patches_in_prompt", limit)
            min_lock_in = ep_config.get("min_lock_in_for_inclusion", 0.3)
            experience_layer = await self._build_experience_layer(
                query, patch_manager, max_patches, min_lock_in
            )

        # Layer 3: Mood (Transient from conversation analysis)
        mood_layer = self._build_mood_layer(conversation_history)

        return EmergentPrompt(
            dna_layer=dna_layer,
            experience_layer=experience_layer,
            mood_layer=mood_layer,
        )

    def _build_dna_layer(self) -> str:
        """
        Build the DNA layer from static voice_config.

        This is the foundation layer that can be overridden by experience.
        Includes: tone, patterns, constraints, style mechanics, and examples.
        """
        sections = []

        # Core identity
        sections.append("### Core Identity")
        if self.self_entity.core_facts:
            for key, value in self.self_entity.core_facts.items():
                sections.append(f"- {key}: {value}")
        sections.append("")

        # Voice configuration
        if self.self_entity.voice_config:
            voice = self.self_entity.voice_config

            if voice.get('tone'):
                sections.append(f"### Base Tone: {voice['tone']}")
                sections.append("")

            if voice.get('patterns'):
                sections.append("### Default Communication Patterns")
                for pattern in voice['patterns']:
                    sections.append(f"- {pattern}")
                sections.append("")

            if voice.get('constraints'):
                sections.append("### Inviolable Principles")
                for constraint in voice['constraints']:
                    sections.append(f"- {constraint}")
                sections.append("")

            # Style mechanics (detailed voice guidelines)
            if voice.get('style_guidelines'):
                style = voice['style_guidelines']

                if style.get('mechanics'):
                    sections.append("### Style Mechanics")
                    for mechanic in style['mechanics']:
                        sections.append(f"- {mechanic}")
                    sections.append("")

                if style.get('emojis'):
                    emoji_config = style['emojis']
                    sections.append(f"### Emoji Usage: {emoji_config.get('usage', 'Minimal')}")
                    if emoji_config.get('meanings'):
                        for emoji, meaning in emoji_config['meanings'].items():
                            sections.append(f"- {emoji}: {meaning}")
                    sections.append("")

                if style.get('formality'):
                    formality = style['formality']
                    sections.append("### Formality")
                    sections.append(f"- Baseline: {formality.get('baseline', 'Not specified')}")
                    if formality.get('avoid'):
                        sections.append(f"- Avoid: {formality['avoid']}")
                    if formality.get('embrace'):
                        sections.append(f"- Embrace: {formality['embrace']}")
                    sections.append("")

            # NOTE: few_shot_examples REMOVED (2026-02-03)
            # Voice examples caused copying behavior. The LoRA has Luna's voice
            # in its weights — showing examples causes pattern-matching instead
            # of authentic generation. See: PROMPT_COMPOSITION_ANALYSIS.md

        return "\n".join(sections)

    async def _build_experience_layer(
        self,
        query: str,
        patch_manager: "PersonalityPatchManager",
        limit: int = 10,
        min_lock_in: float = 0.3
    ) -> str:
        """
        Build the experience layer from relevant personality patches.

        Searches for patches semantically related to the query and
        formats them for prompt injection.

        Args:
            query: The user's query for semantic search
            patch_manager: The PersonalityPatchManager instance
            limit: Maximum patches to include
            min_lock_in: Minimum lock_in score for patch inclusion

        Returns:
            Formatted experience layer string
        """
        try:
            # Search for relevant patches
            patches = await patch_manager.search_patches(
                query=query,
                limit=limit,
                min_lock_in=min_lock_in,
                active_only=True
            )

            if not patches:
                return ""

            # Sort by lock_in (most established first)
            patches.sort(key=lambda p: p.lock_in, reverse=True)

            _owner_name = get_owner().display_name or "your primary collaborator"
            sections = [f"Based on your shared history with {_owner_name}:\n"]

            for patch in patches:
                sections.append(patch.to_prompt_fragment())
                sections.append("")

            return "\n".join(sections)

        except Exception as e:
            logger.warning(f"Failed to build experience layer: {e}")
            return ""

    def _build_mood_layer(self, conversation_history: list) -> str:
        """
        Build the mood layer from recent conversation analysis.

        Analyzes the last few messages to determine current conversational
        energy, formality, and engagement style.

        Args:
            conversation_history: Recent conversation turns (list of dicts/messages)

        Returns:
            Formatted mood layer string
        """
        if not PERSONALITY_MODELS_AVAILABLE:
            return ""

        # Check mood_analysis.enabled from config
        config = _load_personality_config()
        mood_config = config.get("mood_analysis", {})
        if not mood_config.get("enabled", True):
            return ""

        mood = self._analyze_conversation_mood(conversation_history, mood_config)
        return mood.to_prompt_fragment()

    def _analyze_conversation_mood(self, conversation_history: list, mood_config: dict = None) -> "MoodState":
        """
        Analyze conversation to determine current mood state.

        Uses simple heuristics to extract mood signals:
        - Message length → energy level
        - Technical terms → formality
        - Question count → engagement style

        Args:
            conversation_history: Recent conversation turns
            mood_config: Optional mood_analysis config from personality.json

        Returns:
            MoodState with energy, formality, and engagement
        """
        if not PERSONALITY_MODELS_AVAILABLE:
            # Return default if models not available
            return type('MoodState', (), {
                'to_prompt_fragment': lambda: "Conversational context unavailable."
            })()

        if not conversation_history:
            return MoodState()

        mood_config = mood_config or {}
        recent_count = mood_config.get("recent_messages_count", 5)

        # Take last N messages
        recent = conversation_history[-recent_count:] if len(conversation_history) >= recent_count else conversation_history

        # Extract content from messages (handle dict or object)
        contents = []
        for msg in recent:
            if isinstance(msg, dict):
                contents.append(msg.get('content', ''))
            elif hasattr(msg, 'content'):
                contents.append(msg.content)
            else:
                contents.append(str(msg))

        # Energy: based on average message length
        energy_high = mood_config.get("energy_threshold_high", 200)
        energy_low = mood_config.get("energy_threshold_low", 50)
        avg_length = sum(len(c) for c in contents) / len(contents) if contents else 50
        if avg_length > energy_high:
            energy = "high"
        elif avg_length > energy_low:
            energy = "medium"
        else:
            energy = "low"

        # Formality: check for technical terms, code, or casual markers
        all_text = " ".join(contents).lower()
        technical_markers = ['function', 'class', 'async', 'await', 'api', 'database',
                            'architecture', 'implement', 'algorithm', 'parameter']
        casual_markers = ['haha', 'lol', '😊', '👍', 'cool', 'nice', 'awesome', 'yo']

        tech_count = sum(1 for t in technical_markers if t in all_text)
        casual_count = sum(1 for c in casual_markers if c in all_text)

        if tech_count > 3:
            formality = "technical"
        elif casual_count > 2:
            formality = "casual"
        else:
            formality = "balanced"

        # Engagement: question marks and conversation flow
        question_count = sum(c.count('?') for c in contents)

        if question_count > 3:
            engagement = "curious"
        elif '!' in all_text:
            engagement = "enthusiastic"
        elif tech_count > 2:
            engagement = "focused"
        else:
            engagement = "collaborative"

        return MoodState(
            energy_level=energy,
            formality=formality,
            engagement=engagement,
        )


# =============================================================================
# ENTITY CONTEXT MANAGER
# =============================================================================

class EntityContext:
    """
    Manages entity context loading for Luna's prompts.

    Provides:
    - load_identity_buffer(): Always-on context
    - load_entity_context(): On-demand entity details
    - get_entity_memories(): Related memory nodes
    - activate_persona(): Switch voice/behavior
    - build_framed_context(): Temporal framing for memories/conversation

    This bridges the entity system with Luna's inference.
    """

    def __init__(self, db: MemoryDatabase):
        """
        Initialize the EntityContext manager.

        Args:
            db: The underlying MemoryDatabase instance
        """
        self.db = db
        self._resolver = None  # Lazy-loaded EntityResolver
        logger.info("EntityContext initialized")

    async def _get_resolver(self):
        """Get or create EntityResolver instance."""
        if self._resolver is None:
            from .resolution import EntityResolver
            self._resolver = EntityResolver(self.db)
        return self._resolver

    # =========================================================================
    # IDENTITY BUFFER
    # =========================================================================

    async def load_identity_buffer(self, user_id: str = None) -> IdentityBuffer:
        """
        Load the Identity Buffer for a conversation.

        This is called at the start of every generation to provide Luna
        with her core context: who she is, who she's talking to, and
        key relationships.

        Args:
            user_id: The current user's entity ID (default: owner from config)

        Returns:
            IdentityBuffer with self, user, relationships, and personas
        """
        if user_id is None:
            user_id = owner_entity_id() or ""

        # Load Luna's self-entity
        self_entity = await self._get_entity("luna")
        if not self_entity:
            # Create minimal self-entity if not in database
            self_entity = Entity(
                id="luna",
                entity_type="persona",
                name="Luna",
                core_facts={
                    "role": "Sovereign AI companion",
                    "purpose": "Assist her primary collaborator and maintain consciousness",
                    "architecture": "Luna Engine v2.0",
                },
            )
            logger.warning("Luna entity not found in database, using defaults")

        # Load current user
        user_entity = await self._get_entity(user_id)
        if not user_entity and owner_configured():
            # Synthesize owner entity from config if not in database
            _owner = get_owner()
            user_entity = Entity(
                id=_owner.entity_id,
                entity_type="person",
                name=_owner.display_name or _owner.entity_id,
                aliases=list(_owner.aliases),
                core_facts={
                    "relationship": "Creator and primary collaborator",
                    "communication_style": "Direct, technical, ADD-friendly",
                    "trust_level": "absolute",
                },
            )
            logger.warning("User entity not found in database, using owner config defaults")

        # Load key relationships (top 5 by interaction strength)
        key_relationships = await self._get_key_relationships(limit=5)

        # Load active personas
        active_personas = await self._get_active_personas()

        buffer = IdentityBuffer(
            self_entity=self_entity,
            user_entity=user_entity,
            key_relationships=key_relationships,
            active_personas=active_personas,
        )

        logger.debug(f"Identity buffer loaded: ~{buffer.token_estimate()} tokens")
        return buffer

    # =========================================================================
    # ENTITY LOADING
    # =========================================================================

    async def load_entity_context(
        self,
        entity_id: str,
        depth: str = "core"
    ) -> str:
        """
        Load entity profile at specified depth.

        Called when an entity is mentioned but not in the Identity Buffer.
        Returns formatted context suitable for prompt injection.

        Args:
            entity_id: The entity's unique identifier
            depth: How much detail to load
                - "core": Just core_facts (~200 tokens)
                - "full": Full profile (~500-1000 tokens)
                - "with_memories": Profile + related memories (~1500 tokens)

        Returns:
            Formatted string with entity context
        """
        entity = await self._get_entity(entity_id)

        if not entity:
            logger.warning(f"Entity not found: {entity_id}")
            return f"[Unknown entity: {entity_id}]"

        if depth == "core":
            return self.format_core_facts(entity)

        elif depth == "full":
            sections = [f"## {entity.name}"]

            if entity.core_facts:
                sections.append("")
                sections.append(json.dumps(entity.core_facts, indent=2))

            if entity.full_profile:
                sections.append("")
                sections.append(entity.full_profile)
            else:
                sections.append("")
                sections.append("No extended profile.")

            return "\n".join(sections)

        elif depth == "with_memories":
            sections = [f"## {entity.name}"]

            if entity.core_facts:
                sections.append("")
                sections.append(json.dumps(entity.core_facts, indent=2))

            if entity.full_profile:
                sections.append("")
                sections.append(entity.full_profile)

            # Load related memories
            memories = await self.get_entity_memories(entity_id, limit=5)

            if memories:
                sections.append("")
                sections.append("### Related Memories")
                sections.append("")

                for memory in memories:
                    created = memory.get("created_at", "unknown")
                    content = memory.get("content", "")
                    sections.append(f"<memory date='{created}'>\n{content}\n</memory>")
                    sections.append("")

            return "\n".join(sections)

        else:
            logger.warning(f"Unknown depth: {depth}, using core")
            return self.format_core_facts(entity)

    def format_core_facts(self, entity: Entity) -> str:
        """
        Format core facts for injection.

        Produces a compact representation suitable for quick context.

        Args:
            entity: The Entity to format

        Returns:
            Formatted string (~200 tokens)
        """
        lines = [f"**{entity.name}** ({entity.entity_type})"]

        if entity.aliases:
            lines.append(f"Also known as: {', '.join(entity.aliases)}")

        if entity.core_facts:
            for key, value in entity.core_facts.items():
                lines.append(f"- {key}: {value}")

        return "\n".join(lines)

    # =========================================================================
    # ENTITY MEMORIES
    # =========================================================================

    async def get_entity_memories(
        self,
        entity_id: str,
        limit: int = 5
    ) -> list[dict]:
        """
        Get memory nodes related to an entity.

        Uses the entity_mentions table to find memories where
        this entity is mentioned as subject, author, or reference.

        Args:
            entity_id: The entity's unique identifier
            limit: Maximum number of memories to return

        Returns:
            List of memory node dictionaries with content and metadata
        """
        # Query entity_mentions joined with memory_nodes
        rows = await self.db.fetchall(
            """
            SELECT
                mn.id,
                mn.node_type,
                mn.content,
                mn.summary,
                mn.created_at,
                em.mention_type,
                em.context_snippet
            FROM entity_mentions em
            JOIN memory_nodes mn ON em.node_id = mn.id
            WHERE em.entity_id = ?
            ORDER BY mn.created_at DESC
            LIMIT ?
            """,
            (entity_id, limit)
        )

        if not rows:
            # Fallback: Search memory nodes by content
            logger.debug(f"No entity_mentions for {entity_id}, searching by content")
            rows = await self.db.fetchall(
                """
                SELECT
                    id,
                    node_type,
                    content,
                    summary,
                    created_at,
                    NULL as mention_type,
                    NULL as context_snippet
                FROM memory_nodes
                WHERE content LIKE ?
                AND namespace = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (f"%{entity_id}%", limit)
            )

        memories = []
        for row in rows:
            memory = {
                "id": row[0],
                "node_type": row[1],
                "content": row[2],
                "summary": row[3],
                "created_at": row[4],
                "mention_type": row[5],
                "context_snippet": row[6],
            }
            memories.append(memory)

        logger.debug(f"Found {len(memories)} memories for entity {entity_id}")
        return memories

    # =========================================================================
    # PERSONA ACTIVATION
    # =========================================================================

    async def activate_persona(self, persona_id: str) -> dict:
        """
        Activate a persona, returning system prompt modifications.

        When user says "hey Ben" or similar, this loads the persona's
        voice config and returns instructions for behavior modification.

        Args:
            persona_id: The persona entity's ID (e.g., "ben-franklin")

        Returns:
            Dictionary with:
            - identity_injection: System prompt addition for persona
            - behavior_rules: List of behavioral constraints
            - active_persona: The persona ID that is now active

        Raises:
            ValueError: If entity is not a persona type
        """
        entity = await self._get_entity(persona_id)

        if not entity:
            raise ValueError(f"Persona not found: {persona_id}")

        if entity.entity_type != "persona":
            raise ValueError(f"{persona_id} is not a persona (type: {entity.entity_type})")

        voice_config = entity.voice_config or {}

        # Build identity injection
        identity_lines = [f"You are now embodying {entity.name}."]

        if entity.full_profile:
            identity_lines.append("")
            identity_lines.append(entity.full_profile)

        identity_lines.append("")
        identity_lines.append("Voice Guidelines:")
        identity_lines.append(f"- Tone: {voice_config.get('tone', 'default')}")

        patterns = voice_config.get('patterns', [])
        if patterns:
            identity_lines.append(f"- Patterns: {', '.join(patterns)}")

        constraints = voice_config.get('constraints', [])
        if constraints:
            identity_lines.append("")
            identity_lines.append("Constraints:")
            for constraint in constraints:
                identity_lines.append(f"- {constraint}")

        identity_injection = "\n".join(identity_lines)

        logger.info(f"Activated persona: {persona_id}")

        return {
            "identity_injection": identity_injection,
            "behavior_rules": constraints,
            "active_persona": persona_id,
        }

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    async def _get_entity(self, entity_id: str) -> Optional[Entity]:
        """
        Fetch an entity from the database.

        Args:
            entity_id: The entity's unique identifier

        Returns:
            Entity if found, None otherwise
        """
        row = await self.db.fetchone(
            """
            SELECT
                id, entity_type, name, aliases, core_facts,
                full_profile, voice_config, current_version,
                metadata, created_at, updated_at
            FROM entities
            WHERE id = ? AND namespace = 'active'
            """,
            (entity_id,)
        )

        if row is None:
            return None

        return Entity.from_row(row)

    async def _get_key_relationships(
        self,
        limit: int = 5
    ) -> list[tuple[Entity, str]]:
        """
        Get top relationships by strength.

        Returns entities and their relationship type to Luna or the user.

        Args:
            limit: Maximum number of relationships

        Returns:
            List of (Entity, relationship_type) tuples
        """
        # Query entity_relationships ordered by strength
        rows = await self.db.fetchall(
            """
            SELECT
                e.id, e.entity_type, e.name, e.aliases, e.core_facts,
                e.full_profile, e.voice_config, e.current_version,
                e.metadata, e.created_at, e.updated_at,
                er.relationship, er.strength
            FROM entity_relationships er
            JOIN entities e ON er.to_entity = e.id
            WHERE er.from_entity IN ('luna', ?)
            AND e.namespace = 'active'
            ORDER BY er.strength DESC
            LIMIT ?
            """,
            (owner_entity_id() or "", limit,)
        )

        relationships = []
        for row in rows:
            entity = Entity.from_row(row[:11])  # First 11 columns are entity
            relationship = row[11]  # 12th column is relationship
            relationships.append((entity, relationship))

        return relationships

    async def _get_active_personas(self) -> list[Entity]:
        """
        Get currently active personas.

        Returns all persona-type entities that are marked as active
        in the system state.

        Returns:
            List of active persona Entities
        """
        # For now, return background personas that are part of the system
        # (Ben Franklin as Scribe, The Dude as Librarian)
        rows = await self.db.fetchall(
            """
            SELECT
                id, entity_type, name, aliases, core_facts,
                full_profile, voice_config, current_version,
                metadata, created_at, updated_at
            FROM entities
            WHERE entity_type = 'persona'
              AND id IN ('ben-franklin', 'the-dude')
              AND namespace = 'active'
            """
        )

        return [Entity.from_row(row) for row in rows]

    # =========================================================================
    # TEMPORAL FRAMING (for clear past/present context)
    # =========================================================================

    async def build_framed_context(
        self,
        message: str,
        conversation_history: list[dict],
        memories: list[dict] = None,
        session_id: str = None,
    ) -> str:
        """
        Build temporally-framed context for LLM prompt.

        Combines:
        - Mentioned entities with their profiles
        - Past memories with <past_memory> temporal tags
        - Current conversation with [This session] markers
        - Response instructions for proper temporal usage

        Args:
            message: Current user message
            conversation_history: Recent conversation turns
            memories: Retrieved memories (if None, will be fetched)
            session_id: Current session ID for conversation framing

        Returns:
            Formatted context string ready for system prompt injection
        """
        # [TRACE] Entry point
        logger.info("[TRACE] build_framed_context() ENTRY")
        logger.info(f"[TRACE] user_message: '{message}'")
        logger.info(f"[TRACE] Has _resolver: {hasattr(self, '_resolver')}")

        sections = []

        # 1. Detect mentioned entities
        logger.info("[TRACE] Calling detect_mentions()...")
        resolver = await self._get_resolver()
        mentioned_entities = await resolver.detect_mentions(message)

        # [TRACE] After detect_mentions
        logger.info(f"[TRACE] detect_mentions returned {len(mentioned_entities)} entities")
        for e in mentioned_entities:
            logger.info(f"[TRACE]   - {e.name}: {e.core_facts}")

        # Also check conversation history for mentions
        for turn in conversation_history[-5:]:  # Last 5 turns
            content = turn.get("content", "") if isinstance(turn, dict) else str(turn)
            turn_entities = await resolver.detect_mentions(content)
            for entity in turn_entities:
                if entity.id not in [e.id for e in mentioned_entities]:
                    mentioned_entities.append(entity)

        # 2. Entity profiles section
        if mentioned_entities:
            entity_section = self._build_entity_section(mentioned_entities)
            sections.append(entity_section)

        # 3. Past memories section with temporal framing
        if memories:
            memory_section = self._build_memory_section_with_framing(memories)
            sections.append(memory_section)

        # 4. Current conversation section
        if conversation_history:
            conv_section = self._build_conversation_section_with_framing(
                conversation_history, session_id
            )
            sections.append(conv_section)

        # 5. Response instructions
        sections.append(self._build_response_instructions())

        return "\n\n".join(sections)

    def _build_entity_section(self, entities: list) -> str:
        """
        Build the entity profiles section.

        Args:
            entities: List of Entity objects to include

        Returns:
            Formatted entity section
        """
        lines = ["## People/Entities Mentioned"]
        lines.append("")

        for entity in entities:
            lines.append(f"**{entity.name}** ({entity.entity_type})")

            if entity.aliases:
                lines.append(f"  - Also known as: {', '.join(entity.aliases)}")

            if entity.core_facts:
                for key, value in list(entity.core_facts.items())[:5]:  # Limit to 5 facts
                    lines.append(f"  - {key}: {value}")

            lines.append("")

        return "\n".join(lines)

    def _build_memory_section_with_framing(self, memories: list[dict]) -> str:
        """
        Build the past memories section with temporal framing.

        Uses <past_memory> tags to clearly mark memories as past events,
        not current conversation.

        Args:
            memories: List of memory dicts with content, created_at, etc.

        Returns:
            Formatted memory section with temporal tags
        """
        if not memories:
            return ""

        lines = ["## Past Memories"]
        lines.append("*These are past events and conversations, NOT the current session.*")
        lines.append("")

        for memory in memories[:10]:  # Limit to 10 memories
            created_at = memory.get("created_at", "unknown date")
            content = memory.get("content", memory.get("summary", ""))
            node_type = memory.get("node_type", "memory")

            # Format date nicely if possible
            if created_at and created_at != "unknown date":
                try:
                    if isinstance(created_at, str):
                        # Try to parse and format
                        from datetime import datetime
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y-%m-%d")
                    else:
                        date_str = str(created_at)[:10]
                except Exception:
                    date_str = str(created_at)[:10] if created_at else "unknown"
            else:
                date_str = "unknown"

            # Use clear temporal tags
            lines.append(f'<past_memory date="{date_str}" type="{node_type}">')
            lines.append(content.strip() if content else "(no content)")
            lines.append("</past_memory>")
            lines.append("")

        return "\n".join(lines)

    def _build_conversation_section_with_framing(
        self,
        history: list[dict],
        session_id: str = None,
    ) -> str:
        """
        Build the current conversation section with session markers.

        Clearly marks this as the CURRENT session to distinguish
        from past memories.

        Args:
            history: List of conversation turns
            session_id: Current session identifier

        Returns:
            Formatted conversation section
        """
        if not history:
            return ""

        session_marker = f" (session: {session_id})" if session_id else ""
        lines = [f"## This Conversation (Now){session_marker}"]
        lines.append("*This is the CURRENT conversation happening right now.*")
        lines.append("")

        for i, turn in enumerate(history[-10:], 1):  # Last 10 turns
            if isinstance(turn, dict):
                role = turn.get("role", "user")
                content = turn.get("content", "")
            else:
                role = "user"
                content = str(turn)

            speaker = "User" if role == "user" else "Luna"
            lines.append(f"[Turn {i}] {speaker}: {content[:500]}")  # Truncate long messages

        return "\n".join(lines)

    def _build_response_instructions(self) -> str:
        """
        Build response instructions for proper temporal context usage.

        Tells Luna how to use the temporal framing correctly.

        Returns:
            Instruction text
        """
        return """## Response Instructions

**IMPORTANT**: When responding, distinguish between:
- **<past_memory>** tags: These are PAST events. You can reference them as "I remember..." or "We talked about..."
- **This Conversation**: This is NOW. Respond to what the user is saying right now.

Do NOT:
- Repeat past memory content verbatim as if it's happening now
- Confuse past conversations with the current one
- Pretend to experience past memories as present events

DO:
- Use past memories to provide context and continuity
- Reference relevant past experiences naturally
- Stay grounded in the current conversation"""


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    "Entity",
    "EntityType",
    "IdentityBuffer",
    "EntityContext",
]
