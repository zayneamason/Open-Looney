"""
MemoryRecallSkill — Phase B companion skill for source-aware recall.

Answers "what do you know about X?" by pulling relevant MemoryNodes through
the live MatrixActor's canonical `get_context()` retrieval path. Renders a
text-first brief that Director injects into the system prompt so Luna can
narrate the recall in her voice.

Fails soft when the Matrix is unavailable or returns thin results — the
skill biases toward honesty over recall theater.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)


class MemoryRecallSkill(Skill):
    name = "memory_recall"
    description = "Source-aware recall from Luna's Matrix"
    triggers: list[str] = []  # canonical patterns live in detector.py

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._max_results = int(self._config.get("max_results", 4))
        self._max_excerpt_chars = int(self._config.get("max_excerpt_chars", 400))
        self._max_tokens = int(self._config.get("max_tokens", 1200))

    def is_available(self) -> bool:
        # MatrixActor availability is checked per-execution; the skill itself
        # is always registrable. Returning True keeps hot-reload simple.
        return True

    async def execute(self, query: str, context: dict) -> SkillResult:
        cleaned = (query or "").strip()
        if not cleaned:
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="Empty recall query",
            )

        matrix_actor = (context or {}).get("matrix_actor")
        if matrix_actor is None or getattr(matrix_actor, "_matrix", None) is None:
            logger.debug("[MEMORY_RECALL] matrix_actor missing from skill context")
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="Matrix substrate unavailable",
            )

        try:
            nodes = await matrix_actor._matrix.get_context(
                cleaned, max_tokens=self._max_tokens
            )
        except Exception as e:
            logger.warning("[MEMORY_RECALL] get_context failed: %s", e)
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error=f"recall backend failed: {e}",
            )

        usable = _filter_usable(nodes)
        if not usable:
            return SkillResult(
                success=False,
                skill_name=self.name,
                fallthrough=True,
                error="No relevant memories found",
            )

        top = usable[: self._max_results]
        normalized = [self._normalize(n) for n in top]
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
                "recall_count": len(normalized),
            },
        )

    # ---------------------------------------------------------------- helpers

    def _normalize(self, node: Any) -> dict[str, Any]:
        content = getattr(node, "content", "") or ""
        excerpt = content.strip()
        if len(excerpt) > self._max_excerpt_chars:
            excerpt = excerpt[: self._max_excerpt_chars].rstrip() + "…"
        summary = (getattr(node, "summary", "") or "").strip()
        if not summary and content:
            summary = content.strip().split("\n", 1)[0][:80]
        return {
            "id": getattr(node, "id", ""),
            "type": getattr(node, "node_type", "UNKNOWN"),
            "source": getattr(node, "source", None) or "unknown",
            "summary": summary or None,
            "excerpt": excerpt,
            "confidence": float(getattr(node, "confidence", 1.0) or 1.0),
            "importance": float(getattr(node, "importance", 0.5) or 0.5),
            "scope": getattr(node, "scope", "global") or "global",
        }

    def _render_brief(self, query: str, rows: list[dict[str, Any]]) -> str:
        lines = [f"Memory recall — query: {query!r}", ""]
        for i, r in enumerate(rows, start=1):
            origin = f"[{r['source']} / {r['type']}]"
            header = r["summary"] or r["excerpt"][:60]
            lines.append(f"{i}. {header} {origin}")
            if r["excerpt"] and r["excerpt"] != header:
                lines.append(f"   {r['excerpt']}")
            lines.append("")
        lines.append(
            f"Cite these as remembered context, not fresh document research. "
            f"{len(rows)} memory result(s) shown."
        )
        return "\n".join(lines).rstrip()

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "Treat these as remembered context. Summarize what you recall "
            "and mention uncertainty if the memory is thin or indirect. "
            "Cite sources briefly when helpful; do not narrate as fresh research."
        )


def _filter_usable(nodes: Iterable[Any] | None) -> list[Any]:
    """Drop empty/malformed nodes; accept any object quacking like MemoryNode."""
    if not nodes:
        return []
    usable: list[Any] = []
    for n in nodes:
        nid = getattr(n, "id", None)
        content = getattr(n, "content", "") or ""
        if not nid or not content.strip():
            continue
        usable.append(n)
    return usable
