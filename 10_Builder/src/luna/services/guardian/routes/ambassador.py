"""
Ambassador Routes — Protocol Management & Query API
====================================================

Endpoints for managing ambassador protocols and querying ambassadors
via the sovereign membrane.

FaceID gate: Protocol modification requires authenticated identity.
Audit log: Only the owner can view their own log.
Queries: Any authenticated entity can query; the protocol decides what returns.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ambassador")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ProtocolUpdateRequest(BaseModel):
    """Request to set or update an ambassador protocol."""
    protocol: dict = Field(..., description="Full ambassador protocol JSON")
    updated_by: str = Field(..., description="Entity ID of the authenticated user")


class AmbassadorQueryRequest(BaseModel):
    """Request to query an ambassador."""
    owner_entity_id: str = Field(..., description="Whose ambassador to query")
    requester_entity_id: str = Field(..., description="Who is asking")
    requester_roles: list[str] = Field(default_factory=list, description="Roles of the requester")
    query_text: str = Field(..., description="The query")
    knowledge_node_ids: list[str] = Field(
        default_factory=list,
        description="Specific node IDs to filter (empty = use membrane retrieval)"
    )
    context: dict = Field(default_factory=dict, description="Context for condition evaluation")


class AmbassadorQueryResponse(BaseModel):
    """Response from an ambassador query."""
    owner_entity_id: str
    allowed_count: int
    denied_count: int
    allowed_nodes: list[dict]
    rule_matches: dict


# ---------------------------------------------------------------------------
# Helper: get engine from request state
# ---------------------------------------------------------------------------

def _get_engine(request: Request):
    """Extract LunaEngine from FastAPI app state."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not available")
    return engine


def _get_db(request: Request):
    """Extract database from engine."""
    engine = _get_engine(request)
    matrix = engine.get_actor("matrix")
    if matrix is None or not matrix.is_ready:
        raise HTTPException(status_code=503, detail="Memory matrix not ready")
    return matrix._matrix.db


def _require_identity(request: Request, required_entity_id: str):
    """
    FaceID gate: verify the request comes from the correct identity.

    In the full implementation, this checks the FaceID-authenticated
    identity from the request. For the demo, we validate the entity_id
    parameter against the identity actor.
    """
    engine = _get_engine(request)
    identity_actor = engine.get_actor("identity")

    # If identity actor is available, enforce FaceID
    if identity_actor and hasattr(identity_actor, "current"):
        current = identity_actor.current
        if hasattr(current, "is_present") and current.is_present:
            if current.entity_id != required_entity_id:
                raise HTTPException(
                    status_code=403,
                    detail="Protocol modification requires authenticated identity match"
                )
            return  # Identity matches

    # If no identity actor (demo mode), allow but log
    logger.warning(
        "Ambassador: FaceID not active, allowing protocol access for %s",
        required_entity_id,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/protocol/{entity_id}")
async def get_protocol(entity_id: str, request: Request):
    """
    Get an entity's ambassador protocol.

    Only the owner can view their full protocol.
    """
    _require_identity(request, entity_id)
    db = _get_db(request)

    from luna.identity.ambassador import AmbassadorProxy
    proxy = AmbassadorProxy(db)
    protocol = await proxy.load_protocol(entity_id)

    if protocol is None:
        raise HTTPException(status_code=404, detail="No protocol found for this entity")

    return AmbassadorProxy._protocol_to_dict(protocol)


@router.put("/protocol/{entity_id}")
async def set_protocol(entity_id: str, body: ProtocolUpdateRequest, request: Request):
    """
    Set or update an entity's ambassador protocol.

    Requires FaceID authentication matching the entity.
    """
    # FaceID gate
    _require_identity(request, entity_id)

    if body.updated_by != entity_id:
        raise HTTPException(
            status_code=403,
            detail="Only the protocol owner can modify their ambassador"
        )

    db = _get_db(request)

    from luna.identity.ambassador import AmbassadorProxy, AmbassadorProtocol
    proxy = AmbassadorProxy(db)

    # Parse and validate the protocol
    try:
        protocol = AmbassadorProtocol.from_json(entity_id, body.protocol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid protocol: {e}")

    # Force deny-by-default (never allow override)
    protocol.default_action = "deny"
    protocol.audit_log_enabled = True

    await proxy.save_protocol(protocol, updated_by=body.updated_by)

    return {"status": "saved", "owner": entity_id, "rules_count": len(protocol.rules)}


@router.post("/query")
async def query_ambassador(body: AmbassadorQueryRequest, request: Request):
    """
    Query an ambassador for knowledge.

    The ambassador evaluates the query against its protocol and returns
    only explicitly allowed knowledge. Silent deny for everything else.
    """
    db = _get_db(request)
    engine = _get_engine(request)

    from luna.identity.ambassador import AmbassadorProxy

    proxy = AmbassadorProxy(db)

    # Retrieve knowledge nodes
    knowledge_nodes = []
    if body.knowledge_node_ids:
        # Specific nodes requested
        for node_id in body.knowledge_node_ids:
            row = await db.fetchone(
                "SELECT id, node_type, content, summary, metadata "
                "FROM memory_nodes WHERE id = ?",
                (node_id,),
            )
            if row:
                metadata = row[4] or "{}"
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
                knowledge_nodes.append({
                    "id": row[0],
                    "node_type": row[1],
                    "content": row[2],
                    "summary": row[3],
                    "metadata": metadata,
                })
    else:
        # Retrieve from membrane context (project-scoped)
        matrix = engine.get_actor("matrix")
        if matrix and matrix.is_ready:
            scope = engine.active_scope if engine else "global"
            results = await matrix._matrix.search_nodes(
                query=body.query_text,
                limit=20,
                scope=scope,
            )
            for r in results:
                if isinstance(r, dict):
                    knowledge_nodes.append(r)
                else:
                    knowledge_nodes.append({
                        "id": getattr(r, "id", ""),
                        "node_type": getattr(r, "node_type", ""),
                        "content": getattr(r, "content", ""),
                        "summary": getattr(r, "summary", ""),
                        "metadata": getattr(r, "metadata", {}),
                        "tags": getattr(r, "tags", []),
                    })

    # Run ambassador evaluation
    result = await proxy.evaluate(
        owner_entity_id=body.owner_entity_id,
        requester_entity_id=body.requester_entity_id,
        requester_roles=body.requester_roles,
        query_text=body.query_text,
        knowledge_nodes=knowledge_nodes,
        context=body.context,
    )

    return AmbassadorQueryResponse(
        owner_entity_id=body.owner_entity_id,
        allowed_count=len(result.allowed),
        denied_count=len(result.denied),
        allowed_nodes=result.allowed,
        rule_matches=result.rule_matches,
    )


@router.get("/audit/{entity_id}")
async def get_audit_log(
    entity_id: str,
    request: Request,
    limit: int = 50,
):
    """
    Get the audit log for an entity's ambassador.

    Only the owner can view their audit log (FaceID gated).
    """
    _require_identity(request, entity_id)
    db = _get_db(request)

    from luna.identity.ambassador import AmbassadorProxy
    proxy = AmbassadorProxy(db)
    entries = await proxy.get_audit_log(entity_id, limit=limit)

    return {"owner": entity_id, "entries": entries, "count": len(entries)}


@router.post("/membrane/query")
async def membrane_query(body: AmbassadorQueryRequest, request: Request):
    """
    Membrane-level query: routes a query to ALL relevant ambassadors.

    The membrane queries each user's ambassador independently.
    Ambassadors are isolated — they don't know about each other.
    Results are aggregated by the membrane, not by ambassadors.
    """
    db = _get_db(request)

    from luna.identity.ambassador import AmbassadorProxy
    proxy = AmbassadorProxy(db)

    # Get all ambassadors (the membrane knows about all protocols)
    rows = await db.fetchall(
        "SELECT owner_entity_id FROM ambassador_protocol"
    )

    aggregated_allowed = []
    total_denied = 0

    for row in rows:
        owner_id = row[0]

        # Skip querying yourself
        if owner_id == body.requester_entity_id:
            continue

        # Each ambassador is queried independently (isolation)
        # Get this owner's knowledge nodes
        owner_nodes = await db.fetchall(
            """SELECT mn.id, mn.node_type, mn.content, mn.summary, mn.metadata
               FROM memory_nodes mn
               JOIN entity_mentions em ON mn.id = em.node_id
               WHERE em.entity_id = ?
               LIMIT 50""",
            (owner_id,),
        )

        knowledge_nodes = []
        for n in owner_nodes:
            metadata = n[4] or "{}"
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            knowledge_nodes.append({
                "id": n[0], "node_type": n[1],
                "content": n[2], "summary": n[3],
                "metadata": metadata,
            })

        if not knowledge_nodes:
            continue

        result = await proxy.evaluate(
            owner_entity_id=owner_id,
            requester_entity_id=body.requester_entity_id,
            requester_roles=body.requester_roles,
            query_text=body.query_text,
            knowledge_nodes=knowledge_nodes,
            context=body.context,
        )

        for node in result.allowed:
            node["_shared_by"] = owner_id
            aggregated_allowed.append(node)
        total_denied += len(result.denied)

    return {
        "query": body.query_text,
        "requester": body.requester_entity_id,
        "results": aggregated_allowed,
        "allowed_count": len(aggregated_allowed),
        "denied_count": total_denied,
    }
