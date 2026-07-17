"""
LibraryResearchSkill — first companion-facing research skill.

Searches Luna's document library (AiBrarian / dataroom collections) and
returns a grounded research brief with provenance. Text-first: the narratable
result lives in `result_str`; structured rows live in `data` for future UI.

Backend: dataroom_search() fans out across connected AiBrarian collections
and falls back to MemoryMatrix DOCUMENT nodes. No nexus plumbing, no web
search, no widget geometry.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)


class LibraryResearchSkill(Skill):
    name = "library_research"
    description = "Grounded retrieval over Luna's document library"
    triggers: list[str] = []  # detector owns the canonical pattern set

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._max_results = int(self._config.get("max_results", 3))
        self._max_excerpt_chars = int(self._config.get("max_excerpt_chars", 500))
        self._search_type = self._config.get("search_type", "hybrid")

    def is_available(self) -> bool:
        try:
            from luna.tools.dataroom_tools import dataroom_search  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        cleaned = (query or "").strip()
        if not cleaned:
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="Empty research query",
            )

        try:
            from luna.tools.dataroom_tools import dataroom_search
        except ImportError as e:
            logger.warning("[LIBRARY_RESEARCH] dataroom_tools import failed: %s", e)
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="dataroom_tools unavailable",
            )

        try:
            raw_results = await dataroom_search(
                query=cleaned,
                search_type=self._search_type,
                limit=max(self._max_results * 2, 6),
            )
        except Exception as e:
            logger.warning("[LIBRARY_RESEARCH] dataroom_search failed: %s", e)
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error=f"library backend failed: {e}",
            )

        usable = _filter_usable(raw_results)
        if not usable:
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="No relevant library results found",
            )

        top = usable[: self._max_results]
        normalized = [self._normalize(r) for r in top]
        result_str = self._render_brief(cleaned, normalized)

        return SkillResult(
            success=True,
            skill_name=self.name,
            fallthrough=False,
            result=normalized,
            result_str=result_str,
            data={
                "query": cleaned,
                "results": normalized,
                "citation_count": len(normalized),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        excerpt = raw.get("content") or raw.get("snippet") or ""
        if len(excerpt) > self._max_excerpt_chars:
            excerpt = excerpt[: self._max_excerpt_chars].rstrip() + "…"
        return {
            "name": raw.get("name") or raw.get("title") or raw.get("id", "untitled"),
            "collection": raw.get("collection") or "",
            "category": raw.get("category") or "",
            "score": float(raw.get("score") or 0),
            "excerpt": excerpt,
            "search_type": raw.get("search_type") or self._search_type,
        }

    def _render_brief(self, query: str, rows: list[dict[str, Any]]) -> str:
        lines = [f"Library research — query: {query!r}", ""]
        for i, r in enumerate(rows, start=1):
            origin_bits = [b for b in (r["collection"], r["category"]) if b]
            origin = f" [{' / '.join(origin_bits)}]" if origin_bits else ""
            lines.append(f"{i}. {r['name']}{origin} (score={r['score']:.2f})")
            if r["excerpt"]:
                lines.append(f"   {r['excerpt']}")
            lines.append("")
        lines.append(
            f"Cite these sources by name when narrating. {len(rows)} result(s) shown."
        )
        return "\n".join(lines).rstrip()

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "Ground your answer in these library results. Cite sources by name. "
            "If a detail isn't in the excerpts, say so rather than inferring."
        )


def _filter_usable(raw: list[Any]) -> list[dict[str, Any]]:
    """Drop error rows and obvious non-hits from a dataroom_search response."""
    if not raw:
        return []
    usable: list[dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        if r.get("error"):
            continue
        if not (r.get("name") or r.get("title") or r.get("id")):
            continue
        usable.append(r)
    return usable
