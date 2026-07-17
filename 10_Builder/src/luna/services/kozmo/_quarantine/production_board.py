"""
KOZMO Production Board Service — Phase 9

Aggregation and planning layer over LAB briefs.
The board is a VIEW, not a store — it reads from lab/briefs/ YAML files.

Provides: grouping, filtering, dependency checking, AI thread management,
bulk operations, stats aggregation.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from ..lab_pipeline import LabPipelineService
from ..types import ProductionBrief


# =============================================================================
# Status and Priority Config (mirrors frontend)
# =============================================================================

STATUS_ORDER = {
    "idea": 0,
    "planning": 1,
    "rigging": 2,
    "queued": 3,
    "generating": 4,
    "review": 5,
    "approved": 6,
    "locked": 7,
}

PRIORITY_WEIGHT = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

TERMINAL_STATUSES = {"approved", "locked"}


# =============================================================================
# Production Board Service
# =============================================================================


class ProductionBoardService:
    """Aggregation and planning layer over LAB briefs."""

    def __init__(self, lab_service: LabPipelineService):
        self.lab = lab_service

    def get_board(
        self,
        group_by: str = "status",
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all briefs grouped by the specified field.
        Returns { groups: [{ key, briefs }], stats }
        """
        briefs = self.lab.list_briefs(status=status)

        groups: Dict[str, List[ProductionBrief]] = {}
        for brief in briefs:
            key = self._group_key(brief, group_by)
            if isinstance(key, list):
                # Character grouping: brief appears in multiple groups
                for k in key:
                    groups.setdefault(k, []).append(brief)
            else:
                groups.setdefault(key, []).append(brief)

        result_groups = [
            {"key": k, "briefs": [b.model_dump() for b in v]}
            for k, v in groups.items()
        ]

        return {
            "groups": result_groups,
            "stats": self.get_stats(),
        }

    def _group_key(self, brief: ProductionBrief, group_by: str) -> Any:
        """Determine grouping key for a brief."""
        if group_by == "status":
            return brief.status
        elif group_by == "priority":
            return brief.priority
        elif group_by == "assignee":
            return brief.assignee or "unassigned"
        elif group_by == "character":
            return brief.characters if brief.characters else ["untagged"]
        elif group_by == "type":
            return brief.type
        else:
            return "all"

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate stats."""
        briefs = self.lab.list_briefs()
        by_status: Dict[str, int] = {}
        total_shots = 0
        blocking_count = 0

        for brief in briefs:
            by_status[brief.status] = by_status.get(brief.status, 0) + 1
            if brief.shots:
                total_shots += len(brief.shots)
            else:
                total_shots += 1
            if brief.dependencies:
                deps_met = self._check_deps_met(brief, briefs)
                if not deps_met:
                    blocking_count += 1

        return {
            "total_briefs": len(briefs),
            "total_shots": total_shots,
            "by_status": by_status,
            "blocking_count": blocking_count,
        }

    def _check_deps_met(
        self, brief: ProductionBrief, all_briefs: List[ProductionBrief]
    ) -> bool:
        """Check if all dependencies of a brief are in a terminal status."""
        briefs_by_id = {b.id: b for b in all_briefs}
        for dep_id in brief.dependencies:
            dep = briefs_by_id.get(dep_id)
            if dep is None or dep.status not in TERMINAL_STATUSES:
                return False
        return True

    def check_dependencies(self, brief_id: str) -> Dict[str, Any]:
        """
        Check if all dependencies are met.
        Returns { can_proceed, blocking, blocked_by_this }
        """
        briefs = self.lab.list_briefs()
        briefs_by_id = {b.id: b for b in briefs}
        brief = briefs_by_id.get(brief_id)
        if brief is None:
            return {"can_proceed": False, "blocking": [], "blocked_by_this": []}

        blocking = []
        for dep_id in brief.dependencies:
            dep = briefs_by_id.get(dep_id)
            if dep and dep.status not in TERMINAL_STATUSES:
                blocking.append({
                    "id": dep.id,
                    "title": dep.title,
                    "status": dep.status,
                })

        blocked_by_this = []
        for b in briefs:
            if brief_id in b.dependencies:
                blocked_by_this.append({
                    "id": b.id,
                    "title": b.title,
                    "status": b.status,
                })

        return {
            "can_proceed": len(blocking) == 0,
            "blocking": blocking,
            "blocked_by_this": blocked_by_this,
        }

    def push_ready_to_lab(self) -> List[str]:
        """
        Find all briefs with status=rigging and met dependencies.
        Advance them to queued. Returns list of brief IDs pushed.
        """
        briefs = self.lab.list_briefs()
        pushed = []
        for brief in briefs:
            if brief.status != "rigging":
                continue
            if self._check_deps_met(brief, briefs):
                self.lab.update_brief(brief.id, {"status": "queued"})
                pushed.append(brief.id)
        return pushed

    def add_to_thread(self, brief_id: str, role: str, text: str) -> Optional[List[dict]]:
        """Add a message to a brief's AI thread."""
        brief = self.lab.get_brief(brief_id)
        if brief is None:
            return None
        thread = brief.ai_thread or []
        thread.append({
            "role": role,
            "text": text,
            "time": datetime.now().strftime("%-I:%M %p"),
        })
        self.lab.update_brief(brief_id, {"ai_thread": thread})
        return thread

    def get_thread(self, brief_id: str) -> Optional[List[dict]]:
        """Get the AI thread for a brief."""
        brief = self.lab.get_brief(brief_id)
        if brief is None:
            return None
        return brief.ai_thread or []

    def bulk_update(
        self, brief_ids: List[str], updates: dict
    ) -> List[ProductionBrief]:
        """Bulk update multiple briefs."""
        results = []
        for bid in brief_ids:
            updated = self.lab.update_brief(bid, updates)
            if updated:
                results.append(updated)
        return results
