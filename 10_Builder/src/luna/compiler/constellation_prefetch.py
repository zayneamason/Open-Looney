"""
Constellation Prefetch
======================

Pre-fetches compiled PERSON_BRIEFING, PROJECT_STATUS, and
GOVERNANCE_RECORD nodes before general Matrix search runs.

Constellations are not search results — they are pre-compiled
knowledge packets injected first, not competing with search.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .entity_index import EntityIndex

logger = logging.getLogger(__name__)

# Keywords that signal project-level or governance-level queries
PROJECT_SIGNALS = {
    "project", "status", "grant", "progress",
    "timeline", "plan", "initiative",
}
GOVERNANCE_SIGNALS = {
    "governance", "council", "decision", "olukomera",
    "protocol", "tk", "consent", "sovereignty",
    "elder", "traditional", "policy",
}


@dataclass
class PrefetchResult:
    """Result of constellation prefetch."""
    nodes: list  # MemoryNode instances
    tokens_used: int = 0
    entity_ids: list[str] = field(default_factory=list)
    constellation_types: list[str] = field(default_factory=list)


class ConstellationPrefetch:
    """
    Pre-fetches compiled constellation nodes for entity queries.

    Runs before general Matrix search. If the query mentions a known
    entity, fetches PERSON_BRIEFING directly. Also checks for
    PROJECT_STATUS and GOVERNANCE_RECORD on relevant queries.
    """

    def __init__(self, matrix_actor, entity_index: EntityIndex):
        self.matrix_actor = matrix_actor
        self.entity_index = entity_index

    async def prefetch(
        self,
        query: str,
        scope: Optional[str] = None,
    ) -> PrefetchResult:
        """
        Check if query matches compiled constellations.

        Returns matching constellation nodes with token estimates.
        Caller subtracts tokens_used from general search budget.
        """
        result = PrefetchResult(nodes=[])

        # 1. Detect entities in query
        entity_ids = self.entity_index.resolve_mentions(query)
        logger.info(
            f"Constellation prefetch: query='{query}', "
            f"entities={entity_ids}, scope={scope}"
        )

        # 2. Fetch PERSON_BRIEFING for matched entities
        for eid in entity_ids:
            briefing = await self._fetch_by_type_and_tag(
                "PERSON_BRIEFING", eid, scope
            )
            if briefing:
                result.nodes.extend(briefing)
                result.entity_ids.append(eid)
                if "PERSON_BRIEFING" not in result.constellation_types:
                    result.constellation_types.append("PERSON_BRIEFING")

        # 3. Check PROJECT_STATUS if query signals project intent
        if self._has_signal(query, PROJECT_SIGNALS):
            project_nodes = await self._fetch_by_type(
                "PROJECT_STATUS", scope
            )
            if project_nodes:
                result.nodes.extend(project_nodes)
                if "PROJECT_STATUS" not in result.constellation_types:
                    result.constellation_types.append("PROJECT_STATUS")

        # 4. Check GOVERNANCE_RECORD if query signals governance intent
        if self._has_signal(query, GOVERNANCE_SIGNALS):
            gov_nodes = await self._fetch_by_type(
                "GOVERNANCE_RECORD", scope
            )
            if gov_nodes:
                result.nodes.extend(gov_nodes)
                if "GOVERNANCE_RECORD" not in result.constellation_types:
                    result.constellation_types.append("GOVERNANCE_RECORD")

        # 5. Estimate token usage
        result.tokens_used = sum(
            self._estimate_tokens(n) for n in result.nodes
        )

        if result.nodes:
            logger.info(
                f"Constellation prefetch: {len(result.nodes)} nodes, "
                f"~{result.tokens_used} tokens, "
                f"types={result.constellation_types}, "
                f"entities={result.entity_ids}"
            )

        return result

    async def _fetch_by_type_and_tag(
        self, node_type: str, entity_tag: str, scope: Optional[str]
    ) -> list:
        """Fetch nodes by type where tags contain entity ID."""
        try:
            memory = self.matrix_actor._matrix
            db = memory.db

            if scope:
                rows = await db.fetchall(
                    """SELECT id FROM memory_nodes
                       WHERE node_type = ? AND scope = ?
                       AND tags LIKE ?""",
                    (node_type, scope, f'%{entity_tag}%'),
                )
            else:
                rows = await db.fetchall(
                    """SELECT id FROM memory_nodes
                       WHERE node_type = ?
                       AND tags LIKE ?""",
                    (node_type, f'%{entity_tag}%'),
                )

            logger.info(
                f"Prefetch lookup: type={node_type}, tag={entity_tag}, "
                f"scope={scope}, rows={len(rows)}"
            )

            nodes = []
            for row in rows:
                node = await memory.get_node(row[0])
                if node:
                    nodes.append(node)
            return nodes
        except Exception as e:
            logger.debug(f"Constellation prefetch lookup failed: {e}")
            return []

    async def _fetch_by_type(
        self, node_type: str, scope: Optional[str]
    ) -> list:
        """Fetch all nodes of a given type in scope."""
        try:
            memory = self.matrix_actor._matrix
            db = memory.db

            if scope:
                rows = await db.fetchall(
                    "SELECT id FROM memory_nodes WHERE node_type = ? AND scope = ?",
                    (node_type, scope),
                )
            else:
                rows = await db.fetchall(
                    "SELECT id FROM memory_nodes WHERE node_type = ?",
                    (node_type,),
                )

            nodes = []
            for row in rows:
                node = await memory.get_node(row[0])
                if node:
                    nodes.append(node)
            return nodes
        except Exception as e:
            logger.debug(f"Constellation type lookup failed: {e}")
            return []

    def _has_signal(self, query: str, signals: set[str]) -> bool:
        """Check if query contains any signal words."""
        words = set(query.lower().split())
        return bool(words & signals)

    def _estimate_tokens(self, node) -> int:
        """Rough token estimate: len/4."""
        content = getattr(node, "content", "")
        return len(content) // 4
