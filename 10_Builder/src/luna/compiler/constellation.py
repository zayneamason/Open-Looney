"""
Constellation Generators
========================

Produces aggregated knowledge nodes:
- PERSON_BRIEFING: Everything Luna knows about one entity
- PROJECT_STATUS: Current state of a project/initiative
- GOVERNANCE_RECORD: Council decisions and TK protocols
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .entity_index import EntityIndex, EntityProfile

logger = logging.getLogger(__name__)


@dataclass
class CompiledNode:
    """A node ready to be stored in the Memory Matrix."""

    source_id: str  # Original guardian ID (e.g. "briefing:ada_001")
    node_type: str
    content: str
    entities: list[str]
    scope: str
    confidence: float
    tags: list[str]
    lock_in: float = 0.8


def build_person_briefing(
    profile: EntityProfile,
    related_nodes: list[dict],
) -> CompiledNode:
    """
    Build a PERSON_BRIEFING constellation for one entity.

    Aggregates all facts, decisions, actions, milestones mentioning
    this entity into a single rich text node.
    """
    parts = []

    # Identity line
    identity = profile.name
    if profile.role:
        identity += f" is the {profile.role}"
    if profile.clan:
        identity += f", {profile.clan} clan"
    parts.append(identity + ".")

    if profile.profile:
        # Use first 2 sentences of the profile for context
        sentences = profile.profile.split(". ")
        summary = ". ".join(sentences[:2])
        if not summary.endswith("."):
            summary += "."
        parts.append(summary)

    # Group related nodes by type
    by_type: dict[str, list[dict]] = {}
    for node in related_nodes:
        ntype = node.get("node_type", "FACT")
        by_type.setdefault(ntype, []).append(node)

    # Key accomplishments (MILESTONES + FACTS with high lock-in)
    accomplishments = []
    for m in by_type.get("MILESTONE", []):
        accomplishments.append(m)
    for f in by_type.get("FACT", []):
        if f.get("lock_in", 0) >= 0.6:
            accomplishments.append(f)

    if accomplishments:
        parts.append("\nKey accomplishments:")
        for node in accomplishments[:8]:
            title = node.get("title", "")
            date = node.get("created_date", "")
            line = f"- {title}"
            if date:
                line += f" ({date})"
            parts.append(line)

    # Decisions involving this entity
    decisions = by_type.get("DECISION", [])
    if decisions:
        parts.append("\nKey decisions:")
        for d in decisions[:5]:
            parts.append(f"- {d.get('title', '')}")

    # Active actions
    actions = by_type.get("ACTION", [])
    if actions:
        parts.append("\nActive/pending actions:")
        for a in actions[:5]:
            parts.append(f"- {a.get('title', '')}")

    # Insights
    insights = by_type.get("INSIGHT", [])
    if insights:
        parts.append("\nInsights:")
        for i in insights[:4]:
            parts.append(f"- {i.get('title', '')}")

    # Scope and status
    if profile.scope:
        parts.append(f"\nScope: {profile.scope}.")
    if profile.scope_transitions:
        latest = profile.scope_transitions[-1]
        parts.append(
            f"Scope transition: {latest.get('from', '')} -> "
            f"{latest.get('to', '')} ({latest.get('date', 'unknown')})."
        )

    # Connections via household
    if profile.household:
        connections = []
        for role, name in profile.household.items():
            if isinstance(name, str):
                connections.append(f"{name} ({role})")
        if connections:
            parts.append(f"\nHousehold: {', '.join(connections)}.")

    content = "\n".join(parts)

    return CompiledNode(
        source_id=f"briefing:{profile.id}",
        node_type="PERSON_BRIEFING",
        content=content,
        entities=[profile.id],
        scope=profile.scope or "community",
        confidence=0.9,
        tags=["briefing", "compiled", "constellation", profile.id],
        lock_in=0.85,
    )


def build_project_status(
    project_name: str,
    project_id: str,
    milestones: list[dict],
    actions: list[dict],
    decisions: list[dict],
    scope: str = "community",
) -> CompiledNode:
    """Build a PROJECT_STATUS constellation from timeline + actions."""
    parts = [f"Project: {project_name}"]

    if milestones:
        parts.append("\nTimeline:")
        # Sort by date
        sorted_ms = sorted(milestones, key=lambda m: m.get("created_date", ""))
        for m in sorted_ms[:10]:
            date = m.get("created_date", "")
            parts.append(f"- [{date}] {m.get('title', '')}")

    active_actions = [a for a in actions if "pending" in str(a.get("tags", [])).lower()]
    completed_actions = [a for a in actions if "completed" in str(a.get("tags", [])).lower()]

    if completed_actions:
        parts.append(f"\nCompleted: {len(completed_actions)} actions")
    if active_actions:
        parts.append("\nPending actions:")
        for a in active_actions[:5]:
            parts.append(f"- {a.get('title', '')}")

    if decisions:
        parts.append(f"\nKey decisions: {len(decisions)}")
        for d in decisions[:3]:
            parts.append(f"- {d.get('title', '')}")

    # Collect all entities mentioned across nodes
    all_entities = set()
    for node_list in [milestones, actions, decisions]:
        for node in node_list:
            for e in node.get("entities", []):
                all_entities.add(e)

    content = "\n".join(parts)

    return CompiledNode(
        source_id=f"project_status:{project_id}",
        node_type="PROJECT_STATUS",
        content=content,
        entities=list(all_entities),
        scope=scope,
        confidence=0.85,
        tags=["project_status", "compiled", "constellation", project_id],
        lock_in=0.8,
    )


def build_governance_record(
    decisions: list[dict],
    insights: list[dict],
    scope_transitions: list[dict],
    entity_index: EntityIndex,
    project_name: str = None,
) -> Optional[CompiledNode]:
    """Build a GOVERNANCE_RECORD from governance-scoped decisions + protocols.

    Args:
        project_name: Display name for the governance record header.
            If None and no governance data exists, returns None.
    """
    gov_decisions = [d for d in decisions if d.get("scope") == "governance"]

    # If no project name provided and no governance data, skip entirely
    if not project_name and not gov_decisions and not scope_transitions:
        return None

    header = f"Governance Record — {project_name}" if project_name else "Governance Record"
    parts = [header]

    if gov_decisions:
        parts.append("\nCouncil decisions:")
        for d in sorted(gov_decisions, key=lambda x: x.get("created_date", "")):
            parts.append(f"- [{d.get('created_date', '')}] {d.get('title', '')}")

    gov_insights = [i for i in insights if i.get("scope") == "governance"]
    if gov_insights:
        parts.append("\nGovernance insights:")
        for i in gov_insights[:5]:
            parts.append(f"- {i.get('title', '')}")

    if scope_transitions:
        parts.append("\nScope transitions:")
        for st in scope_transitions:
            parts.append(
                f"- [{st.get('timestamp', '')[:10]}] "
                f"{st.get('from_scope', '')} -> {st.get('to_scope', '')}: "
                f"{st.get('content_description', '')[:100]}"
            )

    all_entities = set()
    for d in gov_decisions:
        for e in d.get("entities", []):
            all_entities.add(e)

    content = "\n".join(parts)

    source_slug = project_name.lower().replace(" ", "_")[:40] if project_name else "active_project"
    return CompiledNode(
        source_id=f"governance_record:{source_slug}",
        node_type="GOVERNANCE_RECORD",
        content=content,
        entities=list(all_entities),
        scope="governance",
        confidence=0.9,
        tags=["governance", "compiled", "constellation"],
        lock_in=0.9,
    )
