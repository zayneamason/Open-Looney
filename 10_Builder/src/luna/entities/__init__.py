"""
Luna Engine Entity System
=========================

First-class entities (people, personas, places, projects) with:
- Version history and temporal queries
- Relationship graph
- Memory node linking
- Context injection

Emergent Personality System:
- PersonalityPatch: Discrete units of identity evolution
- EmergentPrompt: Three-layer personality synthesis (DNA + Experience + Mood)
- ReflectionLoop: Automatic patch generation from conversations

The entity system follows the Scribe/Librarian protocol:
- Scribe (Ben) extracts and writes entity profiles
- Librarian (Dude) organizes and maintains the graph
"""

from .models import (
    Entity,
    EntityType,
    EntityVersion,
    EntityRelationship,
    EntityMention,
    EntityUpdate,
    ChangeType,
    # Personality system
    PersonalityPatch,
    PatchTopic,
    PatchTrigger,
    EmergentPrompt,
    MoodState,
)
from .resolution import EntityResolver
from .context import EntityContext, IdentityBuffer
from .storage import PersonalityPatchManager
from .reflection import ReflectionLoop
from .bootstrap import (
    BOOTSTRAP_PATCHES,
    bootstrap_personality,
    check_bootstrap_needed,
)
from .lifecycle import LifecycleManager

__all__ = [
    # Entity system
    "Entity",
    "EntityType",
    "EntityVersion",
    "EntityRelationship",
    "EntityMention",
    "EntityUpdate",
    "ChangeType",
    "EntityResolver",
    "EntityContext",
    "IdentityBuffer",
    # Personality system
    "PersonalityPatch",
    "PatchTopic",
    "PatchTrigger",
    "EmergentPrompt",
    "MoodState",
    "PersonalityPatchManager",
    "ReflectionLoop",
    # Bootstrap
    "BOOTSTRAP_PATCHES",
    "bootstrap_personality",
    "check_bootstrap_needed",
    # Lifecycle
    "LifecycleManager",
]
