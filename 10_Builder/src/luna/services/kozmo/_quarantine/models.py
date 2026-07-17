"""
KOZMO Data Models

Pydantic models for KOZMO entities, documents, and operations.
Used for API validation and database serialization.
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum


class EntityType(str, Enum):
    """Valid entity types in KOZMO"""
    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"
    EVENT = "event"
    LORE = "lore"


class EntityStatus(str, Enum):
    """Entity lifecycle status"""
    ACTIVE = "active"
    DEAD = "dead"
    ARCHIVED = "archived"
    DRAFT = "draft"


# === Entity Models ===

class EntityProfile(BaseModel):
    """Extended profile data for entities"""
    traits: List[str] = Field(default_factory=list)
    atmosphere: Optional[str] = None
    dialogue_style: Optional[str] = None
    scene_template: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"  # Allow additional fields


class Entity(BaseModel):
    """Core entity model"""
    slug: str
    name: str
    type: EntityType
    color: str = "#60a5fa"
    status: EntityStatus = EntityStatus.ACTIVE
    aliases: List[str] = Field(default_factory=list)  # Alternative names for highlighting
    profile: EntityProfile = Field(default_factory=EntityProfile)
    tags: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)

    # Lifecycle tracking
    first_appearance: Optional[str] = None  # scene slug
    last_appearance: Optional[str] = None   # scene slug

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted: bool = False


class EntityCreate(BaseModel):
    """Request model for creating entities"""
    type: EntityType
    name: str
    color: Optional[str] = "#60a5fa"
    aliases: List[str] = Field(default_factory=list)
    profile: Optional[EntityProfile] = None
    tags: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)


class EntityUpdate(BaseModel):
    """Request model for updating entities"""
    name: Optional[str] = None
    color: Optional[str] = None
    status: Optional[EntityStatus] = None
    aliases: Optional[List[str]] = None
    profile: Optional[EntityProfile] = None
    tags: Optional[List[str]] = None
    data: Optional[Dict[str, Any]] = None
    first_appearance: Optional[str] = None
    last_appearance: Optional[str] = None


class BulkEntityCreate(BaseModel):
    """Request model for bulk entity creation"""
    entities: List[EntityCreate]
    on_duplicate: str = "skip"  # skip | overwrite | rename


# === Document Models (Fountain/Scene) ===

class DocumentFrontmatter(BaseModel):
    """Frontmatter for Fountain documents"""
    characters_present: List[str] = Field(default_factory=list)  # entity slugs
    location: Optional[str] = None  # entity slug
    props: List[str] = Field(default_factory=list)  # entity slugs
    time_of_day: Optional[str] = None
    mood: Optional[str] = None

    class Config:
        extra = "allow"


class Document(BaseModel):
    """Fountain document (scene, act, etc.)"""
    slug: str
    type: str  # scene | act | treatment | outline
    title: str
    body: str = ""
    frontmatter: DocumentFrontmatter = Field(default_factory=DocumentFrontmatter)

    scene_number: Optional[int] = None
    parent_slug: Optional[str] = None  # container slug
    order: int = 0

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted: bool = False


class DocumentCreate(BaseModel):
    """Request model for creating documents"""
    type: str
    title: str
    body: str = ""
    frontmatter: Optional[DocumentFrontmatter] = None
    parent_slug: Optional[str] = None


class DocumentUpdate(BaseModel):
    """Request model for updating documents"""
    title: Optional[str] = None
    body: Optional[str] = None
    frontmatter: Optional[DocumentFrontmatter] = None
    order: Optional[int] = None


# === Reverse Index Models (Phase 5) ===

class SceneReference(BaseModel):
    """Single reference to an entity in a scene"""
    scene_slug: str
    scene_title: str
    scene_number: int
    reference_type: str  # frontmatter | body_mention | implicit
    field: Optional[str] = None  # Which frontmatter field
    context: Optional[str] = None  # Surrounding text for mentions


class EntityUsageRecord(BaseModel):
    """Complete usage record for one entity"""
    entity_slug: str
    entity_name: str
    entity_type: str

    scenes: List[SceneReference] = Field(default_factory=list)

    # Statistics
    total_scenes: int = 0
    mention_count: int = 0  # Total body mentions across all scenes
    first_appearance: Optional[str] = None
    last_appearance: Optional[str] = None
    appearance_frequency: Dict[int, int] = Field(default_factory=dict)

    last_updated: datetime = Field(default_factory=datetime.utcnow)


class ReverseIndex(BaseModel):
    """Complete reverse index for a project"""
    project_slug: str

    entity_usage: Dict[str, EntityUsageRecord] = Field(default_factory=dict)
    scene_entities: Dict[str, List[str]] = Field(default_factory=dict)

    total_entities: int = 0
    total_scenes: int = 0
    last_rebuilt: datetime = Field(default_factory=datetime.utcnow)


# === WebSocket Models (Phase 4) ===

class WSMessage(BaseModel):
    """WebSocket message wrapper"""
    type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EntityUpdateMessage(BaseModel):
    """Entity update notification"""
    type: str = "entity_updated"
    entity_slug: str
    entity: Entity
    old_entity: Optional[Entity] = None
    affected_scenes: List[str] = Field(default_factory=list)
    changes: List[str] = Field(default_factory=list)


# === Scene Generation Models (Phase 6) ===

class ShotConfig(BaseModel):
    """Camera/shot configuration for prompt building"""
    camera_body: Optional[str] = None
    lens: Optional[str] = None
    film_stock: Optional[str] = None
    movement: Optional[str] = None
    framing: Optional[str] = None


class SceneGenerateRequest(BaseModel):
    """Request model for AI scene generation"""
    character_slugs: List[str]
    location_slug: str
    goal: str
    style: str = "fountain"  # fountain | prose | mixed


class SceneGenerateResponse(BaseModel):
    """Response model for scene generation"""
    frontmatter: DocumentFrontmatter
    body: str
    meta: Dict[str, Any] = Field(default_factory=dict)
