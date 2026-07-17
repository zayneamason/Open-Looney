"""
KOZMO Type Definitions — Pydantic Models

All models match YAML structure exactly.
These are the data contracts between:
- YAML files on disk (source of truth)
- Project graph DB (derived index)
- API endpoints (JSON serialization)
- Frontend (React state)
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# =============================================================================
# Project Models
# =============================================================================


class ProjectSettings(BaseModel):
    """Project-level defaults for camera, lens, and film stock."""

    default_camera: str = "arri_alexa35"
    default_lens: str = "cooke_s7i"
    default_film_stock: str = "kodak_5219"
    aspect_ratio: str = "21:9"
    media_sync_path: Optional[str] = None


class EdenSettings(BaseModel):
    """Eden integration settings for this project."""

    default_agent_id: Optional[str] = None
    manna_budget: float = 100.0


class ProjectManifest(BaseModel):
    """Project manifest — metadata and settings."""

    name: str
    slug: str
    version: int = 1
    created: datetime
    updated: datetime
    settings: ProjectSettings
    eden: Optional[EdenSettings] = None
    entity_types: List[str] = Field(
        default_factory=lambda: ["characters", "locations", "props", "lore", "factions"]
    )


# =============================================================================
# Entity Models
# =============================================================================


class Relationship(BaseModel):
    """Relationship between entities."""

    entity: str  # Slug of related entity
    type: str  # family, rival, catalyst, thematic_mirror, etc.
    detail: Optional[str] = None


class EntityReferences(BaseModel):
    """Reference materials for entity (images, LoRAs)."""

    images: List[str] = Field(default_factory=list)  # Relative paths
    lora: Optional[str] = None  # LoRA model ID or path


class Entity(BaseModel):
    """Entity — character, location, prop, lore, faction."""

    type: str  # character, location, prop, lore, faction
    name: str
    slug: str  # Filename stem (mordecai, crooked_nail)
    status: str = "active"  # active, deceased, unknown, draft
    aliases: List[str] = Field(default_factory=list)  # Alternative names for highlighting
    data: Dict[str, Any] = Field(default_factory=dict)  # All YAML content beyond core fields
    relationships: List[Relationship] = Field(default_factory=list)
    references: EntityReferences = Field(default_factory=EntityReferences)
    scenes: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    luna_notes: Optional[str] = None

    model_config = ConfigDict(extra="allow")  # Allow dynamic fields from templates


# =============================================================================
# Shot Models
# =============================================================================


class CameraConfig(BaseModel):
    """Camera configuration for a shot."""

    body: str = "arri_alexa35"
    lens: str = "cooke_s7i"
    focal_mm: int = 50
    aperture: float = 2.8
    movement: List[str] = Field(default_factory=lambda: ["static"])  # Max 3
    duration_sec: float = 3.0

    def validate_movement(self) -> None:
        """Validate max 3 camera movements."""
        if len(self.movement) > 3:
            raise ValueError(f"Max 3 camera movements allowed, got {len(self.movement)}")


class PostConfig(BaseModel):
    """Post-processing configuration for a shot."""

    film_stock: str = "kodak_5219"
    color_temp_k: int = 5600
    grain_pct: int = 0
    bloom_pct: int = 0
    halation_pct: int = 0


class HeroFrame(BaseModel):
    """Hero frame metadata for a shot."""

    path: Optional[str] = None
    eden_task_id: Optional[str] = None
    approved: bool = False
    approved_at: Optional[datetime] = None


class ShotConfig(BaseModel):
    """Shot configuration — camera, post, hero frame."""

    id: str  # sh001, sh002
    scene: str  # scene slug
    name: str
    status: str = "idea"  # idea | draft | rendering | hero_approved | approved | locked
    camera: CameraConfig
    post: PostConfig
    hero_frame: Optional[HeroFrame] = None
    prompt: str
    characters_present: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    continuity_notes: List[str] = Field(default_factory=list)


# =============================================================================
# Template Models
# =============================================================================


class TemplateField(BaseModel):
    """Field definition in a template."""

    key: str
    type: str  # string, text, int, float, enum, ref, list, file_list
    required: bool = False
    description: Optional[str] = None
    ref_type: Optional[str] = None  # For ref fields: entity type or "any"
    options: List[str] = Field(default_factory=list)  # For enum fields
    nullable: bool = False


class TemplateSection(BaseModel):
    """Section in a template."""

    name: str
    dynamic: bool = False  # User can add arbitrary keys
    fields: List[TemplateField] = Field(default_factory=list)
    type: Optional[str] = None  # "list" for relationship-style sections
    item_fields: List[TemplateField] = Field(default_factory=list)


class Template(BaseModel):
    """Template definition for an entity type."""

    type: str  # character, location, prop, lore, faction
    version: int = 1
    sections: List[TemplateSection]


# =============================================================================
# Fountain Models
# =============================================================================


class FountainScene(BaseModel):
    """Parsed Fountain scene."""

    header: str
    location: Optional[str] = None
    time_of_day: Optional[str] = None
    characters_present: List[str] = Field(default_factory=list)
    has_dialogue: Dict[str, int] = Field(default_factory=dict)  # character -> line count


class FountainDocument(BaseModel):
    """Parsed Fountain document."""

    scenes: List[FountainScene]
    characters: List[str]
    title_page: Dict[str, str] = Field(default_factory=dict)


# =============================================================================
# Dispatch Models
# =============================================================================


class DispatchRequest(BaseModel):
    """Request to dispatch an agent task."""

    action: str  # generate_image, generate_video, etc.
    project_slug: str
    entity_slug: Optional[str] = None
    shot_id: Optional[str] = None
    prompt: Optional[str] = None
    agent_id: Optional[str] = None


class QueueEntry(BaseModel):
    """Generation queue entry."""

    task_id: str
    eden_task_id: str
    action: str
    status: str  # pending, processing, completed, failed
    project_slug: str
    entity_slug: Optional[str] = None
    shot_id: Optional[str] = None
    result_url: Optional[str] = None
    saved_path: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


# =============================================================================
# Entity Load Result
# =============================================================================


class EntityLoadResult(BaseModel):
    """Result of loading an entity from YAML."""

    entity: Optional[Entity] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# =============================================================================
# SCRIBO Models
# =============================================================================


class StoryLevel(BaseModel):
    """Hierarchy level definition (Act, Chapter, Scene, etc.)."""

    name: str           # "Act", "Chapter", "Scene"
    slug_prefix: str    # "act_", "ch_", "sc_"


class StoryStructure(BaseModel):
    """Project story hierarchy and ordering manifest (_structure.yaml)."""

    levels: List[StoryLevel] = Field(default_factory=list)
    order: Dict[str, Any] = Field(default_factory=dict)  # Nested slug -> children


class ContainerMeta(BaseModel):
    """Metadata for a story container (act, chapter, etc.)."""

    title: str
    slug: str
    level: str              # Which StoryLevel this is
    status: str = "idea"    # idea | draft | revised | polished | locked
    summary: Optional[str] = None
    word_count: int = 0     # Computed from children
    notes: Optional[str] = None


class ScriboFrontmatter(BaseModel):
    """YAML frontmatter parsed from a .scribo file."""

    type: str = "scene"     # scene | chapter | act | note | outline
    container: Optional[str] = None  # Parent container slug
    characters_present: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    time: Optional[str] = None
    status: str = "draft"
    tags: List[str] = Field(default_factory=list)
    audio_file: Optional[str] = None       # "09_Bella.mp3"
    audio_duration: Optional[str] = None   # "15.9s"


class LunaNote(BaseModel):
    """Luna's inline annotation on a scene."""

    type: str               # continuity | tone | thematic | production | character
    text: str
    line_ref: Optional[int] = None  # Optional line number reference
    created: Optional[datetime] = None


class ScriboDocument(BaseModel):
    """A complete SCRIBO document (.scribo file)."""

    slug: str               # Filename stem
    path: str               # Relative path from project root
    frontmatter: ScriboFrontmatter
    body: str               # Raw body content (prose + Fountain mixed)
    word_count: int = 0
    luna_notes: List[LunaNote] = Field(default_factory=list)


class StoryTreeNode(BaseModel):
    """Recursive tree node for API responses."""

    id: str
    title: str
    type: str               # Level name or "scene"
    status: str = "idea"
    word_count: int = 0
    children: List["StoryTreeNode"] = Field(default_factory=list)


StoryTreeNode.model_rebuild()  # Enable self-reference


# =============================================================================
# Phase 7: Overlay Models
# =============================================================================


class TextHighlight(BaseModel):
    """Optional text range within a paragraph."""

    start: int  # Character offset start
    end: int    # Character offset end


class LabAction(BaseModel):
    """Embedded LAB production action within an annotation."""

    type: str  # generate_image | shot_sequence | generate_video
    status: str = "planning"  # planning | queued | generating | review | complete
    prompt: Optional[str] = None
    shots: Optional[List[str]] = None  # For shot_sequence type
    entity: Optional[str] = None  # CODEX entity slug
    assignee: Optional[str] = None  # Agent name


class AgentTask(BaseModel):
    """Embedded agent task within an annotation."""

    agent: str  # luna | maya | chiba | ben
    status: str = "pending"  # pending | processing | complete
    action: str  # generate_reference | continuity_check | etc.
    entity: Optional[str] = None


class Annotation(BaseModel):
    """A single overlay annotation anchored to a paragraph."""

    id: str
    paragraph_id: str  # Anchored to this paragraph
    type: str  # note | comment | continuity | agent | action | luna
    author: str  # "User", "Luna", etc.
    text: str
    highlight: Optional[TextHighlight] = None
    resolved: bool = False
    lab_action: Optional[LabAction] = None
    agent_task: Optional[AgentTask] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class OverlayState(BaseModel):
    """Complete overlay state for a document."""

    document_slug: str
    annotations: List[Annotation] = Field(default_factory=list)


# =============================================================================
# Phase 8: LAB Pipeline Models
# =============================================================================


class AudioTrack(BaseModel):
    """Audio file reference for timeline-driven projects."""

    id: str
    filename: str           # "09_Bella.mp3"
    path: str               # Relative to project assets/audio/
    voice: Optional[str] = None  # Entity slug: "bella"
    start_time: float       # Seconds from project start
    end_time: float         # Seconds
    duration: float         # Seconds
    text: Optional[str] = None  # Transcript/dialogue
    section: Optional[int] = None
    lines: Optional[str] = None
    # SCRIBO cross-reference — links audio clip back to fountain script
    document_slug: Optional[str] = None   # e.g. "sc_09_bella_im_a_file"
    container_slug: Optional[str] = None  # e.g. "sec_2_what_i_actually_am"
    visual_prompt: Optional[str] = None   # [[VISUAL]] tag content from scribo body


class AudioTimeline(BaseModel):
    """Complete audio timeline for a project."""

    total_duration: float = 0.0
    tracks: List[AudioTrack] = Field(default_factory=list)


class CameraRig(BaseModel):
    """Camera configuration for a production brief."""

    body: str = "arri_alexa35"
    lens: str = "cooke_s7i"
    focal: int = 50
    aperture: float = 2.8
    movement: List[str] = Field(default_factory=lambda: ["static"])  # Max 3
    duration: float = 3.0


class BriefPostConfig(BaseModel):
    """Post-processing configuration for a production brief."""

    stock: str = "none"
    color_temp: int = 5600
    grain: int = 0  # 0-30
    bloom: int = 0  # 0-30
    halation: int = 0  # 0-20


class SequenceShot(BaseModel):
    """Individual shot within a sequence brief."""

    id: str
    title: str
    prompt: str
    status: str = "planning"
    camera: CameraRig = Field(default_factory=CameraRig)
    post: BriefPostConfig = Field(default_factory=BriefPostConfig)
    hero_frame: Optional[str] = None  # Path to generated image
    eden_task_id: Optional[str] = None


class ProductionBrief(BaseModel):
    """A production unit in the LAB pipeline."""

    id: str
    type: str  # single | sequence | reference
    status: str = "planning"  # planning | rigging | queued | generating | review | approved | locked
    priority: str = "medium"  # critical | high | medium | low
    title: str
    prompt: str

    # Source context (from SCRIBO)
    source_scene: Optional[str] = None
    source_annotation_id: Optional[str] = None
    source_paragraph: Optional[str] = None

    # Entity context
    characters: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    assignee: Optional[str] = None  # Agent: maya | chiba | luna | ben
    tags: List[str] = Field(default_factory=list)

    # Camera (for single shots)
    camera: Optional[CameraRig] = None
    post: Optional[BriefPostConfig] = None

    # Sequence (for multi-shot briefs)
    shots: Optional[List[SequenceShot]] = None

    # Generation state
    hero_frame: Optional[str] = None
    eden_task_id: Optional[str] = None
    progress: Optional[int] = None  # 0-100

    # Audio sync (pins brief to audio timeline)
    audio_start: Optional[float] = None  # Seconds
    audio_end: Optional[float] = None
    audio_track_id: Optional[str] = None  # Links to AudioTrack.id

    # Timeline binding (Phase 3: Container System integration)
    container_id: Optional[str] = None  # Links to Timeline Container

    # Dependencies
    dependencies: List[str] = Field(default_factory=list)  # Other brief IDs

    # Discussion
    notes: str = ""
    ai_thread: List[Dict[str, Any]] = Field(default_factory=list)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MediaAsset(BaseModel):
    """A generated or imported media asset."""

    id: str
    type: str  # image | video | audio
    filename: str
    path: str  # relative to project root
    source: str  # eden | import | capture
    brief_id: Optional[str] = None
    scene_slug: Optional[str] = None
    audio_track_id: Optional[str] = None
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None
    eden_task_id: Optional[str] = None
    prompt: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    status: str = "generated"  # generated | approved | rejected | archived
    created_at: Optional[datetime] = None
    duration: Optional[float] = None  # Source file duration in seconds

    def to_asset_ref(self):
        """Convert to timeline MediaAssetRef for drag-to-timeline flow."""
        from .timeline_types import MediaAssetRef, MediaType

        type_map = {"image": MediaType.IMAGE, "video": MediaType.VIDEO, "audio": MediaType.AUDIO}
        media_type = type_map.get(self.type, MediaType.GENERATIVE)
        return MediaAssetRef(
            asset_id=self.id,
            path=self.path,
            duration=self.duration or 3.0,  # Default 3s for images
            media_type=media_type,
        )
