"""Knowledge node routes — serve knowledge JSON fixtures."""

import json
from fastapi import APIRouter, HTTPException

from luna.core.paths import local_dir

router = APIRouter()

KNOWLEDGE_DIR = local_dir() / "guardian" / "knowledge_nodes"

# Node type → filename mapping
NODE_FILES = {
    "facts": "facts.json",
    "insights": "insights.json",
    "decisions": "decisions.json",
    "actions": "actions.json",
    "milestones": "milestones.json",
}


@router.get("/knowledge")
async def list_knowledge():
    """Get all knowledge nodes (merged from all type files)."""
    all_nodes = {}
    for node_type, filename in NODE_FILES.items():
        path = KNOWLEDGE_DIR / filename
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                for node in data.get("nodes", []):
                    all_nodes[node["id"]] = node
    return {"nodes": list(all_nodes.values()), "count": len(all_nodes)}


@router.get("/knowledge/{node_id}")
async def get_knowledge_node(node_id: str):
    """Get a single knowledge node by ID."""
    for filename in NODE_FILES.values():
        path = KNOWLEDGE_DIR / filename
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                for node in data.get("nodes", []):
                    if node["id"] == node_id:
                        return node
    raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
