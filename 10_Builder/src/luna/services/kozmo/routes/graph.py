"""KOZMO Graph Routes — Relationship graph queries and rebuild."""

from fastapi import APIRouter, HTTPException

from . import _get_project_paths, _load_project_graph, _load_project_entities
from ..graph import ProjectGraph

router = APIRouter(tags=["kozmo-graph"])


@router.get("/projects/{project_slug}/graph/stats")
def api_graph_stats(project_slug: str):
    """Get project graph statistics."""
    paths = _get_project_paths(project_slug)
    graph = _load_project_graph(paths)
    return graph.stats()


@router.get("/projects/{project_slug}/graph/neighbors/{entity_slug}")
def api_graph_neighbors(project_slug: str, entity_slug: str):
    """Get entity neighbors in graph."""
    paths = _get_project_paths(project_slug)
    graph = _load_project_graph(paths)

    if not graph.has_entity(entity_slug):
        raise HTTPException(status_code=404, detail=f"Entity not in graph: {entity_slug}")

    return {
        "entity": entity_slug,
        "neighbors": graph.neighbors(entity_slug),
        "predecessors": graph.predecessors(entity_slug),
        "relationships": graph.get_relationships_for(entity_slug),
    }


@router.get("/projects/{project_slug}/graph/path/{from_slug}/{to_slug}")
def api_graph_path(project_slug: str, from_slug: str, to_slug: str):
    """Find shortest path between entities."""
    paths = _get_project_paths(project_slug)
    graph = _load_project_graph(paths)

    path = graph.shortest_path(from_slug, to_slug)
    if path is None:
        return {"path": None, "message": "No path found"}

    return {"path": path, "length": len(path) - 1}


@router.post("/projects/{project_slug}/graph/rebuild")
def api_graph_rebuild(project_slug: str):
    """Rebuild project graph from YAML entities."""
    paths = _get_project_paths(project_slug)
    entities = _load_project_entities(paths)

    graph = ProjectGraph()
    graph.rebuild(list(entities.values()))
    graph.save(paths.graph_db)

    return {
        "rebuilt": True,
        "stats": graph.stats(),
    }
