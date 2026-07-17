"""
Aperture & Library Cognition Tools for Luna Engine
====================================================

MCP-compatible tools for managing Luna's cognitive focus (aperture),
collection lock-in scores, and annotation bridge.

These tools are the agentic interface to the Aperture system:
- aperture_get: Current focus state
- aperture_set: Change aperture preset/angle/tags
- collection_lock_in: Query collection engagement scores
- annotate: Create annotation bridging collection → Memory Matrix
"""

import logging
from typing import Optional

from .registry import Tool

logger = logging.getLogger(__name__)


# =============================================================================
# MODULE-LEVEL STATE (set by engine initialization)
# =============================================================================

_aperture_manager = None
_lock_in_engine = None
_annotation_engine = None


def set_aperture_manager(manager) -> None:
    """Set the global ApertureManager reference."""
    global _aperture_manager
    _aperture_manager = manager
    logger.info("Aperture tools connected to ApertureManager")


def set_lock_in_engine(engine) -> None:
    """Set the global CollectionLockInEngine reference."""
    global _lock_in_engine
    _lock_in_engine = engine
    logger.info("Aperture tools connected to CollectionLockInEngine")


def set_annotation_engine(engine) -> None:
    """Set the global AnnotationEngine reference."""
    global _annotation_engine
    _annotation_engine = engine
    logger.info("Aperture tools connected to AnnotationEngine")


def get_aperture_manager():
    return _aperture_manager


def get_lock_in_engine():
    return _lock_in_engine


def get_annotation_engine():
    return _annotation_engine


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

async def aperture_get() -> dict:
    """
    Get current aperture state.

    Returns:
        Dict with preset, angle, thresholds, focus_tags, and active collections
    """
    if _aperture_manager is None:
        return {
            "error": "Aperture not initialized",
            "preset": "balanced",
            "angle": 55,
        }

    return _aperture_manager.state.to_dict(mode=_aperture_manager.mode.value)


async def aperture_set(
    preset: Optional[str] = None,
    angle: Optional[int] = None,
    focus_tags: Optional[list] = None,
    active_project: Optional[str] = None,
    active_collections: Optional[list] = None,
) -> dict:
    """
    Set aperture state.

    Args:
        preset: Named preset (tunnel, narrow, balanced, wide, open)
        angle: Raw angle 15-95 (preset takes precedence)
        focus_tags: Tags to prioritize in recall
        active_project: Project slug for context
        active_collections: Collection keys to include in inner ring

    Returns:
        Updated aperture state
    """
    if _aperture_manager is None:
        return {"error": "Aperture not initialized"}

    from luna.context.aperture import AperturePreset

    if preset is not None:
        try:
            p = AperturePreset(preset.lower())
            _aperture_manager.set_preset(p)
        except ValueError:
            return {"error": f"Unknown preset: {preset}. Use: tunnel, narrow, balanced, wide, open"}
    elif angle is not None:
        _aperture_manager.set_angle(angle)

    if focus_tags is not None:
        _aperture_manager.set_focus_tags(focus_tags)

    if active_project is not None:
        _aperture_manager.set_active_project(active_project)

    if active_collections is not None:
        _aperture_manager.set_active_collections(active_collections)

    return _aperture_manager.state.to_dict(mode=_aperture_manager.mode.value)


async def collection_lock_in(
    collection: Optional[str] = None,
) -> dict:
    """
    Get collection lock-in scores.

    Args:
        collection: Specific collection key. If None, returns all.

    Returns:
        Lock-in data for one or all collections
    """
    if _lock_in_engine is None:
        return {"error": "Collection lock-in not initialized", "collections": []}

    if collection:
        record = await _lock_in_engine.get_lock_in(collection)
        if record is None:
            return {"error": f"Collection '{collection}' not tracked", "collection": collection}
        return {
            "collection_key": record.collection_key,
            "lock_in": record.lock_in,
            "state": record.state,
            "access_count": record.access_count,
            "annotation_count": record.annotation_count,
            "connected_collections": record.connected_collections,
            "entity_overlap_count": record.entity_overlap_count,
            "last_accessed_at": record.last_accessed_at,
        }
    else:
        records = await _lock_in_engine.get_all()
        return {
            "collections": [
                {
                    "collection_key": r.collection_key,
                    "lock_in": r.lock_in,
                    "state": r.state,
                    "access_count": r.access_count,
                    "annotation_count": r.annotation_count,
                }
                for r in records
            ],
            "count": len(records),
        }


async def annotate(
    collection: str,
    doc_id: str,
    annotation_type: str = "note",
    content: Optional[str] = None,
    chunk_index: Optional[int] = None,
    original_text_preview: str = "",
) -> dict:
    """
    Create an annotation bridging a collection document into Memory Matrix.

    Args:
        collection: Collection key (e.g., "dataroom")
        doc_id: Document ID within the collection
        annotation_type: "bookmark", "note", or "flag"
        content: Luna's note text (required for note type)
        chunk_index: Which chunk within the document
        original_text_preview: First ~200 chars of source chunk

    Returns:
        Dict with annotation_id and matrix_node_id
    """
    if _annotation_engine is None:
        return {"error": "Annotation engine not initialized"}

    from luna.substrate.collection_annotations import AnnotationType

    try:
        atype = AnnotationType(annotation_type.lower())
    except ValueError:
        return {"error": f"Unknown annotation type: {annotation_type}. Use: bookmark, note, flag"}

    if atype == AnnotationType.NOTE and not content:
        return {"error": "Content is required for note annotations"}

    annotation_id = await _annotation_engine.create(
        collection_key=collection,
        doc_id=doc_id,
        annotation_type=atype,
        content=content,
        chunk_index=chunk_index,
        original_text_preview=original_text_preview,
    )

    # Fetch back to get matrix_node_id
    annotation = await _annotation_engine.get(annotation_id)

    return {
        "success": True,
        "annotation_id": annotation_id,
        "matrix_node_id": annotation.matrix_node_id if annotation else None,
        "collection": collection,
        "doc_id": doc_id,
        "annotation_type": annotation_type,
    }


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

aperture_get_tool = Tool(
    name="aperture_get",
    description=(
        "Get Luna's current cognitive focus state (aperture). "
        "Returns preset, angle, thresholds, focus tags, and active collections."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    execute=aperture_get,
    requires_confirmation=False,
    timeout_seconds=5,
)

aperture_set_tool = Tool(
    name="aperture_set",
    description=(
        "Set Luna's cognitive focus (aperture). Use a named preset "
        "(tunnel, narrow, balanced, wide, open) or a raw angle (15-95). "
        "Can also set focus tags and active project/collections."
    ),
    parameters={
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "description": "Named preset: tunnel (15°), narrow (35°), balanced (55°), wide (75°), open (95°)",
                "enum": ["tunnel", "narrow", "balanced", "wide", "open"],
            },
            "angle": {
                "type": "integer",
                "description": "Raw angle 15-95 (preset takes precedence if both given)",
            },
            "focus_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to prioritize in recall (e.g., ['investor', 'community'])",
            },
            "active_project": {
                "type": "string",
                "description": "Project slug for context",
            },
            "active_collections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Collection keys to include in inner ring",
            },
        },
        "required": [],
    },
    execute=aperture_set,
    requires_confirmation=False,
    timeout_seconds=5,
)

collection_lock_in_tool = Tool(
    name="collection_lock_in",
    description=(
        "Get engagement scores for Aibrarian collections. "
        "Shows how actively Luna has been using each collection "
        "(access count, annotations, entity overlap, lock-in state)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "collection": {
                "type": "string",
                "description": "Specific collection key. Omit for all collections.",
            },
        },
        "required": [],
    },
    execute=collection_lock_in,
    requires_confirmation=False,
    timeout_seconds=10,
)

annotate_tool = Tool(
    name="annotate",
    description=(
        "Create an annotation on a collection document, bridging it into Luna's Memory Matrix. "
        "Three types: bookmark (mark for later), note (Luna's interpretation), flag (needs attention). "
        "Each annotation creates a provenance-tagged Memory Matrix node."
    ),
    parameters={
        "type": "object",
        "properties": {
            "collection": {
                "type": "string",
                "description": "Collection key (e.g., 'dataroom')",
            },
            "doc_id": {
                "type": "string",
                "description": "Document ID within the collection",
            },
            "annotation_type": {
                "type": "string",
                "description": "Type of annotation",
                "enum": ["bookmark", "note", "flag"],
                "default": "note",
            },
            "content": {
                "type": "string",
                "description": "Luna's note text (required for 'note' type)",
            },
            "chunk_index": {
                "type": "integer",
                "description": "Which chunk within the document (optional)",
            },
            "original_text_preview": {
                "type": "string",
                "description": "First ~200 chars of source chunk for provenance",
            },
        },
        "required": ["collection", "doc_id"],
    },
    execute=annotate,
    requires_confirmation=False,
    timeout_seconds=15,
)


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

ALL_APERTURE_TOOLS = [
    aperture_get_tool,
    aperture_set_tool,
    collection_lock_in_tool,
    annotate_tool,
]


def register_aperture_tools(registry) -> None:
    """Register all aperture/library cognition tools with a ToolRegistry."""
    for tool in ALL_APERTURE_TOOLS:
        registry.register(tool)
    logger.info(f"Registered {len(ALL_APERTURE_TOOLS)} aperture tools")
