"""
KOZMO Timeline Routes — Container System API
=============================================
Phase 2: REST endpoints + WebSocket event broadcast.

Endpoints:
  GET    /projects/{slug}/timeline           — Load current state
  POST   /projects/{slug}/timeline/containers — Create container
  POST   /projects/{slug}/timeline/razor      — Razor at time
  POST   /projects/{slug}/timeline/split      — Split clip out
  POST   /projects/{slug}/timeline/merge      — Merge containers
  POST   /projects/{slug}/timeline/group      — Group containers
  DELETE /projects/{slug}/timeline/group/{id} — Ungroup
  WS     /ws/kozmo/{slug}/timeline            — Event stream
"""

import asyncio
import logging
from typing import List, Optional, Set

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..timeline_types import MediaAssetRef, MediaType
from ..timeline_service import TimelineEvent, TimelineService, EventSink
from ..timeline_store import load_timeline, save_timeline
from . import _get_project_paths

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kozmo-timeline"])


# ── WebSocket Event Broadcasting ──────────────────────────────────────────────

# Per-project WebSocket connections: {project_slug: set(WebSocket)}
_timeline_connections: dict[str, Set[WebSocket]] = {}


async def _broadcast_events(project_slug: str, events: list[TimelineEvent]) -> None:
    """Broadcast timeline events to all connected clients for a project."""
    clients = _timeline_connections.get(project_slug, set())
    if not clients:
        return

    messages = [
        {
            "type": e.type.value,
            "container_ids": e.container_ids,
            "group_id": e.group_id,
            "detail": e.detail,
        }
        for e in events
    ]

    disconnected = set()
    for ws in clients:
        try:
            await ws.send_json({"events": messages})
        except Exception:
            disconnected.add(ws)

    clients.difference_update(disconnected)


class WebSocketEventSink:
    """Collects events during an operation, then broadcasts after persist."""

    def __init__(self):
        self.events: list[TimelineEvent] = []

    def emit(self, event: TimelineEvent) -> None:
        self.events.append(event)


# ── Request Models ────────────────────────────────────────────────────────────


class CreateContainerRequest(BaseModel):
    asset_id: str
    path: str
    duration: float
    media_type: str = "video"
    track_id: str
    position: float
    label: str = ""


class RazorRequest(BaseModel):
    container_id: str
    cut_time: float


class SplitClipRequest(BaseModel):
    container_id: str
    clip_id: str


class MergeRequest(BaseModel):
    target_id: str
    source_id: str
    confirmed: bool = False


class GroupRequest(BaseModel):
    container_ids: List[str]
    label: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_project_timeline(project_slug: str):
    """Load timeline + project paths, raising 404 if project missing."""
    paths = _get_project_paths(project_slug)
    tl = load_timeline(paths.root)
    return paths, tl


def _make_service():
    """Create service with collecting sink."""
    sink = WebSocketEventSink()
    svc = TimelineService(sink=sink)
    return svc, sink


def _handle_result(result, paths, tl, sink, project_slug: str):
    """Persist on success, broadcast events, return response or raise."""
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)

    save_timeline(paths.root, tl)

    # Schedule broadcast (non-blocking)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast_events(project_slug, sink.events))
    except RuntimeError:
        pass  # No running loop (sync context / tests)

    response = {"ok": True}
    if result.container_ids:
        response["container_ids"] = result.container_ids
    if result.group_id:
        response["group_id"] = result.group_id
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/timeline", tags=["timeline"])
def api_get_timeline(project_slug: str):
    """Load current timeline state for a project."""
    paths = _get_project_paths(project_slug)
    tl = load_timeline(paths.root)
    return tl.model_dump(mode="json")


@router.post("/projects/{project_slug}/timeline/containers", status_code=201, tags=["timeline"])
async def api_create_container(project_slug: str, req: CreateContainerRequest):
    """Create a new container from an asset reference."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    media_type = MediaType(req.media_type)
    asset_ref = MediaAssetRef(
        asset_id=req.asset_id,
        path=req.path,
        duration=req.duration,
        media_type=media_type,
    )

    result = svc.create_container(tl, asset_ref, req.track_id, req.position, req.label)
    return _handle_result(result, paths, tl, sink, project_slug)


@router.post("/projects/{project_slug}/timeline/razor", tags=["timeline"])
async def api_razor(project_slug: str, req: RazorRequest):
    """Razor (cut) a container at an absolute timeline time."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    result = svc.razor(tl, req.container_id, req.cut_time)
    return _handle_result(result, paths, tl, sink, project_slug)


@router.post("/projects/{project_slug}/timeline/split", tags=["timeline"])
async def api_split_clip(project_slug: str, req: SplitClipRequest):
    """Extract a clip from a multi-clip container into its own container."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    result = svc.split_clip_out(tl, req.container_id, req.clip_id)
    return _handle_result(result, paths, tl, sink, project_slug)


@router.post("/projects/{project_slug}/timeline/merge", tags=["timeline"])
async def api_merge(project_slug: str, req: MergeRequest):
    """Merge source container into target. Target chain wins. Requires confirmed=true."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    result = svc.merge(tl, req.target_id, req.source_id, confirmed=req.confirmed)
    return _handle_result(result, paths, tl, sink, project_slug)


@router.post("/projects/{project_slug}/timeline/group", tags=["timeline"])
async def api_group(project_slug: str, req: GroupRequest):
    """Group containers together (move as unit, keep independent effects)."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    result = svc.group(tl, req.container_ids, req.label)
    return _handle_result(result, paths, tl, sink, project_slug)


@router.delete("/projects/{project_slug}/timeline/group/{group_id}", tags=["timeline"])
async def api_ungroup(project_slug: str, group_id: str):
    """Dissolve a container group."""
    paths, tl = _load_project_timeline(project_slug)
    svc, sink = _make_service()

    result = svc.ungroup(tl, group_id)
    return _handle_result(result, paths, tl, sink, project_slug)


# ── WebSocket ─────────────────────────────────────────────────────────────────


@router.websocket("/ws/kozmo/{project_slug}/timeline")
async def timeline_websocket(websocket: WebSocket, project_slug: str):
    """
    WebSocket for real-time timeline event streaming.

    On connect: sends current timeline state.
    Ongoing: receives timeline events as they happen.
    """
    await websocket.accept()

    if project_slug not in _timeline_connections:
        _timeline_connections[project_slug] = set()
    _timeline_connections[project_slug].add(websocket)

    logger.info(
        f"Timeline WS connected for {project_slug}. "
        f"Total: {len(_timeline_connections[project_slug])}"
    )

    try:
        # Send current state on connect
        paths = _get_project_paths(project_slug)
        tl = load_timeline(paths.root)
        await websocket.send_json({
            "type": "timeline.state",
            "timeline": tl.model_dump(mode="json"),
        })

        # Keep alive — client can send pings or commands
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _timeline_connections.get(project_slug, set()).discard(websocket)
        logger.info(
            f"Timeline WS disconnected for {project_slug}. "
            f"Total: {len(_timeline_connections.get(project_slug, set()))}"
        )
