"""
Demo Ambassador Protocols — ROSA Presentation
===============================================

Pre-configured protocols for the three demo personas:
- Community Member A (Youth Leader)
- Elder A (Governance Authority)
- Youth A (Financial Stewardship)

Loaded on Guardian activation. Shows the pattern working,
not the configuration UI.
"""

import json
import logging
from typing import Optional

from luna.substrate.database import MemoryDatabase
from luna.identity.ambassador import AmbassadorProxy, AmbassadorProtocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Demo protocol definitions (from ambassador_pattern_spec.md Appendix A)
# ---------------------------------------------------------------------------

COMMUNITY_MEMBER_A_PROTOCOL = {
    "ambassador_protocol": {
        "version": "0.1",
        "owner": "community_member_a",
        "display_name": "Community Member A's Ambassador",
        "rules": [
            {
                "id": "project-sharing",
                "description": "Share project work with all community roles",
                "audience": {
                    "type": "role",
                    "value": ["elder", "council_member", "youth_leader",
                              "treasurer", "community_member"],
                },
                "scope": {
                    "include": [
                        "project_updates", "restoration_data",
                        "monitoring_results", "crew_schedules",
                        "volunteer_counts", "site_prep_status",
                        "meeting_attendance", "grant_evidence",
                    ],
                    "exclude": [
                        "personal", "health", "family",
                        "financial_personal", "tendo_care",
                        "mother_health", "stress", "relationship",
                    ],
                },
                "conditions": {},
            },
            {
                "id": "governance-participation",
                "description": "Share governance-relevant input with council",
                "audience": {
                    "type": "role",
                    "value": ["elder", "council_member"],
                },
                "scope": {
                    "include": [
                        "youth_perspective", "capacity_assessment",
                        "community_feedback_from_youth",
                    ],
                    "exclude": ["personal", "health", "family"],
                },
                "conditions": {},
            },
        ],
        "default_action": "deny",
        "audit_log": True,
    }
}

ELDER_A_PROTOCOL = {
    "ambassador_protocol": {
        "version": "0.1",
        "owner": "elder_a",
        "display_name": "Elder A's Ambassador",
        "rules": [
            {
                "id": "governance-sharing",
                "description": "Share governance frameworks and precedents with community",
                "audience": {
                    "type": "role",
                    "value": ["elder", "council_member", "youth_leader", "treasurer"],
                },
                "scope": {
                    "include": [
                        "governance_frameworks", "three_hearings_protocol",
                        "council_precedents", "cultural_protocols",
                        "decision_history",
                    ],
                    "exclude": [
                        "personal", "health", "clan_restricted",
                        "ceremonial_private",
                    ],
                },
                "conditions": {},
            },
            {
                "id": "elder-teaching",
                "description": "Oral history and teachings — council-approved only",
                "audience": {
                    "type": "role",
                    "value": ["youth_leader", "community_member"],
                },
                "scope": {
                    "include": [
                        "oral_history_approved", "land_stewardship_knowledge",
                        "water_agreements_public",
                    ],
                    "exclude": [
                        "sacred_knowledge", "clan_restricted",
                        "ceremonial_private", "personal",
                    ],
                },
                "conditions": {
                    "requires_council_approval": True,
                },
            },
        ],
        "default_action": "deny",
        "audit_log": True,
    }
}

YOUTH_A_PROTOCOL = {
    "ambassador_protocol": {
        "version": "0.1",
        "owner": "youth_a",
        "display_name": "Youth A's Ambassador",
        "rules": [
            {
                "id": "financial-alerts",
                "description": "Share financial status and alerts with governance roles",
                "audience": {
                    "type": "role",
                    "value": ["elder", "council_member"],
                },
                "scope": {
                    "include": [
                        "funding_timeline", "grant_status",
                        "budget_alerts", "financial_planning",
                        "stipend_structure", "community_contribution_model",
                    ],
                    "exclude": [
                        "personal", "health", "family",
                        "personal_finances", "anxiety_notes",
                    ],
                },
                "conditions": {},
            },
            {
                "id": "budget-transparency",
                "description": "Share budget summaries with all community roles",
                "audience": {
                    "type": "role",
                    "value": ["youth_leader", "community_member"],
                },
                "scope": {
                    "include": [
                        "budget_summary", "stipend_structure",
                        "funding_timeline_public",
                    ],
                    "exclude": [
                        "detailed_financials", "personal", "health",
                        "family", "anxiety_notes",
                    ],
                },
                "conditions": {},
            },
        ],
        "default_action": "deny",
        "audit_log": True,
    }
}

_DEMO_PROTOCOLS = {
    "community_member_a": COMMUNITY_MEMBER_A_PROTOCOL,
    "elder_a": ELDER_A_PROTOCOL,
    "youth_a": YOUTH_A_PROTOCOL,
}


def get_demo_protocols(active_project: Optional[str] = None) -> dict:
    """Return demo protocols for the active project, or empty dict if none match."""
    if active_project == "guardian-demo":
        return _DEMO_PROTOCOLS
    # No protocols for other projects or when no project is active
    return {}


# Keep backwards-compatible alias
DEMO_PROTOCOLS = _DEMO_PROTOCOLS


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

async def load_demo_protocols(db: MemoryDatabase, active_project: Optional[str] = None) -> dict:
    """
    Load demo ambassador protocols into the database.

    Only loads protocols that match the currently active project.
    Called on Guardian project activation (alongside memory bridge sync).

    Returns:
        Stats dict: {"loaded": N, "skipped": N}
    """
    protocols = get_demo_protocols(active_project)
    if not protocols:
        return {"loaded": 0, "skipped": 0, "reason": "no_matching_project"}

    proxy = AmbassadorProxy(db)
    stats = {"loaded": 0, "skipped": 0}

    for entity_id, protocol_data in protocols.items():
        # Check if already loaded
        existing = await proxy.load_protocol(entity_id)
        if existing:
            logger.debug("Ambassador: Protocol already exists for %s, skipping", entity_id)
            stats["skipped"] += 1
            continue

        # Ensure entity exists (demo entities should already be synced via memory bridge)
        entity_exists = await db.fetchone(
            "SELECT id FROM entities WHERE id = ?", (entity_id,)
        )
        if not entity_exists:
            # Create a minimal entity record for the demo persona
            await db.execute(
                """INSERT OR IGNORE INTO entities (id, entity_type, name)
                   VALUES (?, 'person', ?)""",
                (entity_id, protocol_data["ambassador_protocol"]["display_name"].replace("'s Ambassador", "")),
            )

        protocol = AmbassadorProtocol.from_json(entity_id, protocol_data)
        await proxy.save_protocol(protocol, updated_by="demo_loader")
        stats["loaded"] += 1
        logger.info("Ambassador: Loaded demo protocol for %s (%d rules)",
                     entity_id, len(protocol.rules))

    logger.info("Ambassador demo protocols: loaded=%d, skipped=%d",
                 stats["loaded"], stats["skipped"])
    return stats
