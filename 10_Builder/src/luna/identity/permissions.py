"""
Permission Filter — Data Room Access Control
=============================================

Filters Memory Matrix document nodes based on the querying entity's
data room tier and category access. Runs at query time, between
document retrieval and response assembly.

gate_content() is the single choke-point — every content path must call it.
"""

import json
import logging
from typing import Optional, Any

from .bridge import BridgeResult
from luna.core.owner import admin_contacts_str

logger = logging.getLogger(__name__)

_DOCUMENT_TYPES = {"DOCUMENT", "document"}


# Denial response templates by Luna tier
def _denial_templates() -> dict:
    _contacts = admin_contacts_str()
    return {
        "admin": "",                           # Admin never gets denied
        "trusted": (
            "that one's outside your current access — "
            f"you'd want to check with {_contacts}. "
            "i can help you find related docs you do have access to though."
        ),
        "friend": (
            "i don't have access to share that one with you. "
            f"{_contacts} could help."
        ),
        "guest": "that document requires additional access permissions.",
        "unknown": None,                       # None = don't acknowledge the document exists
    }

DENIAL_TEMPLATES = _denial_templates()


def filter_documents(
    results: list[dict],
    bridge: Optional[BridgeResult],
) -> tuple[list[dict], list[dict]]:
    """
    Filter Memory Matrix search results by data room permissions.

    Args:
        results: Memory Matrix search results (each has node metadata)
        bridge: BridgeResult for the querying entity (None = unknown)

    Returns:
        (allowed, denied) — two lists of results
    """
    if bridge is None:
        # Unknown entity — deny everything, don't leak existence
        return [], results

    if bridge.can_see_all:
        # Tier 1-2 see everything
        return results, []

    allowed = []
    denied = []

    for result in results:
        # Extract category from node metadata
        metadata = result.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        category = metadata.get("dataroom_category")

        # Also parse "category": "3. Legal" format (MCP ingestion)
        cat_string = metadata.get("category", "")
        if isinstance(cat_string, str) and cat_string and cat_string[0].isdigit():
            try:
                parsed_cat = int(cat_string.split(".")[0].strip())
                if 1 <= parsed_cat <= 9:
                    category = category or parsed_cat
            except (ValueError, IndexError):
                pass

        # Also check tags (documents ingested via MCP may store category in tags)
        tags = result.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []

        # Collect all categories this document belongs to
        categories = []
        if category is not None:
            try:
                categories.append(int(category))
            except (ValueError, TypeError):
                pass
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("dr_cat:"):
                try:
                    categories.append(int(tag.split(":")[1]))
                except (ValueError, IndexError):
                    pass

        if not categories:
            # No category assigned — non-dataroom content (conversation memories etc.)
            allowed.append(result)
            continue

        # Check if ANY of the document's categories match the entity's access
        if any(bridge.can_access_category(c) for c in categories):
            allowed.append(result)
        else:
            denied.append(result)

    if denied:
        logger.info(
            "Permission filter: %s — allowed %d, denied %d documents",
            bridge.entity_id, len(allowed), len(denied),
        )

    return allowed, denied


def get_denial_message(bridge: Optional[BridgeResult]) -> Optional[str]:
    """
    Get the appropriate denial message for this entity's Luna tier.
    Returns None if the entity shouldn't know the document exists.
    """
    if bridge is None:
        return None                        # Don't acknowledge existence
    return DENIAL_TEMPLATES.get(bridge.luna_tier, DENIAL_TEMPLATES["guest"])


# ---------------------------------------------------------------------------
# Central Content Gate — single choke-point for ALL content paths
# ---------------------------------------------------------------------------

def _node_type(item: Any) -> str:
    """Extract node_type from a dict, MemoryNode object, or similar."""
    if isinstance(item, dict):
        return item.get("node_type", "") or item.get("type", "")
    return getattr(item, "node_type", "") or ""


def _node_id(item: Any) -> str:
    """Extract node id for logging."""
    if isinstance(item, dict):
        return item.get("id", "") or item.get("node_id", "")
    return getattr(item, "id", "") or ""


def _to_filter_dict(item: Any) -> dict:
    """Normalise a node into the dict format filter_documents expects."""
    if isinstance(item, dict):
        return item
    # MemoryNode-like objects
    meta = getattr(item, "metadata", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    tags = getattr(item, "tags", [])
    return {
        "id": getattr(item, "id", ""),
        "content": getattr(item, "content", ""),
        "summary": getattr(item, "summary", ""),
        "metadata": meta,
        "tags": tags,
        "_original": item,
    }


async def gate_content(
    nodes: list,
    bridge: Optional[BridgeResult],
    db: Optional[Any] = None,
    source: str = "unknown",
) -> tuple[list, list]:
    """
    Central content gate. Every path that returns content MUST call this.

    Contract:
        - bridge is None  → strip ALL DOCUMENT nodes (deny-by-default)
        - bridge.can_see_all (tier 1-2) → pass everything
        - otherwise → filter_documents() on DOCUMENT nodes, pass rest

    Non-DOCUMENT nodes (FACT, ENTITY, CONVERSATION, etc.) always pass.

    Args:
        nodes:  list of dicts or MemoryNode-like objects
        bridge: BridgeResult for the requesting entity, or None
        db:     optional MemoryDatabase for audit logging
        source: label for audit trail (e.g. "constellation", "api/dataroom")

    Returns:
        (allowed, denied) — two lists preserving original item types
    """
    if not nodes:
        return [], []

    # Fast path: tier 1-2 see everything
    if bridge is not None and bridge.can_see_all:
        return list(nodes), []

    doc_items = []
    non_doc_items = []

    for item in nodes:
        ntype = _node_type(item)
        if ntype in _DOCUMENT_TYPES:
            doc_items.append(item)
        else:
            non_doc_items.append(item)

    # No documents → nothing to filter
    if not doc_items:
        return list(nodes), []

    # bridge is None → deny ALL documents
    if bridge is None:
        denied = doc_items
        allowed_docs = []
    else:
        # Normalise to dict format for filter_documents
        doc_dicts = [_to_filter_dict(d) for d in doc_items]
        allowed_dicts, denied_dicts = filter_documents(doc_dicts, bridge)

        # Map back to original items
        allowed_ids = {d.get("id") or d.get("node_id") for d in allowed_dicts}
        allowed_docs = []
        denied = []
        for item in doc_items:
            nid = _node_id(item)
            if nid and nid in allowed_ids:
                allowed_docs.append(item)
            else:
                denied.append(item)

    # Audit logging
    if denied:
        entity_id = bridge.entity_id if bridge else "anonymous"
        logger.info(
            "GATE[%s] %s: allowed=%d docs, denied=%d docs, passthrough=%d non-docs",
            source, entity_id, len(allowed_docs), len(denied), len(non_doc_items),
        )
        if db:
            try:
                for item in denied:
                    await db.execute(
                        "INSERT INTO permission_log (event_type, entity_id, details) "
                        "VALUES (?, ?, ?)",
                        (
                            "deny",
                            entity_id,
                            json.dumps({
                                "node_id": _node_id(item),
                                "node_type": _node_type(item),
                                "source": source,
                            }),
                        ),
                    )
            except Exception as e:
                logger.warning("Audit logging failed: %s", e)

    return non_doc_items + allowed_docs, denied
