"""
Ambassador Proxy — Sovereign Knowledge Sharing
===============================================

A stateless function that evaluates a query against a user's declared
ambassador protocol and returns only explicitly allowed knowledge.

The ambassador is dumb about boundaries, smart about presentation.
It executes a declared protocol. It does not interpret intent.

Core constraints:
    - default_action is ALWAYS "deny" (fail closed)
    - exclude rules ALWAYS override include rules
    - ambassadors are isolated (no ambassador-to-ambassador communication)
    - ambassadors are one-way valves (outward only)
    - every response is auditable

Usage:
    proxy = AmbassadorProxy(db)
    result = await proxy.evaluate(
        owner_entity_id="community_member_a",
        requester_entity_id="elder_a",
        requester_roles=["elder", "council_member"],
        query_text="What is the status of the restoration project?",
        knowledge_nodes=[...],  # Memory nodes to filter
    )
    # result.allowed = [...nodes that passed protocol...]
    # result.denied  = [...nodes that were blocked...]
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AmbassadorRule:
    """A single rule from the ambassador protocol."""
    id: str
    description: str
    audience_type: str                      # "role", "individual", "group"
    audience_values: list[str]              # Roles, entity IDs, or group names
    scope_include: list[str]                # Knowledge categories allowed
    scope_exclude: list[str]                # Knowledge categories always blocked
    conditions: dict = field(default_factory=dict)

    def audience_matches(self, requester_roles: list[str],
                         requester_entity_id: Optional[str] = None) -> bool:
        """Check if the requester matches this rule's audience."""
        if self.audience_type == "role":
            return bool(set(requester_roles) & set(self.audience_values))
        if self.audience_type == "individual":
            return requester_entity_id in self.audience_values
        if self.audience_type == "group":
            # Groups resolved externally; for now treat as role match
            return bool(set(requester_roles) & set(self.audience_values))
        return False

    def scope_allows(self, knowledge_categories: list[str]) -> bool:
        """
        Check if this rule allows the given knowledge categories.

        CRITICAL: exclude ALWAYS overrides include.
        """
        if not knowledge_categories:
            return False

        for cat in knowledge_categories:
            # Exclude check first — always wins
            if cat in self.scope_exclude:
                return False

        # At least one category must be in include list
        return any(cat in self.scope_include for cat in knowledge_categories)

    def conditions_met(self, context: dict) -> bool:
        """Check if rule conditions are satisfied."""
        if not self.conditions:
            return True

        # Time-bound check
        time_bound = self.conditions.get("time_bound")
        if time_bound is not None:
            # TODO: implement time-bound checking when needed
            pass

        # Active project check
        if self.conditions.get("requires_active_project"):
            if not context.get("active_project"):
                return False

        # Council approval check
        if self.conditions.get("requires_council_approval"):
            if not context.get("council_approved"):
                return False

        return True


@dataclass
class AmbassadorProtocol:
    """A user's complete ambassador protocol."""
    owner_entity_id: str
    version: str
    display_name: str
    rules: list[AmbassadorRule]
    default_action: str = "deny"            # Always "deny"
    audit_log_enabled: bool = True

    @classmethod
    def from_json(cls, owner_entity_id: str, data: dict) -> "AmbassadorProtocol":
        """Parse a protocol from its JSON representation."""
        proto = data.get("ambassador_protocol", data)

        rules = []
        for rule_data in proto.get("rules", []):
            audience = rule_data.get("audience", {})
            scope = rule_data.get("scope", {})
            rules.append(AmbassadorRule(
                id=rule_data.get("id", ""),
                description=rule_data.get("description", ""),
                audience_type=audience.get("type", "role"),
                audience_values=audience.get("value", []),
                scope_include=scope.get("include", []),
                scope_exclude=scope.get("exclude", []),
                conditions=rule_data.get("conditions", {}),
            ))

        return cls(
            owner_entity_id=owner_entity_id,
            version=proto.get("version", "0.1"),
            display_name=proto.get("display_name", ""),
            rules=rules,
            default_action=proto.get("default_action", "deny"),
            audit_log_enabled=proto.get("audit_log", True),
        )


@dataclass
class AmbassadorResult:
    """Result of an ambassador evaluation."""
    allowed: list[dict] = field(default_factory=list)
    denied: list[dict] = field(default_factory=list)
    rule_matches: dict = field(default_factory=dict)  # node_id -> rule_id


# ---------------------------------------------------------------------------
# Knowledge category extraction
# ---------------------------------------------------------------------------

def _extract_categories(node: dict) -> list[str]:
    """
    Extract knowledge categories from a memory node.

    Categories come from:
    - metadata.ambassador_categories (explicit)
    - tags (e.g., "project_updates", "personal")
    - node_type mapped to category
    - metadata.scope_tag
    """
    categories = []

    # Explicit ambassador categories
    metadata = node.get("metadata", {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    ambassador_cats = metadata.get("ambassador_categories", [])
    if isinstance(ambassador_cats, list):
        categories.extend(ambassador_cats)

    # Tags as categories
    tags = node.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    categories.extend(tags)

    # Node type as category (lowercase)
    node_type = node.get("node_type", "").lower()
    if node_type:
        categories.append(node_type)

    # Scope tag
    scope_tag = metadata.get("scope_tag", "")
    if scope_tag:
        categories.append(scope_tag)

    # Content-based category inference for demo personas
    # Map known content patterns to categories
    content = (node.get("content", "") + " " + node.get("summary", "")).lower()
    _CONTENT_CATEGORY_MAP = {
        "restoration": "restoration_data",
        "monitoring": "monitoring_results",
        "volunteer": "volunteer_counts",
        "crew": "crew_schedules",
        "site prep": "site_prep_status",
        "attendance": "meeting_attendance",
        "grant": "grant_evidence",
        "funding": "funding_timeline",
        "budget": "budget_summary",
        "stipend": "stipend_structure",
        "governance": "governance_frameworks",
        "three hearings": "three_hearings_protocol",
        "council": "council_precedents",
        "oral history": "oral_history_approved",
        "stewardship": "land_stewardship_knowledge",
        "water agreement": "water_agreements_public",
    }
    for keyword, category in _CONTENT_CATEGORY_MAP.items():
        if keyword in content:
            categories.append(category)

    return list(set(categories))  # Deduplicate


# ---------------------------------------------------------------------------
# Ambassador Proxy
# ---------------------------------------------------------------------------

class AmbassadorProxy:
    """
    Stateless ambassador proxy.

    Evaluates queries against a user's declared protocol and filters
    knowledge nodes accordingly. Every evaluation is audit-logged.

    The proxy is:
    - Stateless: (query, protocol) → response | null
    - Isolated: no awareness of other ambassadors
    - One-way: outward projection only, never receives
    - Auditable: every response logged
    """

    def __init__(self, db: MemoryDatabase):
        self.db = db

    async def load_protocol(self, owner_entity_id: str) -> Optional[AmbassadorProtocol]:
        """Load a user's ambassador protocol from the database."""
        row = await self.db.fetchone(
            "SELECT protocol_json, version, display_name, default_action, audit_log_enabled "
            "FROM ambassador_protocol WHERE owner_entity_id = ?",
            (owner_entity_id,),
        )
        if not row:
            return None

        try:
            protocol_data = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            logger.error("Invalid protocol JSON for %s", owner_entity_id)
            return None

        protocol = AmbassadorProtocol.from_json(owner_entity_id, protocol_data)
        protocol.version = row[1] or protocol.version
        protocol.display_name = row[2] or protocol.display_name
        protocol.default_action = row[3] or "deny"
        protocol.audit_log_enabled = bool(row[4])
        return protocol

    async def save_protocol(
        self,
        protocol: AmbassadorProtocol,
        updated_by: str = "system",
    ) -> None:
        """
        Save or update a user's ambassador protocol.

        Args:
            protocol: The protocol to save
            updated_by: Entity ID of who is making the change (FaceID gated)
        """
        protocol_json = json.dumps(self._protocol_to_dict(protocol))

        await self.db.execute(
            """INSERT INTO ambassador_protocol
               (owner_entity_id, version, display_name, protocol_json,
                default_action, audit_log_enabled, updated_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_entity_id) DO UPDATE SET
                   version = excluded.version,
                   display_name = excluded.display_name,
                   protocol_json = excluded.protocol_json,
                   default_action = excluded.default_action,
                   audit_log_enabled = excluded.audit_log_enabled,
                   updated_by = excluded.updated_by""",
            (
                protocol.owner_entity_id,
                protocol.version,
                protocol.display_name,
                protocol_json,
                protocol.default_action,
                1 if protocol.audit_log_enabled else 0,
                updated_by,
            ),
        )

    async def evaluate(
        self,
        owner_entity_id: str,
        requester_entity_id: Optional[str],
        requester_roles: list[str],
        query_text: str,
        knowledge_nodes: list[dict],
        context: Optional[dict] = None,
    ) -> AmbassadorResult:
        """
        Evaluate a query against an owner's ambassador protocol.

        This is the core stateless function:
            (query, protocol) → response | null

        Args:
            owner_entity_id: Whose ambassador to query
            requester_entity_id: Who is asking
            requester_roles: Roles of the requester (e.g. ["elder", "council_member"])
            query_text: The query text
            knowledge_nodes: Memory nodes to filter through the protocol
            context: Optional context for condition evaluation

        Returns:
            AmbassadorResult with allowed and denied node lists
        """
        context = context or {}
        result = AmbassadorResult()

        # Load protocol
        protocol = await self.load_protocol(owner_entity_id)
        if not protocol:
            # No protocol declared — deny everything (fail closed)
            result.denied = list(knowledge_nodes)
            await self._audit_batch(
                owner_entity_id, requester_entity_id,
                requester_roles, query_text, result,
            )
            return result

        # Evaluate each knowledge node against the protocol
        for node in knowledge_nodes:
            categories = _extract_categories(node)
            matched_rule = self._match_rule(
                protocol, requester_roles, requester_entity_id,
                categories, context,
            )

            node_id = node.get("id", "") or node.get("node_id", "")

            if matched_rule:
                result.allowed.append(node)
                result.rule_matches[node_id] = matched_rule.id
            else:
                # default_action: deny — silent deny
                result.denied.append(node)

        # Audit log
        await self._audit_batch(
            owner_entity_id, requester_entity_id,
            requester_roles, query_text, result,
        )

        logger.info(
            "Ambassador[%s] → requester=%s roles=%s: allowed=%d denied=%d",
            owner_entity_id, requester_entity_id, requester_roles,
            len(result.allowed), len(result.denied),
        )

        return result

    def _match_rule(
        self,
        protocol: AmbassadorProtocol,
        requester_roles: list[str],
        requester_entity_id: Optional[str],
        categories: list[str],
        context: dict,
    ) -> Optional[AmbassadorRule]:
        """
        Find the first matching rule in the protocol.

        Rules are evaluated in order. First match wins.
        If no rule matches, returns None (→ deny).
        """
        for rule in protocol.rules:
            # 1. Audience check
            if not rule.audience_matches(requester_roles, requester_entity_id):
                continue

            # 2. Scope check (exclude overrides include)
            if not rule.scope_allows(categories):
                continue

            # 3. Conditions check
            if not rule.conditions_met(context):
                continue

            return rule

        return None

    async def _audit_batch(
        self,
        owner_entity_id: str,
        requester_entity_id: Optional[str],
        requester_roles: list[str],
        query_text: str,
        result: AmbassadorResult,
    ) -> None:
        """Write audit log entries for an ambassador evaluation."""
        try:
            roles_str = ",".join(requester_roles) if requester_roles else ""

            # Log allowed nodes
            for node in result.allowed:
                node_id = node.get("id", "") or node.get("node_id", "")
                rule_id = result.rule_matches.get(node_id, "")
                categories = _extract_categories(node)
                await self.db.execute(
                    """INSERT INTO ambassador_audit_log
                       (owner_entity_id, requester_entity_id, requester_role,
                        query_text, rule_matched, knowledge_returned,
                        scope_categories_matched, action)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        owner_entity_id,
                        requester_entity_id,
                        roles_str,
                        query_text,
                        rule_id,
                        json.dumps({"node_id": node_id, "summary": node.get("summary", "")[:200]}),
                        json.dumps(categories[:10]),
                        "allow",
                    ),
                )

            # Log denied nodes (no knowledge content leaked)
            for node in result.denied:
                node_id = node.get("id", "") or node.get("node_id", "")
                await self.db.execute(
                    """INSERT INTO ambassador_audit_log
                       (owner_entity_id, requester_entity_id, requester_role,
                        query_text, rule_matched, knowledge_returned,
                        scope_categories_matched, action)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        owner_entity_id,
                        requester_entity_id,
                        roles_str,
                        query_text,
                        None,
                        None,  # No knowledge leaked on deny
                        None,
                        "deny",
                    ),
                )

        except Exception as e:
            logger.warning("Ambassador audit logging failed: %s", e)

    async def get_audit_log(
        self,
        owner_entity_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """
        Retrieve audit log entries for a user's ambassador.

        Only the owner can view their own audit log (FaceID gated at API layer).
        """
        rows = await self.db.fetchall(
            """SELECT id, requester_entity_id, requester_role, query_text,
                      rule_matched, knowledge_returned, scope_categories_matched,
                      action, created_at
               FROM ambassador_audit_log
               WHERE owner_entity_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (owner_entity_id, limit),
        )
        return [
            {
                "id": row[0],
                "requester_entity_id": row[1],
                "requester_role": row[2],
                "query_text": row[3],
                "rule_matched": row[4],
                "knowledge_returned": row[5],
                "scope_categories_matched": row[6],
                "action": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    @staticmethod
    def _protocol_to_dict(protocol: AmbassadorProtocol) -> dict:
        """Serialize a protocol back to its JSON representation."""
        return {
            "ambassador_protocol": {
                "version": protocol.version,
                "owner": protocol.owner_entity_id,
                "display_name": protocol.display_name,
                "rules": [
                    {
                        "id": rule.id,
                        "description": rule.description,
                        "audience": {
                            "type": rule.audience_type,
                            "value": rule.audience_values,
                        },
                        "scope": {
                            "include": rule.scope_include,
                            "exclude": rule.scope_exclude,
                        },
                        "conditions": rule.conditions,
                    }
                    for rule in protocol.rules
                ],
                "default_action": protocol.default_action,
                "audit_log": protocol.audit_log_enabled,
            }
        }
