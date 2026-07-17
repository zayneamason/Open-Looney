"""DiagnosticSkill — wraps diagnostics/health.py for system health checks."""

import logging
from ..base import Skill, SkillResult

logger = logging.getLogger(__name__)


class DiagnosticSkill(Skill):
    name = "diagnostic"
    description = "System health diagnostics"
    triggers = [
        r"\b(health check|system status|diagnostics?)\b",
        r"\b(how('?s| is).{0,10}(memory|database|system|engine))\b",
        r"\b(is.{0,10}(everything|system|database).{0,10}(okay|working|broken))\b",
    ]

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._include_metrics = self._config.get("include_metrics", True)

    def is_available(self) -> bool:
        try:
            from luna.diagnostics.health import HealthChecker  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(self, query: str, context: dict) -> SkillResult:
        try:
            from luna.diagnostics.health import HealthChecker
        except ImportError:
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error="health module not available",
            )

        try:
            checker = HealthChecker()
            checks = checker.check_all()

            components = []
            for check in checks:
                entry = {
                    "name": check.component,
                    "status": check.status.value,
                    "message": check.message,
                }
                if self._include_metrics and check.metrics:
                    entry["metrics"] = check.metrics
                components.append(entry)

            # Determine overall status
            statuses = [c["status"] for c in components]
            if "broken" in statuses:
                overall = "broken"
            elif "degraded" in statuses:
                overall = "degraded"
            elif all(s == "healthy" for s in statuses):
                overall = "healthy"
            else:
                overall = "unknown"

            # Build narration string
            lines = [f"Overall: {overall}"]
            for c in components:
                lines.append(f"  {c['name']}: {c['status']} — {c['message']}")
            result_str = "\n".join(lines)

            return SkillResult(
                success=True,
                skill_name=self.name,
                result=components,
                result_str=result_str,
                data={
                    "components": components,
                    "overall": overall,
                },
            )
        except Exception as e:
            logger.warning(f"[DIAGNOSTIC] Health check failed: {e}")
            return SkillResult(
                success=False, skill_name=self.name,
                fallthrough=True, error=str(e),
            )

    def narration_hint(self, result: SkillResult) -> str:
        return (
            "Luna reports her own system health. First-person: "
            "'my memory matrix...', 'I'm seeing...'. Direct about problems."
        )
