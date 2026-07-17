"""
Observatory HTTP routes — wraps MCP tool functions as FastAPI endpoints.

Mounted at /observatory by server.py. The frontend (observatory/api.js)
calls /observatory/api/* which resolves to these handlers.
"""

import asyncio
import json
import logging
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from luna.core.paths import user_dir, memory_matrix_path
from luna_mcp.observatory.config import RetrievalParams
from luna_mcp.observatory.tools import (
    tool_observatory_entities,
    tool_observatory_graph_dump,
    tool_observatory_maintenance_sweep,
    tool_observatory_quest_board,
    tool_observatory_replay,
    tool_observatory_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observatory", tags=["observatory"])

# ---------------------------------------------------------------------------
# Observatory WebSocket — live event stream for the frontend
# ---------------------------------------------------------------------------

_observatory_websockets: set[WebSocket] = set()


async def broadcast_observatory_event(event_data: dict) -> None:
    """Broadcast an event to all connected Observatory WebSocket clients."""
    if not _observatory_websockets:
        return
    payload = json.dumps(event_data)
    dead: set[WebSocket] = set()
    for ws in _observatory_websockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _observatory_websockets.difference_update(dead)


@router.websocket("/ws/events")
async def observatory_events_ws(websocket: WebSocket):
    """WebSocket endpoint for Observatory live events."""
    await websocket.accept()
    _observatory_websockets.add(websocket)
    logger.info("Observatory WS connected. Total: %d", len(_observatory_websockets))

    # Subscribe to knowledge bus so extraction events flow to Observatory too
    from luna.core.event_bus import event_bus

    async def _forward(ev):
        await broadcast_observatory_event({
            "type": ev.type,
            "data": ev.payload,
            "ts": ev.timestamp,
        })

    event_bus.subscribe("knowledge", _forward)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _observatory_websockets.discard(websocket)
        # Remove the subscriber to avoid leaking callbacks
        if _forward in event_bus._subscribers.get("knowledge", []):
            event_bus._subscribers["knowledge"].remove(_forward)
        logger.info("Observatory WS disconnected. Total: %d", len(_observatory_websockets))


# ---------------------------------------------------------------------------
# DB helpers (mirrors tools.py private helpers)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _readonly_db():
    db = await aiosqlite.connect(f"file:{memory_matrix_path()}?mode=ro", uri=True)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout=15000")
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def _readwrite_db():
    db = await aiosqlite.connect(str(memory_matrix_path()))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout=15000")
    await db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class QuestCompleteBody(BaseModel):
    journal_text: str = ""
    themes: list = []


# ===========================================================================
# Direct MCP wrappers (11 endpoints)
# ===========================================================================

@router.get("/api/stats")
async def stats():
    return await tool_observatory_stats()


@router.get("/api/graph-dump")
async def graph_dump(
    limit: int = Query(500),
    min_lock_in: float = Query(0.0),
):
    return await tool_observatory_graph_dump(limit, min_lock_in)


@router.post("/api/replay")
async def replay(query: str = Form(...)):
    return await tool_observatory_replay(query)


@router.get("/api/entities")
async def entities(
    type: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    limit: int = Query(20),
):
    return await tool_observatory_entities(
        entity_type=type or "", limit=limit,
    )


@router.get("/api/entities/{entity_id}")
async def entity_detail(entity_id: str):
    result = await tool_observatory_entities(entity_id=entity_id)
    if "error" in result:
        return result
    # Fetch version history and quests not covered by the MCP tool
    if not memory_matrix_path().exists():
        result.setdefault("versions", [])
        result.setdefault("quests", [])
        return result
    async with _readonly_db() as db:
        cursor = await db.execute(
            "SELECT version, change_type, change_summary AS summary, created_at "
            "FROM entity_versions WHERE entity_id = ? ORDER BY version DESC",
            (entity_id,),
        )
        result["versions"] = [dict(r) for r in await cursor.fetchall()]
        try:
            cursor = await db.execute(
                "SELECT q.id, q.title, q.subtitle, q.status "
                "FROM quests q "
                "JOIN quest_entities qe ON qe.quest_id = q.id "
                "WHERE qe.entity_id = ? ORDER BY q.created_at DESC",
                (entity_id,),
            )
            result["quests"] = [dict(r) for r in await cursor.fetchall()]
        except Exception:
            result["quests"] = []
    return result


@router.get("/api/quests")
async def quests(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
):
    return await tool_observatory_quest_board(
        action="list",
        status=status or "",
        quest_type=type or "",
        project=project or "",
    )


@router.get("/api/quests/{quest_id}")
async def quest_detail(quest_id: str):
    return await tool_observatory_quest_board(action="list", quest_id=quest_id)


@router.post("/api/quests/{quest_id}/accept")
async def quest_accept(quest_id: str):
    return await tool_observatory_quest_board(action="accept", quest_id=quest_id)


@router.post("/api/quests/{quest_id}/complete")
async def quest_complete(quest_id: str, body: QuestCompleteBody = QuestCompleteBody()):
    return await tool_observatory_quest_board(
        action="complete",
        quest_id=quest_id,
        journal_text=body.journal_text,
    )


@router.post("/api/maintenance-sweep")
async def maintenance_sweep():
    return await tool_observatory_maintenance_sweep()


@router.get("/api/config")
async def config():
    return RetrievalParams.load().to_dict()


# ===========================================================================
# New SQL endpoints (6 + 1 stub)
# ===========================================================================

@router.get("/api/events/recent")
async def events_recent(
    n: int = Query(50),
    type_filter: Optional[str] = Query(None),
):
    if not memory_matrix_path().exists():
        return {"events": []}
    async with _readonly_db() as db:
        q = "SELECT id, node_type, content, lock_in, created_at FROM memory_nodes WHERE namespace = 'active'"
        params: list = []
        if type_filter:
            q += " AND node_type = ?"
            params.append(type_filter)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(n)
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()
    return {"events": [dict(r) for r in rows]}


@router.get("/api/zoom/universe")
async def zoom_universe():
    if not memory_matrix_path().exists():
        return {"clusters": [], "edges": []}
    try:
        async with _readonly_db() as db:
            cursor = await db.execute(
                "SELECT cluster_id, name, summary, lock_in, state, member_count, "
                "avg_node_lock_in FROM clusters ORDER BY lock_in DESC"
            )
            clusters = [dict(r) for r in await cursor.fetchall()]
            cursor = await db.execute(
                "SELECT from_cluster, to_cluster, relationship, strength "
                "FROM cluster_edges"
            )
            edges = [dict(r) for r in await cursor.fetchall()]
        return {"clusters": clusters, "edges": edges}
    except Exception:
        return {"clusters": [], "edges": []}


@router.get("/api/zoom/galaxy")
async def zoom_galaxy(
    cluster_id: str = Query(...),
    limit: int = Query(200),
):
    if not memory_matrix_path().exists():
        return {"nodes": [], "edges": [], "focus_cluster": None}
    try:
        async with _readonly_db() as db:
            # Fetch cluster metadata for the label/breadcrumb
            focus_cluster = None
            try:
                cursor = await db.execute(
                    "SELECT cluster_id, name, summary, lock_in, state, "
                    "member_count, avg_node_lock_in FROM clusters "
                    "WHERE cluster_id = ?",
                    (cluster_id,),
                )
                row = await cursor.fetchone()
                if row:
                    focus_cluster = dict(row)
                    focus_cluster["label"] = focus_cluster.get("name") or cluster_id
            except Exception:
                pass  # clusters table may not exist

            cursor = await db.execute(
                "SELECT mn.id, mn.node_type AS type, mn.content, mn.lock_in "
                "FROM cluster_members cm "
                "JOIN memory_nodes mn ON cm.node_id = mn.id "
                "WHERE cm.cluster_id = ? "
                "ORDER BY mn.lock_in DESC LIMIT ?",
                (cluster_id, limit),
            )
            nodes = [dict(r) for r in await cursor.fetchall()]
            if nodes:
                ids = [n["id"] for n in nodes]
                placeholders = ",".join("?" * len(ids))
                cursor = await db.execute(
                    f"SELECT from_id, to_id, relationship, strength "
                    f"FROM graph_edges "
                    f"WHERE from_id IN ({placeholders}) AND to_id IN ({placeholders})",
                    ids + ids,
                )
                edges = [dict(r) for r in await cursor.fetchall()]
            else:
                edges = []
        return {
            "nodes": nodes,
            "edges": edges,
            "focus_cluster": focus_cluster or {"label": cluster_id},
        }
    except Exception:
        return {"nodes": [], "edges": [], "focus_cluster": {"label": cluster_id}}


@router.get("/api/zoom/solarsystem")
async def zoom_solarsystem(node_id: str = Query(...)):
    if not memory_matrix_path().exists():
        return {"focus_node": None, "edges": [], "neighbors": []}
    async with _readonly_db() as db:
        cursor = await db.execute(
            "SELECT id, node_type, content, lock_in, lock_in_state, scope, "
            "metadata, created_at FROM memory_nodes WHERE id = ?",
            (node_id,),
        )
        node_row = await cursor.fetchone()
        if not node_row:
            return {"focus_node": None, "edges": [], "neighbors": []}
        focus_node = dict(node_row)
        # Normalize type field for frontend
        focus_node["type"] = focus_node.pop("node_type", "OBSERVATION")

        cursor = await db.execute(
            "SELECT ge.from_id, ge.to_id, ge.relationship, ge.strength, "
            "mn.id AS neighbor_id, mn.node_type AS neighbor_type, "
            "mn.content AS neighbor_content, mn.lock_in AS neighbor_lock_in, "
            "mn.lock_in_state AS neighbor_lock_in_state "
            "FROM graph_edges ge "
            "JOIN memory_nodes mn ON "
            "  (CASE WHEN ge.from_id = ? THEN ge.to_id ELSE ge.from_id END) = mn.id "
            "WHERE ge.from_id = ? OR ge.to_id = ?",
            (node_id, node_id, node_id),
        )
        raw_edges = await cursor.fetchall()

        # Build deduplicated neighbors list and clean edge list
        seen_neighbors: dict = {}
        edges = []
        for r in raw_edges:
            edges.append({
                "from_id": r["from_id"],
                "to_id": r["to_id"],
                "relationship": r["relationship"],
                "strength": r["strength"] or 0.5,
            })
            nid = r["neighbor_id"]
            if nid not in seen_neighbors:
                seen_neighbors[nid] = {
                    "id": nid,
                    "type": r["neighbor_type"],
                    "content": (r["neighbor_content"] or "")[:200],
                    "lock_in": r["neighbor_lock_in"] or 0,
                    "lock_in_state": r["neighbor_lock_in_state"] or "drifting",
                }

    return {
        "focus_node": focus_node,
        "neighbors": list(seen_neighbors.values()),
        "edges": edges,
    }


# Active lanes for thread-scoped task summary. Terminal states
# (completed/failed/cancelled/archived) remain reachable via the Tasks view
# but are not counted here — matches the old `open_tasks.length` semantics.
_THREAD_ACTIVE_TASK_LANES = ("inbox", "ready", "in_progress", "blocked", "waiting")


@router.get("/api/threads")
async def threads(
    status: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
):
    if not memory_matrix_path().exists():
        return {"threads": []}
    async with _readonly_db() as db:
        q = (
            "SELECT th.id, th.topic, th.status, th.project_slug, "
            "       th.started_at, th.parked_at, th.resumed_at, th.closed_at, "
            "       th.resume_count, th.metadata_json, "
            "       tt.task_id AS linked_task_id, t.status AS task_status "
            "FROM threads th "
            "LEFT JOIN thread_tasks tt ON tt.thread_id = th.id "
            "LEFT JOIN tasks t ON t.id = tt.task_id "
            "WHERE 1=1"
        )
        params: list = []
        if status:
            q += " AND th.status = ?"
            params.append(status)
        if project:
            q += " AND th.project_slug = ?"
            params.append(project)
        q += " ORDER BY th.started_at DESC, th.id ASC"
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()

    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        tid = r["id"]
        if tid not in by_id:
            meta = _parse_json(r["metadata_json"])
            entities = meta.get("entities") or []
            if not isinstance(entities, list):
                entities = []
            entity_node_ids = meta.get("entity_node_ids") or []
            if not isinstance(entity_node_ids, list):
                entity_node_ids = []
            sections = meta.get("study_context_sections") or []
            if not isinstance(sections, list):
                sections = []
            try:
                turn_count = int(meta.get("turn_count", 0) or 0)
            except (TypeError, ValueError):
                turn_count = 0
            by_id[tid] = {
                "id": tid,
                "topic": r["topic"] or "",
                "status": r["status"],
                "project_slug": r["project_slug"],
                "started_at": r["started_at"],
                "parked_at": r["parked_at"],
                "resumed_at": r["resumed_at"],
                "closed_at": r["closed_at"],
                "resume_count": r["resume_count"] or 0,
                "entities": list(entities),
                "entity_node_ids": list(entity_node_ids),
                "study_context_sections": list(sections),
                "parent_thread_id": meta.get("parent_thread_id"),
                "turn_count": turn_count,
                "task_count": 0,
                "task_summary": {
                    "total": 0,
                    "inbox": 0,
                    "ready": 0,
                    "in_progress": 0,
                    "blocked": 0,
                    "waiting": 0,
                },
            }
            order.append(tid)

        task_status = r["task_status"]
        if task_status in _THREAD_ACTIVE_TASK_LANES:
            bucket = by_id[tid]["task_summary"]
            bucket[task_status] += 1
            bucket["total"] += 1
            by_id[tid]["task_count"] += 1

    results = [by_id[tid] for tid in order[:100]]
    return {"threads": results}


# ---------------------------------------------------------------------------
# Tasks — canonical TaskManager board read model
# ---------------------------------------------------------------------------
#
# Slice 1 of the TaskManager Board UI (see
# Docs/bible/Handoffs/HANDOFF_BUILD_TASKMANAGER_BOARD_UI_SLICE1.md).
# Read-only. Reads `tasks`, `thread_tasks`, `threads`, `task_dependencies`,
# and `task_runs` directly via the same `_readonly_db()` context the other
# Observatory views use. Never mutates.
#
# The `mappings` object is intentionally always fully populated (all nine
# buckets present, empty lists where data is absent) so the frontend can
# render every section unconditionally without branching.

_TASK_STATUSES = (
    "inbox", "ready", "in_progress", "blocked",
    "waiting", "completed", "failed", "cancelled", "archived",
)


def _parse_json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _build_thread_summary(row: aiosqlite.Row) -> Optional[dict]:
    """Build the `thread_summary` block from joined threads columns."""
    if row["th_id"] is None:
        return None
    th_meta = _parse_json(row["th_metadata_json"])
    entities = th_meta.get("entities") or []
    if not isinstance(entities, list):
        entities = []
    return {
        "id": row["th_id"],
        "topic": row["topic"] or "",
        "status": row["th_status"],
        "project_slug": row["project_slug"],
        "entities": list(entities),
    }


def _empty_mapping_tables() -> dict:
    """Default shape returned by _fetch_table_mappings when no rows exist."""
    return {"people": [], "entities": [], "subjects": [], "keywords": [],
            "files": [], "memory": []}


async def _fetch_table_mappings(db, task_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch mapping rows from the five canonical join tables.

    Returns `{task_id: {people, entities, subjects, keywords, files, memory}}`.
    People/entity split uses either `entities.entity_type='person'` (when the
    task_entities row resolves to a person entity) or the `relationship='owned_by'`
    hint written by the metadata backfill.
    """
    if not task_ids:
        return {}
    placeholders = ",".join("?" * len(task_ids))
    result: dict[str, dict] = {tid: _empty_mapping_tables() for tid in task_ids}

    # Entities + person filter via LEFT JOIN on entities.entity_type
    entity_cursor = await db.execute(
        f"""
        SELECT te.task_id, te.entity_id, te.entity_text, te.relationship,
               e.entity_type, e.name
        FROM task_entities te
        LEFT JOIN entities e ON e.id = te.entity_id AND te.entity_id <> ''
        WHERE te.task_id IN ({placeholders})
        """,
        task_ids,
    )
    for row in await entity_cursor.fetchall():
        tid = row["task_id"]
        # '' is the absence sentinel in task_entities — treat as missing.
        label = row["name"] or (row["entity_text"] or None) or (row["entity_id"] or None)
        if not label:
            continue
        if row["entity_type"] == "person" or row["relationship"] == "owned_by":
            result[tid]["people"].append(label)
        else:
            result[tid]["entities"].append(label)

    subj_cursor = await db.execute(
        f"SELECT task_id, subject FROM task_subjects WHERE task_id IN ({placeholders}) "
        f"ORDER BY weight DESC, subject",
        task_ids,
    )
    for row in await subj_cursor.fetchall():
        result[row["task_id"]]["subjects"].append(row["subject"])

    kw_cursor = await db.execute(
        f"SELECT task_id, keyword FROM task_keywords WHERE task_id IN ({placeholders}) "
        f"ORDER BY weight DESC, keyword",
        task_ids,
    )
    for row in await kw_cursor.fetchall():
        result[row["task_id"]]["keywords"].append(row["keyword"])

    file_cursor = await db.execute(
        f"SELECT task_id, path FROM task_files WHERE task_id IN ({placeholders}) "
        f"ORDER BY created_at",
        task_ids,
    )
    for row in await file_cursor.fetchall():
        result[row["task_id"]]["files"].append(row["path"])

    mem_cursor = await db.execute(
        f"SELECT task_id, memory_node_id, relationship FROM task_memory_links "
        f"WHERE task_id IN ({placeholders}) ORDER BY score DESC",
        task_ids,
    )
    for row in await mem_cursor.fetchall():
        result[row["task_id"]]["memory"].append({
            "id": row["memory_node_id"],
            "relationship": row["relationship"],
        })

    return result


def _derive_mappings(
    metadata: dict,
    thread_summary: Optional[dict],
    table_mappings: Optional[dict] = None,
) -> dict:
    """Combine canonical mapping tables with thread/project derivation.

    All nine buckets are always present. Data sources:
    - `people`, `entities`, `subjects`, `keywords`, `files`, `memory` from
      the canonical join tables (via `_fetch_table_mappings`)
    - `threads` from the joined thread_summary
    - `projects` from metadata.project → thread.project_slug fallback
    - `quests` deferred until the quest-narrowing slice — currently reads
      `metadata.quests` only for forward-compat
    """
    m = metadata or {}
    t = table_mappings or _empty_mapping_tables()
    project = m.get("project") or (thread_summary or {}).get("project_slug")

    def _str_list(value) -> list:
        return [x for x in (value or []) if isinstance(x, str)]

    return {
        "people":   list(t["people"]),
        "entities": list(t["entities"]),
        "subjects": list(t["subjects"]),
        "keywords": list(t["keywords"]),
        "threads":  [thread_summary["id"]] if thread_summary else [],
        "projects": [project] if project else [],
        "quests":   _str_list(m.get("quests")),
        "files":    list(t["files"]),
        "memory":   list(t["memory"]),
    }


def _unpack_latest_run(packed: Optional[str]) -> Optional[dict]:
    if not packed:
        return None
    parts = packed.split("|", 2)
    if len(parts) < 3:
        return None
    status, started, completed = parts
    return {
        "status": status,
        "started_at": started or None,
        "completed_at": completed or None,
    }


async def _fetch_dependency_ids(db, task_ids: list[str]) -> dict[str, list[str]]:
    """One query, returns {task_id: [dep_id, ...]}."""
    if not task_ids:
        return {}
    placeholders = ",".join("?" * len(task_ids))
    cursor = await db.execute(
        f"SELECT task_id, depends_on_task_id FROM task_dependencies "
        f"WHERE task_id IN ({placeholders})",
        task_ids,
    )
    out: dict[str, list[str]] = {}
    for row in await cursor.fetchall():
        out.setdefault(row["task_id"], []).append(row["depends_on_task_id"])
    return out


def _row_to_task_dict(
    row: aiosqlite.Row,
    dep_ids: list[str],
    table_mappings: Optional[dict] = None,
) -> dict:
    metadata = _parse_json(row["metadata_json"])
    result = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError):
            result = row["result_json"]
    thread_summary = _build_thread_summary(row)
    owner = row["owner"]
    if not owner and row["source"] == "yaml_bridge_migration":
        # Legacy desktop/code queue tasks predate explicit owner stamping.
        owner = "luna"
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "kind": row["kind"],
        "status": row["status"],
        "priority": row["priority"],
        "owner": owner,
        "source": row["source"],
        "blocked_reason": row["blocked_reason"],
        "result": result,
        "error": row["error"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "due_at": row["due_at"],
        "thread_id": row["tt_thread_id"],
        "thread_summary": thread_summary,
        "mappings": _derive_mappings(metadata, thread_summary, table_mappings),
        "dependencies": list(dep_ids),
        "run_count": int(row["run_count"] or 0),
        "latest_run": _unpack_latest_run(row["latest_run_packed"]),
    }


_TASK_BASE_SELECT = """
SELECT
    t.id, t.title, t.description, t.kind, t.status, t.priority,
    t.owner, t.source, t.blocked_reason, t.result_json, t.error,
    t.metadata_json, t.created_at, t.updated_at, t.started_at,
    t.completed_at, t.due_at,
    tt.thread_id AS tt_thread_id,
    th.id AS th_id, th.topic, th.status AS th_status,
    th.project_slug, th.metadata_json AS th_metadata_json,
    (SELECT COUNT(*) FROM task_runs r WHERE r.task_id = t.id) AS run_count,
    (SELECT status || '|' || COALESCE(started_at,'') || '|' || COALESCE(completed_at,'')
     FROM task_runs r WHERE r.task_id = t.id
     ORDER BY r.started_at DESC LIMIT 1) AS latest_run_packed
FROM tasks t
LEFT JOIN thread_tasks tt ON tt.task_id = t.id
LEFT JOIN threads      th ON th.id       = tt.thread_id
"""


@router.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    thread_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Canonical task board read model.

    Filters map directly to SQL. `project` matches:
    - linked thread project (`threads.project_slug`)
    - explicit task metadata project (`metadata_json.project`)
    - canonical task keywords equal to the slug (legacy/project-tag fallback)
    """
    if not memory_matrix_path().exists():
        return {"tasks": [], "total": 0, "counts_by_status": {}}

    where: list[str] = []
    params: list = []
    count_where: list[str] = []
    count_params: list = []
    if status:
        where.append("t.status = ?")
        params.append(status)
    if kind:
        where.append("t.kind = ?")
        params.append(kind)
        count_where.append("t.kind = ?")
        count_params.append(kind)
    if owner:
        if owner == "luna":
            owner_clause = "(t.owner = ? OR (t.owner IS NULL AND t.source = 'yaml_bridge_migration'))"
        else:
            owner_clause = "t.owner = ?"
        where.append(owner_clause)
        params.append(owner)
        count_where.append(owner_clause)
        count_params.append(owner)
    if thread_id:
        where.append("tt.thread_id = ?")
        params.append(thread_id)
        count_where.append("tt.thread_id = ?")
        count_params.append(thread_id)
    if project:
        project_clause = (
            "("
            "th.project_slug = ? "
            "OR json_extract(t.metadata_json, '$.project') = ? "
            "OR EXISTS ("
            "    SELECT 1 FROM task_keywords tk "
            "    WHERE tk.task_id = t.id AND tk.keyword = ?"
            ")"
            ")"
        )
        where.append(project_clause)
        params.extend([project, project, project])
        count_where.append(project_clause)
        count_params.extend([project, project, project])

    query = _TASK_BASE_SELECT
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY t.priority ASC, t.created_at ASC LIMIT ?"
    params.append(int(limit))

    async with _readonly_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        task_ids = [r["id"] for r in rows]
        dep_map = await _fetch_dependency_ids(db, task_ids)
        mapping_map = await _fetch_table_mappings(db, task_ids)
        counts_query = (
            "SELECT t.status, COUNT(DISTINCT t.id) AS n "
            "FROM tasks t "
            "LEFT JOIN thread_tasks tt ON tt.task_id = t.id "
            "LEFT JOIN threads th ON th.id = tt.thread_id"
        )
        if count_where:
            counts_query += " WHERE " + " AND ".join(count_where)
        counts_query += " GROUP BY t.status"
        counts_cursor = await db.execute(counts_query, count_params)
        counts = {r["status"]: int(r["n"]) for r in await counts_cursor.fetchall()}

    tasks = [
        _row_to_task_dict(r, dep_map.get(r["id"], []), mapping_map.get(r["id"]))
        for r in rows
    ]
    # Ensure every known status appears, even at zero, so the UI can render
    # stable lane counts without branching.
    counts_by_status = {s: counts.get(s, 0) for s in _TASK_STATUSES}

    return {
        "tasks": tasks,
        "total": len(tasks),
        "counts_by_status": counts_by_status,
    }


@router.get("/api/tasks/{task_id}")
async def get_task_detail(task_id: str):
    """Expanded payload for a single task — full run history + resolved deps."""
    if not memory_matrix_path().exists():
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async with _readonly_db() as db:
        cursor = await db.execute(_TASK_BASE_SELECT + " WHERE t.id = ?", [task_id])
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        dep_map = await _fetch_dependency_ids(db, [task_id])
        dep_ids = dep_map.get(task_id, [])
        mapping_map = await _fetch_table_mappings(db, [task_id])

        base = _row_to_task_dict(row, dep_ids, mapping_map.get(task_id))

        # Resolve dependencies to summary dicts
        resolved_deps: list[dict] = []
        if dep_ids:
            placeholders = ",".join("?" * len(dep_ids))
            dep_cursor = await db.execute(
                f"SELECT id, title, status FROM tasks WHERE id IN ({placeholders})",
                dep_ids,
            )
            for dep_row in await dep_cursor.fetchall():
                resolved_deps.append({
                    "id": dep_row["id"],
                    "title": dep_row["title"],
                    "status": dep_row["status"],
                })

        # Full run history
        runs_cursor = await db.execute(
            "SELECT id, run_type, actor, started_at, completed_at, status, result_json, error "
            "FROM task_runs WHERE task_id = ? ORDER BY started_at ASC",
            (task_id,),
        )
        run_history: list[dict] = []
        for rr in await runs_cursor.fetchall():
            run_history.append({
                "id": rr["id"],
                "run_type": rr["run_type"],
                "actor": rr["actor"],
                "started_at": rr["started_at"],
                "completed_at": rr["completed_at"],
                "status": rr["status"],
                "result": _parse_json(rr["result_json"]) or None,
                "error": rr["error"],
            })

    base["dependencies"] = resolved_deps
    base["run_history"] = run_history
    return base


def _parse_journal_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a journal markdown file.

    Returns (metadata_dict, body_text).
    """
    import yaml as _yaml

    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = _yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            body = parts[2].strip()
    return meta, body


def _journal_summary(path: Path) -> dict:
    """Build a list-level summary dict for a journal file."""
    raw = path.read_text()
    meta, body = _parse_journal_frontmatter(raw)
    # Derive slug from filename: 2025-12-28_012_time-pink-floyd.md → time-pink-floyd
    stem = path.stem  # e.g. "2025-12-28_012_time-pink-floyd"
    parts = stem.split("_", 2)
    slug = parts[2] if len(parts) >= 3 else stem
    # Title: frontmatter 'prompt' field, or first H1 in body, or slug
    title = meta.get("prompt") or meta.get("title") or ""
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break
    return {
        "filename": path.name,
        "date": meta.get("date", parts[0] if len(parts) >= 1 else ""),
        "entry": meta.get("entry", int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0),
        "title": title,
        "slug": slug,
        "song": meta.get("symphony") or meta.get("song") or "",
        "resonance": meta.get("resonance", ""),
        "size": path.stat().st_size,
        "modified": path.stat().st_mtime,
    }


@router.get("/api/journals")
async def journals():
    journal_dir = user_dir() / "journal"
    if not journal_dir.exists():
        return {"journals": []}
    files = sorted(
        journal_dir.glob("*.md"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return {
        "journals": [_journal_summary(f) for f in files]
    }


@router.get("/api/journals/{filename}")
async def journal_detail(filename: str):
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = user_dir() / "journal" / filename
    resolved = path.resolve()
    if not str(resolved).startswith(str((user_dir() / "journal").resolve())):
        raise HTTPException(400, "Invalid filename")
    if not path.exists():
        raise HTTPException(404, "Journal not found")
    raw = path.read_text()
    meta, body = _parse_journal_frontmatter(raw)
    stem = path.stem
    parts = stem.split("_", 2)
    slug = parts[2] if len(parts) >= 3 else stem
    title = meta.get("prompt") or meta.get("title") or ""
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break
    return {
        "filename": filename,
        "date": meta.get("date", parts[0] if len(parts) >= 1 else ""),
        "entry": meta.get("entry", int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0),
        "title": title,
        "slug": slug,
        "song": meta.get("symphony") or meta.get("song") or "",
        "resonance": meta.get("resonance", ""),
        "body": body,
    }


@router.post("/api/layout/recompute")
async def layout_recompute():
    return {"ok": True}


# ===========================================================================
# Observatory v0.1 — Search & Node Detail
# ===========================================================================

_OBS_EXCLUDED = (
    "CONVERSATION_TURN", "SESSION", "THREAD", "PARENT", "CATEGORY",
)

_OBS_FULL_COLS = (
    "id, node_type, content, source, confidence, lock_in, lock_in_state, "
    "created_at, updated_at, metadata, custodian, date_shared, scope, review_status"
)


class ObservatorySearchRequest(BaseModel):
    query: str = ""
    method: str = "hybrid"
    node_types: List[str] = []
    sources: List[str] = []
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    lock_in_min: float = 0.0
    lock_in_max: float = 1.0
    entity_ids: List[str] = []
    limit: int = 50
    offset: int = 0
    sort_by: str = "recency"
    show_quarantined: bool = False


def _obs_sort_key(row: dict, sort_by: str):
    if sort_by == "lock_in":
        return row.get("lock_in") or 0.0
    if sort_by == "drift":
        return row.get("updated_at") or ""
    return row.get("created_at") or ""


def _obs_post_filter(rows: list[dict], req: ObservatorySearchRequest) -> list[dict]:
    out = []
    want_unknown = "unknown" in req.sources
    explicit_sources = [s for s in req.sources if s != "unknown"]

    for row in rows:
        if row.get("node_type") in _OBS_EXCLUDED:
            continue
        if req.node_types and row.get("node_type") not in req.node_types:
            continue
        if req.sources:
            src = row.get("source") or ""
            is_unknown = not src
            matches = (want_unknown and is_unknown) or (src in explicit_sources)
            if not matches:
                continue
        if req.date_from and (row.get("created_at") or "") < req.date_from:
            continue
        if req.date_to and (row.get("created_at") or "") > req.date_to:
            continue
        li = row.get("lock_in") or 0.0
        if li < req.lock_in_min or li > req.lock_in_max:
            continue
        if not req.show_quarantined and row.get("review_status") == "quarantined":
            continue
        out.append(row)
    return out


async def _obs_neighbor_counts(db, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    cursor = await db.execute(
        f"SELECT node_id, COUNT(*) AS cnt FROM ("
        f"  SELECT from_id AS node_id FROM graph_edges WHERE from_id IN ({ph})"
        f"  UNION ALL"
        f"  SELECT to_id FROM graph_edges WHERE to_id IN ({ph})"
        f") GROUP BY node_id",
        ids + ids,
    )
    return {r["node_id"]: r["cnt"] for r in await cursor.fetchall()}


async def _obs_unknown_count(db) -> int:
    cursor = await db.execute(
        "SELECT COUNT(*) FROM memory_nodes "
        "WHERE namespace = 'active' AND (source IS NULL OR source = '') "
        f"AND node_type NOT IN ({','.join('?'*len(_OBS_EXCLUDED))})",
        list(_OBS_EXCLUDED),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


@router.post("/api/search")
async def observatory_search(req: ObservatorySearchRequest):
    if not memory_matrix_path().exists():
        return {"results": [], "total_count": 0, "unknown_provenance_count": 0, "elapsed_ms": 0}

    t0 = _time.monotonic()
    note: Optional[str] = None
    method_used = req.method

    async with _readonly_db() as db:
        unknown_count = await _obs_unknown_count(db)

        if req.query.strip():
            # --- Path A: FTS5-ranked search ---
            if req.method == "semantic":
                method_used = "keyword"
                note = "Semantic search requires the engine runtime. Fell back to keyword search."

            # Sanitise query for FTS5: split on whitespace, OR-join top 5 terms
            terms = [t for t in req.query.split() if len(t) > 1][:5]
            fts_query = " OR ".join(terms) if terms else req.query

            fts_rows: list[dict] = []
            try:
                cursor = await db.execute(
                    "SELECT fts.rowid, -rank AS score "
                    "FROM memory_nodes_fts fts "
                    "WHERE memory_nodes_fts MATCH ? "
                    "ORDER BY rank LIMIT 300",
                    (fts_query,),
                )
                fts_hits = await cursor.fetchall()
                if fts_hits:
                    rowids = [r["rowid"] for r in fts_hits]
                    ph = ",".join("?" * len(rowids))
                    cursor = await db.execute(
                        f"SELECT {_OBS_FULL_COLS} FROM memory_nodes WHERE rowid IN ({ph}) AND namespace = 'active'",
                        rowids,
                    )
                    fts_rows = [dict(r) for r in await cursor.fetchall()]
                    score_map = {r["rowid"]: r["score"] for r in fts_hits}
                    # Preserve FTS5 rank order
                    rowid_index = {r["rowid"]: i for i, r in enumerate(fts_hits)}
            except Exception:
                # FTS5 unavailable — fall back to LIKE
                like = f"%{req.query}%"
                ex_ph = ",".join("?" * len(_OBS_EXCLUDED))
                cursor = await db.execute(
                    f"SELECT {_OBS_FULL_COLS} FROM memory_nodes "
                    f"WHERE content LIKE ? AND namespace = 'active' AND node_type NOT IN ({ex_ph}) "
                    f"ORDER BY created_at DESC LIMIT 300",
                    [like] + list(_OBS_EXCLUDED),
                )
                fts_rows = [dict(r) for r in await cursor.fetchall()]
                note = (note or "") + " FTS5 unavailable; used LIKE fallback."

            # Entity filter (subquery join) when entity_ids supplied
            if req.entity_ids and fts_rows:
                ids_in_results = [r["id"] for r in fts_rows]
                ep = ",".join("?" * len(req.entity_ids))
                ip = ",".join("?" * len(ids_in_results))
                cursor = await db.execute(
                    f"SELECT DISTINCT node_id FROM entity_mentions "
                    f"WHERE entity_id IN ({ep}) AND node_id IN ({ip})",
                    req.entity_ids + ids_in_results,
                )
                allowed = {r["node_id"] for r in await cursor.fetchall()}
                fts_rows = [r for r in fts_rows if r["id"] in allowed]

            rows = _obs_post_filter(fts_rows, req)
            rows.sort(key=lambda r: _obs_sort_key(r, req.sort_by), reverse=True)

        else:
            # --- Path B: browse mode (no query) ---
            clauses = ["namespace = 'active'", f"node_type NOT IN ({','.join('?'*len(_OBS_EXCLUDED))})"]
            params: list = list(_OBS_EXCLUDED)

            if req.node_types:
                clauses.append(f"node_type IN ({','.join('?'*len(req.node_types))})")
                params += req.node_types

            if req.sources:
                want_unknown = "unknown" in req.sources
                explicit_sources = [s for s in req.sources if s != "unknown"]
                src_parts = []
                if want_unknown:
                    src_parts.append("(source IS NULL OR source = '')")
                if explicit_sources:
                    src_parts.append(f"source IN ({','.join('?'*len(explicit_sources))})")
                    params += explicit_sources
                clauses.append(f"({' OR '.join(src_parts)})")

            if req.date_from:
                clauses.append("created_at >= ?")
                params.append(req.date_from)
            if req.date_to:
                clauses.append("created_at <= ?")
                params.append(req.date_to)
            if req.lock_in_min > 0.0:
                clauses.append("lock_in >= ?")
                params.append(req.lock_in_min)
            if req.lock_in_max < 1.0:
                clauses.append("lock_in <= ?")
                params.append(req.lock_in_max)

            if req.entity_ids:
                ep = ",".join("?" * len(req.entity_ids))
                clauses.append(f"EXISTS (SELECT 1 FROM entity_mentions em WHERE em.node_id = memory_nodes.id AND em.entity_id IN ({ep}))")
                params += req.entity_ids

            if not req.show_quarantined:
                clauses.append("(review_status != 'quarantined' OR review_status IS NULL)")

            where = " AND ".join(clauses)
            order_col = {
                "lock_in": "lock_in DESC",
                "drift": "updated_at DESC",
            }.get(req.sort_by, "created_at DESC")

            cursor = await db.execute(
                f"SELECT {_OBS_FULL_COLS} FROM memory_nodes WHERE {where} ORDER BY {order_col}",
                params,
            )
            rows = [dict(r) for r in await cursor.fetchall()]

        total_count = len(rows)
        page_rows = rows[req.offset: req.offset + req.limit]

        counts = await _obs_neighbor_counts(db, [r["id"] for r in page_rows])

    for r in page_rows:
        r["neighbor_count"] = counts.get(r["id"], 0)

    elapsed = round((_time.monotonic() - t0) * 1000, 1)
    resp: dict = {
        "results": page_rows,
        "total_count": total_count,
        "unknown_provenance_count": unknown_count,
        "elapsed_ms": elapsed,
        "method_used": method_used,
    }
    if note:
        resp["note"] = note
    return resp


@router.get("/api/nodes/{node_id}")
async def observatory_node_detail(node_id: str):
    if not memory_matrix_path().exists():
        raise HTTPException(status_code=404, detail="DB not found")
    async with _readonly_db() as db:
        cursor = await db.execute(
            "SELECT id, node_type, content, source, confidence, importance, "
            "lock_in, lock_in_state, access_count, created_at, updated_at, "
            "metadata, custodian, date_shared, scope, classification "
            "FROM memory_nodes WHERE id = ? AND namespace = 'active'",
            (node_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Node not found")
        node = dict(row)

        cursor = await db.execute(
            "SELECT ge.from_id, ge.to_id, ge.relationship, ge.strength, "
            "mn.id AS neighbor_id, mn.node_type, mn.lock_in, mn.lock_in_state, "
            "SUBSTR(mn.content, 1, 120) AS content_preview "
            "FROM graph_edges ge "
            "JOIN memory_nodes mn ON "
            "  (CASE WHEN ge.from_id = ? THEN ge.to_id ELSE ge.from_id END) = mn.id "
            "WHERE (ge.from_id = ? OR ge.to_id = ?) AND mn.namespace = 'active'",
            (node_id, node_id, node_id),
        )
        raw = await cursor.fetchall()

    seen: dict = {}
    for r in raw:
        nid = r["neighbor_id"]
        if nid not in seen:
            seen[nid] = {
                "id": nid,
                "node_type": r["node_type"],
                "content_preview": r["content_preview"] or "",
                "relationship": r["relationship"],
                "strength": r["strength"] or 0.5,
                "lock_in": r["lock_in"] or 0.0,
                "lock_in_state": r["lock_in_state"] or "drifting",
            }
    node["neighbors"] = list(seen.values())
    return node


# ===========================================================================
# Observatory v0.2 — Write actions (nerf / quarantine / sanitize / batch)
# ===========================================================================

import datetime as _dt


class NodePatchBody(BaseModel):
    action: str                      # "nerf" | "quarantine" | "flag" | "restore" | "sanitize"
    content: Optional[str] = None   # sanitize only
    source: Optional[str] = None    # sanitize only


class BatchPatchBody(BaseModel):
    ids: List[str]
    action: str                      # "nerf" | "quarantine" | "flag" | "restore"


def _now_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


@router.patch("/api/nodes/{node_id}")
async def observatory_node_patch(node_id: str, body: NodePatchBody):
    if not memory_matrix_path().exists():
        raise HTTPException(status_code=404, detail="DB not found")

    now = _now_iso()

    async with _readwrite_db() as db:
        # Verify node exists
        cursor = await db.execute(
            "SELECT id, lock_in FROM memory_nodes WHERE id = ?", (node_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Node not found")

        if body.action == "nerf":
            new_lock_in = max(0.15, (row["lock_in"] or 0.15) * 0.5)
            await db.execute(
                "UPDATE memory_nodes SET lock_in = ?, lock_in_state = 'drifting', updated_at = ? WHERE id = ?",
                (new_lock_in, now, node_id),
            )

        elif body.action == "quarantine":
            await db.execute(
                "UPDATE memory_nodes SET review_status = 'quarantined', updated_at = ? WHERE id = ?",
                (now, node_id),
            )

        elif body.action == "unquarantine" or body.action == "restore":
            await db.execute(
                "UPDATE memory_nodes SET review_status = 'current', updated_at = ? WHERE id = ?",
                (now, node_id),
            )

        elif body.action == "flag":
            await db.execute(
                "UPDATE memory_nodes SET review_status = 'flagged', updated_at = ? WHERE id = ?",
                (now, node_id),
            )

        elif body.action == "sanitize":
            sets, params = [], []
            if body.content is not None:
                sets.append("content = ?")
                params.append(body.content)
            if body.source is not None:
                sets.append("source = ?")
                params.append(body.source)
            if not sets:
                raise HTTPException(status_code=400, detail="sanitize requires content or source")
            sets.append("updated_at = ?")
            params.append(now)
            params.append(node_id)
            await db.execute(
                f"UPDATE memory_nodes SET {', '.join(sets)} WHERE id = ?", params
            )

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

        # Return updated node
        cursor = await db.execute(
            "SELECT id, node_type, content, source, lock_in, lock_in_state, "
            "review_status, updated_at FROM memory_nodes WHERE id = ?",
            (node_id,),
        )
        updated = await cursor.fetchone()

    return {"ok": True, "node": dict(updated)}


@router.post("/api/nodes/batch")
async def observatory_nodes_batch(body: BatchPatchBody):
    if not memory_matrix_path().exists():
        raise HTTPException(status_code=404, detail="DB not found")
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids must not be empty")
    if body.action not in ("nerf", "quarantine", "flag", "restore", "unquarantine"):
        raise HTTPException(status_code=400, detail=f"Unknown batch action: {body.action}")

    now = _now_iso()
    ph = ",".join("?" * len(body.ids))

    async with _readwrite_db() as db:
        if body.action == "nerf":
            # Fetch current lock_in values to halve them individually
            cursor = await db.execute(
                f"SELECT id, lock_in FROM memory_nodes WHERE id IN ({ph})", body.ids
            )
            rows = await cursor.fetchall()
            for r in rows:
                new_li = max(0.15, (r["lock_in"] or 0.15) * 0.5)
                await db.execute(
                    "UPDATE memory_nodes SET lock_in = ?, lock_in_state = 'drifting', updated_at = ? WHERE id = ?",
                    (new_li, now, r["id"]),
                )

        elif body.action == "quarantine":
            await db.execute(
                f"UPDATE memory_nodes SET review_status = 'quarantined', updated_at = ? WHERE id IN ({ph})",
                [now] + body.ids,
            )

        elif body.action in ("unquarantine", "restore"):
            await db.execute(
                f"UPDATE memory_nodes SET review_status = 'current', updated_at = ? WHERE id IN ({ph})",
                [now] + body.ids,
            )

        elif body.action == "flag":
            await db.execute(
                f"UPDATE memory_nodes SET review_status = 'flagged', updated_at = ? WHERE id IN ({ph})",
                [now] + body.ids,
            )

    return {"ok": True, "affected": len(body.ids), "action": body.action}
