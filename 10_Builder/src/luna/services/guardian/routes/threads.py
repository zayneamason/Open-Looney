"""Thread routes — serve conversation JSON fixtures."""

import json
from fastapi import APIRouter, HTTPException

from luna.core.paths import local_dir

router = APIRouter()

THREADS_DIR = local_dir() / "guardian" / "conversations"


def _discover_thread_map() -> dict[str, str]:
    """Build thread slug -> filename mapping from directory listing."""
    if not THREADS_DIR.exists():
        return {}
    mapping = {}
    for path in sorted(THREADS_DIR.glob("*_thread.json")):
        # Derive slug from filename: "<name>_thread.json" -> "<name>"
        slug = path.stem.replace("_thread", "")
        mapping[slug] = path.name
    return mapping


@router.get("/threads")
async def list_threads():
    """List available threads."""
    thread_map = _discover_thread_map()
    return {
        "threads": [
            {"id": slug, "name": slug.title()}
            for slug in thread_map.keys()
        ]
    }


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    """Get thread messages by ID."""
    thread_map = _discover_thread_map()
    filename = thread_map.get(thread_id)
    if not filename:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

    path = THREADS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Thread file missing: {filename}")

    with open(path) as f:
        return json.load(f)
