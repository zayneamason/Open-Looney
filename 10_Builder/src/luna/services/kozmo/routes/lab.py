"""KOZMO LAB Routes — Shot CRUD, production briefs, camera rigs, Eden generation, asset serving."""

from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yaml

from ..types import (
    Entity, ShotConfig, CameraConfig, PostConfig,
    ProductionBrief, CameraRig, BriefPostConfig, SequenceShot,
)
from ..project import load_entity, save_entity_to_project
from ..entity import slugify
from ..lab_pipeline import LabPipelineService
from . import _get_project_paths

router = APIRouter(tags=["kozmo-lab"])


# ── Request Models ────────────────────────────────────────────────────────────


class CreateBriefRequest(BaseModel):
    type: str = "single"
    title: str
    prompt: str
    priority: str = "medium"
    characters: List[str] = []
    location: Optional[str] = None
    assignee: Optional[str] = None
    tags: List[str] = []
    source_scene: Optional[str] = None
    source_annotation_id: Optional[str] = None


class UpdateBriefRequest(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    camera: Optional[dict] = None
    post: Optional[dict] = None


class ApplyRigRequest(BaseModel):
    camera: dict
    post: dict


class AddShotRequest(BaseModel):
    title: str
    prompt: str
    camera: Optional[dict] = None
    post: Optional[dict] = None


class ReorderShotsRequest(BaseModel):
    shot_ids: List[str]


class GenerateRefRequest(BaseModel):
    prompt_override: Optional[str] = None
    style: str = "reference art"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_lab_service(project_slug: str) -> LabPipelineService:
    paths = _get_project_paths(project_slug)
    return LabPipelineService(paths.root)


def _build_entity_ref_prompt(entity: Entity, style: str) -> str:
    """Build an Eden-compatible prompt from entity data."""
    parts = [entity.name]

    desc = entity.data.get("description", "")
    appearance = entity.data.get("appearance", "")
    build = entity.data.get("build", "")
    age = entity.data.get("age", "")

    if entity.type in ("character", "characters"):
        if build:
            parts.append(build)
        if age:
            parts.append(f"age {age}")
        if appearance:
            parts.append(appearance)

    parts.append(style)

    if desc:
        snippet = desc[:200].rstrip()
        if len(desc) > 200:
            snippet += "..."
        parts.append(snippet)

    return ", ".join(p for p in parts if p)


# ── Shot CRUD (YAML-based persistence) ───────────────────────────────────────


@router.post("/projects/{project_slug}/shots", tags=["shots"])
def api_create_shot(project_slug: str, shot_data: dict):
    """Create new shot configuration. Stored as YAML in shots/{scene_slug}/."""
    paths = _get_project_paths(project_slug)

    scene_slug = shot_data.get("scene_slug", "default")
    shot_id = shot_data.get("id") or slugify(shot_data.get("name", f"shot_{datetime.now().strftime('%H%M%S')}"))

    shot_dir = paths.shots / scene_slug
    shot_dir.mkdir(parents=True, exist_ok=True)

    shot_file = shot_dir / f"{shot_id}.yaml"
    if shot_file.exists():
        raise HTTPException(status_code=409, detail=f"Shot already exists: {shot_id}")

    shot_data["id"] = shot_id
    shot_data["created_at"] = datetime.now().isoformat()
    shot_data["updated_at"] = datetime.now().isoformat()

    with open(shot_file, "w") as f:
        yaml.dump(shot_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return {"id": shot_id, "scene_slug": scene_slug, "created": True}


@router.get("/projects/{project_slug}/shots/{scene_slug}", tags=["shots"])
def api_list_shots(project_slug: str, scene_slug: str):
    """List all shots for a scene."""
    paths = _get_project_paths(project_slug)
    shot_dir = paths.shots / scene_slug

    if not shot_dir.exists():
        return {"scene_slug": scene_slug, "shots": [], "count": 0}

    shots = []
    for yaml_file in shot_dir.glob("*.yaml"):
        with open(yaml_file, "r") as f:
            data = yaml.safe_load(f)
        if data:
            shots.append(data)

    return {"scene_slug": scene_slug, "shots": shots, "count": len(shots)}


@router.get("/projects/{project_slug}/shots/{scene_slug}/{shot_id}", tags=["shots"])
def api_get_shot(project_slug: str, scene_slug: str, shot_id: str):
    """Get specific shot configuration."""
    paths = _get_project_paths(project_slug)
    shot_file = paths.shots / scene_slug / f"{shot_id}.yaml"

    if not shot_file.exists():
        raise HTTPException(status_code=404, detail=f"Shot not found: {shot_id}")

    with open(shot_file, "r") as f:
        data = yaml.safe_load(f)

    return data


@router.put("/projects/{project_slug}/shots/{scene_slug}/{shot_id}", tags=["shots"])
def api_update_shot(project_slug: str, scene_slug: str, shot_id: str, updates: dict):
    """Update shot configuration."""
    paths = _get_project_paths(project_slug)
    shot_file = paths.shots / scene_slug / f"{shot_id}.yaml"

    if not shot_file.exists():
        raise HTTPException(status_code=404, detail=f"Shot not found: {shot_id}")

    with open(shot_file, "r") as f:
        data = yaml.safe_load(f) or {}

    data.update(updates)
    data["updated_at"] = datetime.now().isoformat()

    with open(shot_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return {"updated": shot_id}


@router.delete("/projects/{project_slug}/shots/{scene_slug}/{shot_id}", tags=["shots"])
def api_delete_shot(project_slug: str, scene_slug: str, shot_id: str):
    """Delete shot configuration."""
    paths = _get_project_paths(project_slug)
    shot_file = paths.shots / scene_slug / f"{shot_id}.yaml"

    if not shot_file.exists():
        raise HTTPException(status_code=404, detail=f"Shot not found: {shot_id}")

    shot_file.unlink()

    shot_dir = paths.shots / scene_slug
    if shot_dir.exists() and not any(shot_dir.iterdir()):
        shot_dir.rmdir()

    return {"deleted": shot_id}


@router.post("/projects/{project_slug}/shots/{scene_slug}/{shot_id}/generate", tags=["shots"])
def api_generate_shot(project_slug: str, scene_slug: str, shot_id: str):
    """Trigger Eden generation for shot (requires Eden integration)."""
    paths = _get_project_paths(project_slug)
    shot_file = paths.shots / scene_slug / f"{shot_id}.yaml"

    if not shot_file.exists():
        raise HTTPException(status_code=404, detail=f"Shot not found: {shot_id}")

    with open(shot_file, "r") as f:
        shot_data = yaml.safe_load(f)

    return {
        "status": "queued",
        "shot_id": shot_id,
        "scene_slug": scene_slug,
        "message": "Shot generation queued. Eden API integration required for actual generation.",
    }


# ── Entity Reference Image Generation ────────────────────────────────────────


@router.post(
    "/projects/{project_slug}/entities/{entity_type}/{entity_slug}/generate-ref",
    tags=["eden"],
)
async def api_generate_entity_ref(
    project_slug: str,
    entity_type: str,
    entity_slug: str,
    req: GenerateRefRequest = GenerateRefRequest(),
):
    """Generate a reference image for an entity via Eden API."""
    import httpx
    from datetime import datetime as dt
    from ...eden.adapter import EdenAdapter
    from ...eden.config import EdenConfig

    paths = _get_project_paths(project_slug)

    result = load_entity(paths, entity_type, entity_slug)
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)
    entity = result.entity

    prompt = req.prompt_override or _build_entity_ref_prompt(entity, req.style)

    config = EdenConfig.load()
    if not config.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Eden API key not configured. Set EDEN_API_KEY environment variable.",
        )

    try:
        async with EdenAdapter(config) as eden:
            task = await eden.create_image(prompt, wait=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Eden generation failed: {e}")

    if task.is_failed:
        raise HTTPException(status_code=502, detail=f"Eden task failed: {task.error}")

    image_url = task.first_output_url
    if not image_url:
        raise HTTPException(status_code=502, detail="Eden returned no output URL")

    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{entity_slug}_{timestamp}.png"
    ref_dir = paths.assets / "refs" / entity_type
    ref_dir.mkdir(parents=True, exist_ok=True)
    local_path = ref_dir / filename

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(image_url)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to download image: {e}")

    entity.references.images.append(filename)
    save_entity_to_project(paths, entity)

    return {
        "url": image_url,
        "path": f"assets/refs/{entity_type}/{filename}",
        "filename": filename,
        "task_id": task.id,
        "prompt_used": prompt,
    }


# ── Production Brief CRUD ────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/lab/briefs", tags=["lab"])
async def api_list_briefs(
    project_slug: str, status: Optional[str] = None, assignee: Optional[str] = None
):
    """List production briefs with optional filters."""
    lab = _get_lab_service(project_slug)
    briefs = lab.list_briefs(status=status, assignee=assignee)
    return [b.model_dump() for b in briefs]


@router.post("/projects/{project_slug}/lab/briefs", status_code=201, tags=["lab"])
async def api_create_brief(project_slug: str, req: CreateBriefRequest):
    """Create production brief."""
    lab = _get_lab_service(project_slug)
    brief = ProductionBrief(
        id="",
        type=req.type,
        title=req.title,
        prompt=req.prompt,
        priority=req.priority,
        characters=req.characters,
        location=req.location,
        assignee=req.assignee,
        tags=req.tags,
        source_scene=req.source_scene,
        source_annotation_id=req.source_annotation_id,
    )
    created = lab.create_brief(brief)
    return created.model_dump()


@router.get("/projects/{project_slug}/lab/briefs/{brief_id}", tags=["lab"])
async def api_get_brief(project_slug: str, brief_id: str):
    """Get single production brief."""
    lab = _get_lab_service(project_slug)
    brief = lab.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return brief.model_dump()


@router.put("/projects/{project_slug}/lab/briefs/{brief_id}", tags=["lab"])
async def api_update_brief(project_slug: str, brief_id: str, req: UpdateBriefRequest):
    """Update production brief."""
    lab = _get_lab_service(project_slug)
    updates = req.model_dump(exclude_none=True)
    updated = lab.update_brief(brief_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return updated.model_dump()


@router.delete("/projects/{project_slug}/lab/briefs/{brief_id}", tags=["lab"])
async def api_delete_brief(project_slug: str, brief_id: str):
    """Delete production brief."""
    lab = _get_lab_service(project_slug)
    deleted = lab.delete_brief(brief_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return {"deleted": True}


@router.put("/projects/{project_slug}/lab/briefs/{brief_id}/rig", tags=["lab"])
async def api_apply_rig(project_slug: str, brief_id: str, req: ApplyRigRequest):
    """Apply camera rig + post to brief."""
    lab = _get_lab_service(project_slug)
    camera = CameraRig(**req.camera)
    post = BriefPostConfig(**req.post)
    updated = lab.apply_camera_rig(brief_id, camera, post)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return updated.model_dump()


@router.get("/projects/{project_slug}/lab/briefs/{brief_id}/prompt", tags=["lab"])
async def api_preview_prompt(project_slug: str, brief_id: str, shot_id: Optional[str] = None):
    """Preview enriched prompt."""
    lab = _get_lab_service(project_slug)
    prompt = lab.build_brief_prompt(brief_id, shot_id=shot_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return {"enriched_prompt": prompt}


@router.put(
    "/projects/{project_slug}/lab/briefs/{brief_id}/shots/{shot_id}/rig",
    tags=["lab"],
)
async def api_apply_shot_rig(
    project_slug: str, brief_id: str, shot_id: str, req: ApplyRigRequest
):
    """Apply rig to individual shot in sequence."""
    lab = _get_lab_service(project_slug)
    camera = CameraRig(**req.camera)
    post = BriefPostConfig(**req.post)
    shot = lab.apply_rig_to_shot(brief_id, shot_id, camera, post)
    if shot is None:
        raise HTTPException(status_code=404, detail="Brief or shot not found")
    return shot.model_dump()


@router.post(
    "/projects/{project_slug}/lab/briefs/{brief_id}/shots",
    status_code=201,
    tags=["lab"],
)
async def api_add_shot(project_slug: str, brief_id: str, req: AddShotRequest):
    """Add shot to sequence."""
    import uuid

    lab = _get_lab_service(project_slug)
    shot = SequenceShot(
        id=f"{brief_id}_{uuid.uuid4().hex[:4]}",
        title=req.title,
        prompt=req.prompt,
        camera=CameraRig(**(req.camera or {})),
        post=BriefPostConfig(**(req.post or {})),
    )
    updated = lab.add_shot(brief_id, shot)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return shot.model_dump()


@router.delete(
    "/projects/{project_slug}/lab/briefs/{brief_id}/shots/{shot_id}",
    tags=["lab"],
)
async def api_remove_shot(project_slug: str, brief_id: str, shot_id: str):
    """Remove shot from sequence."""
    lab = _get_lab_service(project_slug)
    updated = lab.remove_shot(brief_id, shot_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Brief or shot not found")
    return {"deleted": True}


@router.put(
    "/projects/{project_slug}/lab/briefs/{brief_id}/shots/reorder",
    tags=["lab"],
)
async def api_reorder_shots(project_slug: str, brief_id: str, req: ReorderShotsRequest):
    """Reorder shots in sequence."""
    lab = _get_lab_service(project_slug)
    updated = lab.reorder_shots(brief_id, req.shot_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")
    return updated.model_dump()


# ── Eden Generation (Brief → Eden) ───────────────────────────────────────────


@router.post(
    "/projects/{project_slug}/lab/briefs/{brief_id}/generate",
    tags=["lab", "eden"],
)
async def api_generate_brief(project_slug: str, brief_id: str):
    """Dispatch brief to Eden for image generation."""
    from ...eden.adapter import EdenAdapter
    from ...eden.config import EdenConfig

    lab = _get_lab_service(project_slug)
    brief = lab.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")

    config = EdenConfig.load()
    if not config.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Eden API key not configured. Set EDEN_API_KEY environment variable.",
        )

    try:
        async with EdenAdapter(config) as eden:
            task_id = await lab.dispatch_to_eden(brief_id, eden)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Eden dispatch failed: {e}")

    if task_id is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")

    return {"task_id": task_id, "brief_id": brief_id, "status": "generating"}


@router.get(
    "/projects/{project_slug}/lab/briefs/{brief_id}/generation-status",
    tags=["lab", "eden"],
)
async def api_generation_status(project_slug: str, brief_id: str):
    """Poll Eden generation status for a brief."""
    from ...eden.adapter import EdenAdapter
    from ...eden.config import EdenConfig

    lab = _get_lab_service(project_slug)
    brief = lab.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")

    if not brief.eden_task_id:
        return {"brief_id": brief_id, "status": "no_task", "message": "No Eden task in progress"}

    config = EdenConfig.load()
    if not config.is_configured:
        raise HTTPException(status_code=503, detail="Eden not configured")

    try:
        async with EdenAdapter(config) as eden:
            result = await lab.poll_eden_status(brief_id, eden)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Eden poll failed: {e}")

    if result is None:
        raise HTTPException(status_code=404, detail=f"Brief not found: {brief_id}")

    return result


# ── Static Asset Serving ──────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/assets/{file_path:path}", tags=["assets"])
async def api_serve_asset(project_slug: str, file_path: str):
    """Serve project asset files (hero frames, media, etc.)."""
    paths = _get_project_paths(project_slug)
    asset = (paths.root / file_path).resolve()

    # Security: prevent path traversal outside project root
    if not str(asset).startswith(str(paths.root.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal denied")

    if not asset.is_file():
        raise HTTPException(status_code=404, detail=f"Asset not found: {file_path}")

    return FileResponse(asset)
