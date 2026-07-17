"""TopologyAttachmentService — manual-first / owner-driven attachment policy.

Policy layer over ``TopologyClusterStore``. The substrate stays permissive
(many-to-many, no cluster pre-check); this service enforces the initial
write semantics: one thread → at most one topology cluster, explicit
service-call only, no silent move or replace.

See Docs/bible/Handoffs/HANDOFF_ATTACH_THREADS_TO_TOPOLOGY_CLUSTERS.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from .store import TopologyClusterStore

if TYPE_CHECKING:
    from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


class ThreadAttachmentStatus(str, Enum):
    ATTACHED = "attached"
    ALREADY_ATTACHED_SAME = "already_attached_same"
    ALREADY_ATTACHED_OTHER = "already_attached_other"
    MISSING_THREAD = "missing_thread"
    MISSING_CLUSTER = "missing_cluster"


@dataclass
class ThreadAttachmentResult:
    status: ThreadAttachmentStatus
    thread_id: str
    cluster_id: Optional[str]
    existing_cluster_id: Optional[str] = None
    relationship: str = "member"


class TopologyAttachmentService:
    """Conservative attachment policy layered on ``TopologyClusterStore``.

    The store remains permissive at the substrate layer; this service
    enforces the single-cluster-per-thread rule for the only allowed
    write path.
    """

    def __init__(self, db: "MemoryDatabase") -> None:
        self._db = db
        self._store = TopologyClusterStore(db)

    async def attach_thread_manual(
        self,
        cluster_id: str,
        thread_id: str,
        relationship: str = "member",
    ) -> ThreadAttachmentResult:
        # Rule 1: cluster must exist.
        cluster = await self._store.get_cluster(cluster_id)
        if cluster is None:
            return ThreadAttachmentResult(
                status=ThreadAttachmentStatus.MISSING_CLUSTER,
                thread_id=thread_id,
                cluster_id=None,
                relationship=relationship,
            )

        # Rule 2: thread must exist in the canonical `threads` table.
        thread_row = await self._db.fetchone(
            "SELECT 1 FROM threads WHERE id = ?",
            (thread_id,),
        )
        if thread_row is None:
            return ThreadAttachmentResult(
                status=ThreadAttachmentStatus.MISSING_THREAD,
                thread_id=thread_id,
                cluster_id=cluster_id,
                relationship=relationship,
            )

        # Rules 3 + 4: inspect existing memberships before writing.
        existing = await self._store.find_clusters_for_thread(thread_id)
        if existing:
            for membership in existing:
                if membership.cluster_id == cluster_id:
                    return ThreadAttachmentResult(
                        status=ThreadAttachmentStatus.ALREADY_ATTACHED_SAME,
                        thread_id=thread_id,
                        cluster_id=cluster_id,
                        existing_cluster_id=cluster_id,
                        relationship=relationship,
                    )
            return ThreadAttachmentResult(
                status=ThreadAttachmentStatus.ALREADY_ATTACHED_OTHER,
                thread_id=thread_id,
                cluster_id=cluster_id,
                existing_cluster_id=existing[0].cluster_id,
                relationship=relationship,
            )

        # Rule 5: fresh attach.
        await self._store.attach_thread(cluster_id, thread_id, relationship)
        return ThreadAttachmentResult(
            status=ThreadAttachmentStatus.ATTACHED,
            thread_id=thread_id,
            cluster_id=cluster_id,
            relationship=relationship,
        )
