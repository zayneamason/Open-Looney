"""TopologyRuleAttachmentService — deterministic automatic attachment rules.

Rules in this slice:

- ``project_slug_exact`` — if the active thread carries a ``project_slug`` and
  exactly one topology cluster has ``metadata.project_slug`` equal to it,
  attach via the existing manual ``TopologyAttachmentService``
- ``anchor_entity_id_exact`` — if exactly one topology cluster has
  ``metadata.anchor_entity_id`` equal to one of the thread's canonical
  ``entity_node_ids``, attach via the same manual policy service

Non-fatal by design: the caller is Librarian's turn loop, so missing
canonical ``threads`` rows, ambiguous matches, and no matches all return
``SKIPPED_*`` instead of raising. ``TopologyAttachmentService.MISSING_THREAD``
is remapped to ``SKIPPED_MISSING_CANONICAL_THREAD`` because canonical thread
backfill is asynchronous with live thread creation.

See Docs/bible/Handoffs/HANDOFF_ADD_RULE_DRIVEN_TOPOLOGY_ATTACHMENT.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from .attachment_service import (
    ThreadAttachmentStatus,
    TopologyAttachmentService,
)
from .store import TopologyClusterStore

if TYPE_CHECKING:
    from luna.extraction.types import Thread
    from luna.substrate.database import MemoryDatabase

logger = logging.getLogger(__name__)


RULE_PROJECT_SLUG_EXACT = "project_slug_exact"
RULE_ANCHOR_ENTITY_ID_EXACT = "anchor_entity_id_exact"


class RuleDrivenAttachmentStatus(str, Enum):
    ATTACHED = "attached"
    ALREADY_ATTACHED_SAME = "already_attached_same"
    ALREADY_ATTACHED_OTHER = "already_attached_other"
    SKIPPED_NO_PROJECT_SLUG = "skipped_no_project_slug"
    SKIPPED_NO_ENTITY_IDS = "skipped_no_entity_ids"
    SKIPPED_NO_MATCH = "skipped_no_match"
    SKIPPED_AMBIGUOUS_MATCH = "skipped_ambiguous_match"
    SKIPPED_MISSING_CANONICAL_THREAD = "skipped_missing_canonical_thread"


@dataclass
class RuleDrivenAttachmentResult:
    status: RuleDrivenAttachmentStatus
    rule_name: str
    thread_id: str
    project_slug: Optional[str]
    cluster_id: Optional[str] = None
    existing_cluster_id: Optional[str] = None
    matched_cluster_ids: list[str] = field(default_factory=list)
    anchor_entity_id: Optional[str] = None


class TopologyRuleAttachmentService:
    """Automatic attachment driven by deterministic rules.

    Every result path returns a structured ``RuleDrivenAttachmentResult``;
    no branch raises. The caller is expected to be Librarian's turn loop.
    """

    def __init__(self, db: "MemoryDatabase") -> None:
        self._db = db
        self._store = TopologyClusterStore(db)
        self._manual = TopologyAttachmentService(db)

    async def _has_canonical_thread(self, thread_id: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM threads WHERE id = ?",
            (thread_id,),
        )
        return row is not None

    def _map_manual_result(
        self,
        *,
        rule: str,
        thread_id: str,
        project_slug: Optional[str],
        target_cluster_id: str,
        matched_cluster_ids: list[str],
        manual_result: "ThreadAttachmentResult",
        anchor_entity_id: Optional[str] = None,
    ) -> RuleDrivenAttachmentResult:
        if manual_result.status == ThreadAttachmentStatus.ATTACHED:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.ATTACHED,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                cluster_id=target_cluster_id,
                matched_cluster_ids=matched_cluster_ids,
                anchor_entity_id=anchor_entity_id,
            )
        if manual_result.status == ThreadAttachmentStatus.ALREADY_ATTACHED_SAME:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.ALREADY_ATTACHED_SAME,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                cluster_id=target_cluster_id,
                existing_cluster_id=target_cluster_id,
                matched_cluster_ids=matched_cluster_ids,
                anchor_entity_id=anchor_entity_id,
            )
        if manual_result.status == ThreadAttachmentStatus.ALREADY_ATTACHED_OTHER:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.ALREADY_ATTACHED_OTHER,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                cluster_id=target_cluster_id,
                existing_cluster_id=manual_result.existing_cluster_id,
                matched_cluster_ids=matched_cluster_ids,
                anchor_entity_id=anchor_entity_id,
            )
        if manual_result.status == ThreadAttachmentStatus.MISSING_THREAD:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_MISSING_CANONICAL_THREAD,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                matched_cluster_ids=matched_cluster_ids,
                anchor_entity_id=anchor_entity_id,
            )

        logger.warning(
            f"[TOPOLOGY] rule={rule} unexpected manual status "
            f"{manual_result.status!r}; soft-skipping"
        )
        return RuleDrivenAttachmentResult(
            status=RuleDrivenAttachmentStatus.SKIPPED_NO_MATCH,
            rule_name=rule,
            thread_id=thread_id,
            project_slug=project_slug,
            matched_cluster_ids=matched_cluster_ids,
            anchor_entity_id=anchor_entity_id,
        )

    async def attach_thread_by_project_slug_rule(
        self,
        thread: "Thread",
    ) -> RuleDrivenAttachmentResult:
        rule = RULE_PROJECT_SLUG_EXACT
        thread_id = thread.id
        project_slug = getattr(thread, "project_slug", None)

        # Rule 1: skip if no project_slug.
        if not project_slug:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_NO_PROJECT_SLUG,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=None,
            )

        # Rule 2: skip if canonical threads row is missing (soft, expected).
        if not await self._has_canonical_thread(thread_id):
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_MISSING_CANONICAL_THREAD,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
            )

        # Rules 3-4: load clusters and filter by metadata.project_slug.
        clusters = await self._store.list_clusters()
        matches = [
            c for c in clusters
            if c.metadata.get("project_slug") == project_slug
        ]

        # Rule 5: skip if no match.
        if not matches:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_NO_MATCH,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
            )

        # Rule 6: skip if ambiguous.
        if len(matches) > 1:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_AMBIGUOUS_MATCH,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                matched_cluster_ids=[c.cluster_id for c in matches],
            )

        # Rule 7: exactly one match — attempt attach via manual policy.
        target = matches[0]
        manual_result = await self._manual.attach_thread_manual(
            target.cluster_id, thread_id
        )

        return self._map_manual_result(
            rule=rule,
            thread_id=thread_id,
            project_slug=project_slug,
            target_cluster_id=target.cluster_id,
            matched_cluster_ids=[target.cluster_id],
            manual_result=manual_result,
        )

    async def attach_thread_by_anchor_entity_rule(
        self,
        thread: "Thread",
    ) -> RuleDrivenAttachmentResult:
        rule = RULE_ANCHOR_ENTITY_ID_EXACT
        thread_id = thread.id
        project_slug = getattr(thread, "project_slug", None)

        if not await self._has_canonical_thread(thread_id):
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_MISSING_CANONICAL_THREAD,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
            )

        raw_entity_ids = getattr(thread, "entity_node_ids", None) or []
        entity_ids: list[str] = []
        seen: set[str] = set()
        for entity_id in raw_entity_ids:
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            entity_ids.append(entity_id)

        if not entity_ids:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_NO_ENTITY_IDS,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
            )

        clusters = await self._store.list_clusters()
        matches = [
            c for c in clusters
            if c.metadata.get("anchor_entity_id") in entity_ids
        ]

        if not matches:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_NO_MATCH,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
            )

        if len(matches) > 1:
            return RuleDrivenAttachmentResult(
                status=RuleDrivenAttachmentStatus.SKIPPED_AMBIGUOUS_MATCH,
                rule_name=rule,
                thread_id=thread_id,
                project_slug=project_slug,
                matched_cluster_ids=[c.cluster_id for c in matches],
            )

        target = matches[0]
        anchor_entity_id = target.metadata.get("anchor_entity_id")
        manual_result = await self._manual.attach_thread_manual(
            target.cluster_id, thread_id
        )
        return self._map_manual_result(
            rule=rule,
            thread_id=thread_id,
            project_slug=project_slug,
            target_cluster_id=target.cluster_id,
            matched_cluster_ids=[target.cluster_id],
            manual_result=manual_result,
            anchor_entity_id=anchor_entity_id,
        )

    async def attach_thread_by_rules(
        self,
        thread: "Thread",
    ) -> RuleDrivenAttachmentResult:
        first = await self.attach_thread_by_project_slug_rule(thread)
        if first.status in {
            RuleDrivenAttachmentStatus.ATTACHED,
            RuleDrivenAttachmentStatus.ALREADY_ATTACHED_SAME,
            RuleDrivenAttachmentStatus.ALREADY_ATTACHED_OTHER,
            RuleDrivenAttachmentStatus.SKIPPED_AMBIGUOUS_MATCH,
            RuleDrivenAttachmentStatus.SKIPPED_MISSING_CANONICAL_THREAD,
        }:
            return first

        if first.status in {
            RuleDrivenAttachmentStatus.SKIPPED_NO_PROJECT_SLUG,
            RuleDrivenAttachmentStatus.SKIPPED_NO_MATCH,
        }:
            return await self.attach_thread_by_anchor_entity_rule(thread)

        return first
