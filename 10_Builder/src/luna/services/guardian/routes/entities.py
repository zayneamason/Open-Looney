"""Entity routes — serve entity JSON fixtures."""

import json
from fastapi import APIRouter, HTTPException

from luna.core.paths import local_dir

router = APIRouter()

ENTITIES_DIR = local_dir() / "guardian" / "entities"


@router.get("/entities")
async def list_entities():
    """Get all entities."""
    path = ENTITIES_DIR / "entities_updated.json"
    if not path.exists():
        return {"entities": []}
    with open(path) as f:
        return json.load(f)


@router.get("/entities/relationships")
async def get_relationships():
    """Get all entity relationships."""
    path = ENTITIES_DIR / "relationships_updated.json"
    if not path.exists():
        return {"relationships": []}
    with open(path) as f:
        return json.load(f)


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    """Get a single entity by ID."""
    path = ENTITIES_DIR / "entities_updated.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Entities file missing")
    with open(path) as f:
        data = json.load(f)
        for ent in data.get("entities", []):
            if ent["id"] == entity_id:
                return ent
    raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")
