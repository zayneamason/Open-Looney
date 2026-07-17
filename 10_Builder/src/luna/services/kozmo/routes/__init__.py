"""
KOZMO Routes Package

Combines focused sub-routers into a single router for server.py.
"""

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException

from luna.core.paths import user_dir
from ..project import ProjectPaths, load_project, list_projects
from ..entity import slugify, parse_entity_safe
from ..graph import ProjectGraph
from ..scribo import ScriboService

# ── Shared Configuration ─────────────────────────────────────────────────────

# Test override hook — set via monkeypatch in tests. None means "compute from
# current user_dir() at call time", which is the production path.
DEFAULT_PROJECTS_ROOT: Optional[Path] = None


# ── Shared Helpers ────────────────────────────────────────────────────────────

def _get_projects_root() -> Path:
    """Get projects root directory, creating if needed."""
    root = DEFAULT_PROJECTS_ROOT if DEFAULT_PROJECTS_ROOT is not None else (user_dir() / "kozmo_projects")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_project_paths(project_slug: str) -> ProjectPaths:
    """Get paths for a project, raising 404 if not found."""
    root = _get_projects_root()
    project_root = root / project_slug

    if not (project_root / "manifest.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_slug}")

    return ProjectPaths(project_root)


def _load_project_graph(paths: ProjectPaths) -> ProjectGraph:
    """Load or initialize project graph."""
    if paths.graph_db.exists():
        return ProjectGraph(db_path=paths.graph_db)
    return ProjectGraph()


def _get_scribo_service(project_slug: str) -> ScriboService:
    """Get ScriboService for a project."""
    paths = _get_project_paths(project_slug)
    return ScriboService(paths.root)


def _load_project_entities(paths: ProjectPaths) -> dict:
    """Load all entities as slug → Entity dict."""
    entities = {}
    for type_dir in paths.entities.iterdir():
        if type_dir.is_dir():
            for yaml_file in type_dir.glob("*.yaml"):
                result = parse_entity_safe(yaml_file)
                if result.entity:
                    entities[result.entity.slug] = result.entity
    return entities


# ── Combine Sub-Routers ──────────────────────────────────────────────────────

from .projects import router as projects_router
from .entities import router as entities_router
from .graph import router as graph_router
from .fountain import router as fountain_router
from .scribo import router as scribo_router
from .lab import router as lab_router
from .timeline import router as timeline_router

router = APIRouter(prefix="/kozmo", tags=["kozmo"])
router.include_router(projects_router)
router.include_router(entities_router)
router.include_router(graph_router)
router.include_router(fountain_router)
router.include_router(scribo_router)
router.include_router(lab_router)
router.include_router(timeline_router)
