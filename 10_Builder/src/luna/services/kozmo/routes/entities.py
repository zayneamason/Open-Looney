"""KOZMO Entity Routes — CRUD for project entities + bulk create."""

from typing import Optional, List, Dict, Any, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..types import Entity
from ..project import (
    load_project, load_entity, save_entity_to_project,
    list_entities, delete_entity, load_template,
)
from ..entity import slugify
from . import _get_projects_root, _get_project_paths, _load_project_graph

router = APIRouter(tags=["kozmo-entities"])


class CreateEntityRequest(BaseModel):
    type: str
    name: str
    slug: Optional[str] = None
    status: str = "active"
    data: dict = {}
    tags: list = []
    luna_notes: Optional[str] = None


class UpdateEntityRequest(BaseModel):
    status: Optional[str] = None
    data: Optional[dict] = None
    tags: Optional[list] = None
    luna_notes: Optional[str] = None


class EntityDefinition(BaseModel):
    type: str
    name: str
    data: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None


class BulkEntityRequest(BaseModel):
    entities: List[EntityDefinition]
    on_duplicate: Literal["skip", "overwrite", "fail"] = "skip"


class BulkEntityResponse(BaseModel):
    created: List[Dict[str, str]]
    failed: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]]


@router.get("/projects/{project_slug}/entities")
def api_list_entities(project_slug: str, entity_type: Optional[str] = None):
    """List entities in project, optionally filtered by type."""
    paths = _get_project_paths(project_slug)
    slugs = list_entities(paths, entity_type)
    return {"entities": slugs, "count": len(slugs)}


@router.post("/projects/{project_slug}/entities", status_code=201)
def api_create_entity(project_slug: str, req: CreateEntityRequest):
    """Create a new entity in project."""
    paths = _get_project_paths(project_slug)

    slug = req.slug or slugify(req.name)

    existing = paths.entity_file(req.type, slug)
    if existing.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Entity already exists: {req.type}/{slug}",
        )

    entity = Entity(
        type=req.type,
        name=req.name,
        slug=slug,
        status=req.status,
        data=req.data,
        tags=req.tags,
        luna_notes=req.luna_notes,
    )

    save_entity_to_project(paths, entity)

    return {"type": req.type, "slug": slug, "name": req.name}


@router.get("/projects/{project_slug}/entities/{entity_type}/{entity_slug}")
def api_get_entity(project_slug: str, entity_type: str, entity_slug: str):
    """Get a specific entity."""
    paths = _get_project_paths(project_slug)

    template = load_template(paths, entity_type.rstrip("s"))
    result = load_entity(paths, entity_type, entity_slug, template)

    if result.error:
        raise HTTPException(status_code=404, detail=result.error)

    return {
        "entity": result.entity.model_dump(),
        "warnings": result.warnings,
    }


@router.put("/projects/{project_slug}/entities/{entity_type}/{entity_slug}")
def api_update_entity(
    project_slug: str,
    entity_type: str,
    entity_slug: str,
    req: UpdateEntityRequest,
):
    """Update an existing entity."""
    paths = _get_project_paths(project_slug)

    result = load_entity(paths, entity_type, entity_slug)
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)

    entity = result.entity

    if req.status is not None:
        entity.status = req.status
    if req.data is not None:
        entity.data.update(req.data)
    if req.tags is not None:
        entity.tags = req.tags
    if req.luna_notes is not None:
        entity.luna_notes = req.luna_notes

    save_entity_to_project(paths, entity)

    return {"updated": entity_slug}


@router.delete("/projects/{project_slug}/entities/{entity_type}/{entity_slug}")
def api_delete_entity(project_slug: str, entity_type: str, entity_slug: str):
    """Delete an entity."""
    paths = _get_project_paths(project_slug)
    deleted = delete_entity(paths, entity_type, entity_slug)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Entity not found: {entity_type}/{entity_slug}",
        )
    return {"deleted": entity_slug}


@router.post("/projects/{project_slug}/entities/bulk")
def api_bulk_create_entities(
    project_slug: str,
    req: BulkEntityRequest,
):
    """Bulk create multiple entities. Supports partial success."""
    root = _get_projects_root()
    manifest = load_project(root, project_slug)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_slug}")

    paths = _get_project_paths(project_slug)
    valid_types = manifest.entity_types or ["characters", "locations", "props", "events", "lore"]

    results = {"created": [], "failed": [], "skipped": []}

    for entity_def in req.entities:
        try:
            if entity_def.type not in valid_types:
                results["failed"].append({
                    "entity": entity_def.model_dump(),
                    "error": f"Invalid entity type: {entity_def.type}. Valid types: {valid_types}"
                })
                continue

            slug = slugify(entity_def.name)
            entity_path = paths.root / "entities" / entity_def.type / f"{slug}.yaml"

            if entity_path.exists():
                if req.on_duplicate == "skip":
                    results["skipped"].append({
                        "entity": entity_def.model_dump(),
                        "reason": "Entity already exists"
                    })
                    continue
                elif req.on_duplicate == "fail":
                    results["failed"].append({
                        "entity": entity_def.model_dump(),
                        "error": "Entity already exists"
                    })
                    continue

            entity = Entity(
                slug=slug,
                type=entity_def.type,
                name=entity_def.name,
                status="active",
                data=entity_def.data or {},
                tags=entity_def.tags or [],
                luna_notes=None,
            )

            save_entity_to_project(paths, entity)

            results["created"].append({
                "slug": entity.slug,
                "type": entity.type,
                "name": entity.name
            })

        except Exception as e:
            results["failed"].append({
                "entity": entity_def.model_dump(),
                "error": str(e)
            })

    if results["created"]:
        try:
            graph = _load_project_graph(paths)
            graph.rebuild()
        except Exception:
            pass

    return BulkEntityResponse(**results)
