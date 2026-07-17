"""Topology substrate package.

Persistent topology storage (slice 1), manual-first attachment policy
(slice 2), and first rule-driven automatic attachment (slice 4). See:

- Docs/bible/Handoffs/HANDOFF_ADD_TOPOLOGY_CLUSTER_SUBSTRATE.md
- Docs/bible/Handoffs/HANDOFF_ATTACH_THREADS_TO_TOPOLOGY_CLUSTERS.md
- Docs/bible/Handoffs/HANDOFF_ADD_RULE_DRIVEN_TOPOLOGY_ATTACHMENT.md
"""

from .attachment_service import (
    ThreadAttachmentResult,
    ThreadAttachmentStatus,
    TopologyAttachmentService,
)
from .rule_attachment_service import (
    RULE_ANCHOR_ENTITY_ID_EXACT,
    RULE_PROJECT_SLUG_EXACT,
    RuleDrivenAttachmentResult,
    RuleDrivenAttachmentStatus,
    TopologyRuleAttachmentService,
)
from .store import TopologyCluster, TopologyClusterStore, TopologyClusterThread

__all__ = [
    "RULE_PROJECT_SLUG_EXACT",
    "RULE_ANCHOR_ENTITY_ID_EXACT",
    "RuleDrivenAttachmentResult",
    "RuleDrivenAttachmentStatus",
    "ThreadAttachmentResult",
    "ThreadAttachmentStatus",
    "TopologyAttachmentService",
    "TopologyCluster",
    "TopologyClusterStore",
    "TopologyClusterThread",
    "TopologyRuleAttachmentService",
]
