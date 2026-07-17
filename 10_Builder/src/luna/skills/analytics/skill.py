"""AnalyticsSkill — Luna's own memory analytics via sqlite3."""

import re
import logging
import sqlite3
from pathlib import Path
from luna.core.paths import memory_matrix_path
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)

SELF_ANALYTICS_QUERIES = {
    "memory_summary": {
        "sql": "SELECT node_type, COUNT(*) as cnt FROM memory_nodes GROUP BY node_type ORDER BY cnt DESC",
        "title": "Memory Node Distribution",
        "chart_type": "bar",
    },
    "session_stats": {
        "sql": "SELECT COUNT(*) as sessions, CAST(AVG(turns_count) AS INTEGER) as avg_turns FROM sessions",
        "title": "Session Statistics",
        "chart_type": "bar",
    },
    "entity_count": {
        "sql": "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type ORDER BY cnt DESC",
        "title": "Entity Distribution",
        "chart_type": "bar",
    },
    "lock_in_distribution": {
        "sql": "SELECT lock_in_state, COUNT(*) as cnt FROM memory_nodes GROUP BY lock_in_state ORDER BY cnt DESC",
        "title": "Lock-in State Distribution",
        "chart_type": "bar",
    },
}


def _detect_query_type(query: str) -> str:
    """Map user query to an analytics query type."""
    q = query.lower()
    if re.search(r"\b(memor|node)", q):
        return "memory_summary"
    if re.search(r"\b(session|conversation|turn)", q):
        return "session_stats"
    if re.search(r"\b(entit|person|people)", q):
        return "entity_count"
    if re.search(r"\b(lock.?in|state)", q):
        return "lock_in_distribution"
    # Default: memory summary
    return "memory_summary"


class AnalyticsSkill(Skill):
    name = "analytics"
    description = "Luna's memory analytics"
    triggers = [
        r"\b(how many|count).{0,30}\b(memories|nodes|sessions|entities)\b",
        r"\b(memory|session|delegation).{0,10}(stats|statistics|summary|overview)\b",
        r"\b(analyze|analyse)\b.{0,20}\bdata\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._max_rows = self._config.get("max_rows_display", 20)

    def is_available(self) -> bool:
        return memory_matrix_path().exists()

    async def execute(self, query: str, context: dict) -> SkillResult:
        db_path = str(memory_matrix_path())
        if not Path(db_path).exists():
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="Database not found",
            )

        query_type = _detect_query_type(query)
        spec = SELF_ANALYTICS_QUERIES.get(query_type)
        if not spec:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=f"Unknown analytics type: {query_type}",
            )

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout=15000")
            cursor = conn.cursor()
            cursor.execute(spec["sql"])
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            conn.close()

            if not rows:
                return SkillResult(
                    success=True, skill_name=self.name,
                    result=[], result_str="No data found",
                    data={
                        "chart_type": spec["chart_type"],
                        "labels": [],
                        "values": [],
                        "title": spec["title"],
                        "raw": {},
                    },
                )

            # For 2-column results (label, value), build chart data
            if len(columns) == 2:
                labels = [str(r[0]) for r in rows[:self._max_rows]]
                values = [r[1] for r in rows[:self._max_rows]]
            else:
                # For single-row aggregate results, use column names as labels
                labels = columns
                values = list(rows[0])

            # Build result string
            lines = [f"{spec['title']}:"]
            for label, value in zip(labels, values):
                lines.append(f"  {label}: {value:,}" if isinstance(value, (int, float)) else f"  {label}: {value}")
            result_str = "\n".join(lines)

            return SkillResult(
                success=True,
                skill_name=self.name,
                result=rows,
                result_str=result_str,
                data={
                    "chart_type": spec["chart_type"],
                    "labels": labels,
                    "values": values,
                    "title": spec["title"],
                    "raw": {col: [r[i] for r in rows] for i, col in enumerate(columns)},
                },
            )

        except Exception as e:
            logger.warning(f"[ANALYTICS] Query failed: {e}")
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "Analytics data is shown in a chart below. "
            "Narrate the key insight briefly — what's the most interesting pattern? "
            "Ask if the user wants to dig deeper."
        )
