"""KOZMO Project Routes — CRUD for projects and settings."""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..types import ProjectManifest, ProjectSettings, EdenSettings
from ..project import (
    create_project, load_project, save_manifest,
    delete_project, list_projects,
)
from ..entity import slugify
from . import _get_projects_root, _get_project_paths

router = APIRouter(tags=["kozmo-projects"])


class CreateProjectRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    settings: Optional[ProjectSettings] = None
    eden: Optional[EdenSettings] = None


@router.get("/projects")
def api_list_projects():
    """List all KOZMO projects."""
    root = _get_projects_root()
    slugs = list_projects(root)
    projects = []
    for slug in slugs:
        manifest = load_project(root, slug)
        if manifest:
            projects.append({
                "name": manifest.name,
                "slug": manifest.slug,
                "version": manifest.version,
                "entity_types": manifest.entity_types,
            })
    return {"projects": projects}


@router.post("/projects", status_code=201)
def api_create_project(req: CreateProjectRequest):
    """Create a new KOZMO project."""
    root = _get_projects_root()
    try:
        manifest = create_project(
            projects_root=root,
            name=req.name,
            slug=req.slug,
            settings=req.settings,
            eden=req.eden,
        )
        return {
            "name": manifest.name,
            "slug": manifest.slug,
            "created": manifest.created.isoformat(),
        }
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"Project already exists: {req.slug or slugify(req.name)}",
        )


@router.get("/projects/{project_slug}")
def api_get_project(project_slug: str):
    """Get project manifest."""
    root = _get_projects_root()
    manifest = load_project(root, project_slug)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_slug}")
    return manifest.model_dump(mode="json")


@router.delete("/projects/{project_slug}")
def api_delete_project(project_slug: str):
    """Delete a project and all its data."""
    root = _get_projects_root()
    deleted = delete_project(root, project_slug)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_slug}")
    return {"deleted": project_slug}


@router.put("/projects/{project_slug}/settings")
def api_update_settings(project_slug: str, settings: ProjectSettings):
    """Update project settings (camera defaults, media_sync_path, etc.)."""
    root = _get_projects_root()
    manifest = load_project(root, project_slug)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_slug}")

    manifest.settings = settings
    paths = _get_project_paths(project_slug)
    save_manifest(paths, manifest)
    return settings.model_dump()
