"""
QAValidator — Validates inferences against assertions.
=======================================================

The central engine that:
1. Runs all assertions against an InferenceContext
2. Generates actionable diagnosis for failures
3. Stores reports to database
4. Tracks health metrics
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .context import InferenceContext
from .assertions import Assertion, AssertionResult, PatternConfig, get_default_assertions
from .database import QADatabase

logger = logging.getLogger(__name__)


@dataclass
class QAReport:
    """Full QA report for an inference."""
    inference_id: str
    timestamp: datetime
    query: str
    route: str
    provider_used: str
    latency_ms: float
    assertions: list[AssertionResult]
    diagnosis: Optional[str]
    context: InferenceContext

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.assertions)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.assertions if not a.passed)

    @property
    def failed_assertions(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]

    @property
    def critical_failures(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed and a.severity == "critical"]

    @property
    def high_failures(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed and a.severity == "high"]

    def to_dict(self) -> dict:
        return {
            "inference_id": self.inference_id,
            "timestamp": self.timestamp.isoformat(),
            "query": self.query,
            "route": self.route,
            "provider_used": self.provider_used,
            "latency_ms": self.latency_ms,
            "passed": self.passed,
            "failed_count": self.failed_count,
            "diagnosis": self.diagnosis,
            "assertions": [
                {
                    "id": a.id,
                    "name": a.name,
                    "passed": a.passed,
                    "severity": a.severity,
                    "expected": a.expected,
                    "actual": a.actual,
                    "details": a.details,
                }
                for a in self.assertions
            ],
            "context": self.context.to_dict(),
        }


class QAValidator:
    """Validates inferences against assertions."""

    def __init__(self, db_path: str = "data/qa.db"):
        self._assertions: list[Assertion] = []
        self._db = QADatabase(db_path)
        self._last_report: Optional[QAReport] = None
        self._recalibrated_at: Optional[datetime] = None

        # Load built-in assertions
        self._assertions = get_default_assertions()

        # Load custom assertions from DB
        self._load_custom_assertions()

        logger.info(f"QAValidator initialized with {len(self._assertions)} assertions")

    def _load_custom_assertions(self):
        """Load user-defined assertions from database."""
        try:
            custom = self._db.get_assertions()
            for a in custom:
                if a.check_type == "pattern":
                    self._assertions.append(a)
            logger.debug(f"Loaded {len(custom)} custom assertions from database")
        except Exception as e:
            logger.warning(f"Failed to load custom assertions: {e}")

    def validate(self, ctx: InferenceContext) -> QAReport:
        """Run all assertions against an inference."""
        results = []

        for assertion in self._assertions:
            if assertion.enabled:
                try:
                    result = assertion.check(ctx)
                    results.append(result)
                except Exception as e:
                    # If assertion crashes, count as failure
                    results.append(AssertionResult(
                        id=assertion.id,
                        name=assertion.name,
                        passed=False,
                        severity=assertion.severity,
                        expected="No exception",
                        actual=f"Error: {e}",
                    ))

        # Generate diagnosis
        diagnosis = self._generate_diagnosis(results, ctx)

        # Build report
        report = QAReport(
            inference_id=ctx.inference_id,
            timestamp=ctx.timestamp,
            query=ctx.query,
            route=ctx.route,
            provider_used=ctx.provider_used,
            latency_ms=ctx.latency_ms,
            assertions=results,
            diagnosis=diagnosis,
            context=ctx,
        )

        # Store to database
        try:
            self._db.store_report(report)
        except Exception as e:
            logger.error(f"Failed to store QA report: {e}")

        self._last_report = report

        # Log summary
        if report.passed:
            logger.debug(f"QA PASSED: {ctx.inference_id} ({len(results)} assertions)")
        else:
            logger.warning(f"QA FAILED: {ctx.inference_id} - {report.failed_count} failures")
            for failure in report.failed_assertions:
                logger.warning(f"  [{failure.severity}] {failure.name}: {failure.actual}")

        return report

    def _generate_diagnosis(self, results: list[AssertionResult], ctx: InferenceContext) -> Optional[str]:
        """Generate actionable diagnosis from failures."""
        failed = [r for r in results if not r.passed]
        if not failed:
            return None

        diagnoses = []

        # Check for voice injection failure (P3)
        voice_failed = any(r.id == "P3" and not r.passed for r in results)
        if voice_failed:
            diagnoses.append(
                "Voice injection missing from system prompt. Luna's voice is injected "
                "via PromptAssembler, not post-hoc narration. Check that <luna_voice> "
                "block or tone directives are present in the assembled prompt."
            )

        # Check for Claude-isms
        claude_isms_failed = any(r.id == "V1" and not r.passed for r in results)
        if claude_isms_failed and not voice_failed:
            diagnoses.append(
                "Claude-isms detected despite voice injection. Personality prompt "
                "may be insufficient or provider is overriding voice directives."
            )
        elif claude_isms_failed and voice_failed:
            diagnoses.append(
                "Claude-isms detected because voice injection is missing."
            )

        # Check for structural issues
        ascii_failed = any(r.id == "S2" and not r.passed for r in results)
        code_failed = any(r.id == "S1" and not r.passed for r in results)
        mermaid_failed = any(r.id == "S3" and not r.passed for r in results)
        bullet_failed = any(r.id == "S4" and not r.passed for r in results)

        if ascii_failed or code_failed or mermaid_failed:
            diagnoses.append(
                "Response contains formatting (ASCII art, code blocks, diagrams) that Luna "
                "shouldn't produce in casual conversation. May indicate raw model output."
            )

        if bullet_failed:
            diagnoses.append(
                "Response has excessive bullet points. Luna should speak in flowing "
                "natural language, not lists."
            )

        # Check for personality issues
        personality_failed = any(r.id == "P1" and not r.passed for r in results)
        if personality_failed:
            diagnoses.append(
                "Personality prompt missing or too short. Check that virtues and "
                "kernel are being loaded into system prompt."
            )

        virtues_failed = any(r.id == "P2" and not r.passed for r in results)
        if virtues_failed and not personality_failed:
            diagnoses.append(
                "Virtues not loaded from memory. Identity buffer may have failed."
            )

        # Check for provider issues
        provider_failed = any(r.id == "F1" and not r.passed for r in results)
        if provider_failed:
            diagnoses.append(
                f"All providers failed. Errors: {ctx.provider_errors}"
            )

        # Check for timeout
        timeout_failed = any(r.id == "F2" and not r.passed for r in results)
        if timeout_failed:
            diagnoses.append(
                f"Response took {ctx.latency_ms:.0f}ms (>30s timeout). "
                "Consider fallback chain or simpler routing."
            )

        # Check for integration issues
        graph_failed = any(r.id == "I1" and not r.passed for r in results)
        if graph_failed:
            # Distinguish cold-start (memory_stats empty) from a real graph emptiness.
            # The two failure modes have completely different fixes.
            if not ctx.memory_stats:
                diagnoses.append(
                    "QA stats collection returned empty memory_stats — likely "
                    "cold-start race after backend restart, or get_stats() raised. "
                    "Check backend log for QA_STATS warnings; the graph itself is "
                    "probably fine."
                )
            else:
                diagnoses.append(
                    "Knowledge graph has zero edges despite stats reporting. "
                    "Check the Librarian filing path — relations may not be "
                    "reaching graph.add_edge()."
                )

        cluster_failed = any(r.id == "I2" and not r.passed for r in results)
        if cluster_failed:
            diagnoses.append(
                "Nearly all clusters are drifting. Memory consolidation pipeline "
                "may not be running. Check _reflective_tick() wiring."
            )

        diversity_failed = any(r.id == "I3" and not r.passed for r in results)
        if diversity_failed:
            diagnoses.append(
                "Over 95% of nodes are FACTs. Extraction may be over-classifying. "
                "Check _wire_extraction() entity resolution logic."
            )

        assistant_failed = any(r.id == "I4" and not r.passed for r in results)
        if assistant_failed:
            diagnoses.append(
                "Assistant content leaked into extraction. Check _end_auto_session() "
                "and luna_end_session() — they must filter to user turns only."
            )

        backend_dead = any(r.id == "E1" and not r.passed for r in results)
        if backend_dead:
            diagnoses.append(
                "Extraction backend appears dead — 0 extractions after 10+ turns. "
                "If using 'local' backend, check that model is loaded. "
                "If model unavailable, switch to 'haiku' backend."
            )

        no_entities = any(r.id == "E2" and not r.passed for r in results)
        if no_entities:
            diagnoses.append(
                "Most extracted objects have empty entity lists. "
                "Graph relationships depend on entities. Check extraction prompt "
                "and _parse_extraction_response() entity parsing."
            )

        return " ".join(diagnoses) if diagnoses else "Unknown failure pattern."

    def add_assertion(self, assertion: Assertion) -> str:
        """Add a new assertion."""
        self._assertions.append(assertion)
        self._db.store_assertion(assertion)
        logger.info(f"Added assertion: {assertion.id} - {assertion.name}")
        return assertion.id

    def get_assertions(self) -> list[Assertion]:
        """Get all assertions."""
        return self._assertions

    def toggle_assertion(self, assertion_id: str, enabled: bool) -> bool:
        """Enable or disable an assertion."""
        for a in self._assertions:
            if a.id == assertion_id:
                a.enabled = enabled
                if a.check_type == "pattern":
                    self._db.update_assertion(a)
                logger.info(f"Assertion {assertion_id} {'enabled' if enabled else 'disabled'}")
                return True
        return False

    def delete_assertion(self, assertion_id: str) -> bool:
        """Delete a custom assertion."""
        # Don't delete built-ins (they have single-letter + number IDs like P1, S2)
        if len(assertion_id) <= 2:
            logger.warning(f"Cannot delete built-in assertion: {assertion_id}")
            return False

        self._assertions = [a for a in self._assertions if a.id != assertion_id]
        self._db.delete_assertion(assertion_id)
        logger.info(f"Deleted assertion: {assertion_id}")
        return True

    def get_last_report(self) -> Optional[QAReport]:
        """Get most recent report."""
        if self._last_report:
            return self._last_report

        # Try to load from database
        report_dict = self._db.get_last_report()
        if report_dict:
            return self._dict_to_report(report_dict)
        return None

    def _dict_to_report(self, d: dict) -> QAReport:
        """Convert dict from DB to QAReport."""
        ctx = InferenceContext.from_dict(d.get("context", {}))
        assertions = [
            AssertionResult(
                id=a["id"],
                name=a.get("name", a["id"]),
                passed=a["passed"],
                severity=a["severity"],
                expected=a["expected"],
                actual=a["actual"],
                details=a.get("details"),
            )
            for a in d.get("assertions", [])
        ]

        return QAReport(
            inference_id=d["inference_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]) if isinstance(d["timestamp"], str) else d["timestamp"],
            query=d["query"],
            route=d["route"],
            provider_used=d["provider_used"],
            latency_ms=d["latency_ms"],
            assertions=assertions,
            diagnosis=d.get("diagnosis"),
            context=ctx,
        )

    def get_health(self) -> dict:
        """Get quick health summary."""
        stats = self._db.get_stats("24h")
        failing_bugs = self._db.count_failing_bugs()

        return {
            "pass_rate": stats.get("pass_rate", 0),
            "total_24h": stats.get("total", 0),
            "failed_24h": stats.get("failed", 0),
            "failing_bugs": failing_bugs,
            "recent_failures": self._get_recent_failure_names(),
            "top_failures": stats.get("top_failures", []),
        }

    def _get_recent_failure_names(self) -> list[str]:
        """Get names of recently failing assertions."""
        if not self._last_report:
            return []
        return [a.name for a in self._last_report.failed_assertions]

    def get_stats(self, time_range: str = "24h") -> dict:
        """Get statistics for a time range."""
        return self._db.get_stats(time_range)

    def check_single(self, assertion_id: str, ctx: InferenceContext) -> Optional[dict]:
        """Run a single assertion by ID against a given context."""
        for a in self._assertions:
            if a.id == assertion_id and a.enabled:
                result = a.check(ctx)
                return {
                    "id": result.id,
                    "name": result.name,
                    "passed": result.passed,
                    "severity": result.severity,
                    "expected": result.expected,
                    "actual": result.actual,
                    "details": result.details,
                }
        return None

    def revalidate_last(self) -> Optional["QAReport"]:
        """Re-run all assertions against the stored context of the last inference."""
        if not self._last_report:
            return None
        ctx = self._last_report.context
        return self.validate(ctx)

    def revalidate_assertions(self, assertion_ids: list) -> list:
        """Re-run specific assertions against the last inference context."""
        if not self._last_report:
            return []
        ctx = self._last_report.context
        results = []
        for a in self._assertions:
            if a.id in assertion_ids and a.enabled:
                try:
                    r = a.check(ctx)
                    results.append({
                        "id": r.id,
                        "name": r.name,
                        "passed": r.passed,
                        "severity": r.severity,
                        "expected": r.expected,
                        "actual": r.actual,
                    })
                except Exception as e:
                    results.append({"id": a.id, "name": a.name, "passed": False, "error": str(e)})
        return results

    def mark_recalibration(self) -> datetime:
        """Mark a recalibration point. Stats context will note 'since recalibration'."""
        self._recalibrated_at = datetime.now()
        logger.info(f"QA recalibrated at {self._recalibrated_at.isoformat()}")
        return self._recalibrated_at

    @property
    def recalibrated_at(self) -> Optional[datetime]:
        return self._recalibrated_at

    def get_history(self, limit: int = 100) -> list[dict]:
        """Get report history."""
        return self._db.get_recent_reports(limit)


# Global validator instance (singleton pattern)
_global_validator: Optional[QAValidator] = None


def _resolve_db_path(db_path: str) -> str:
    """Resolve database path, using LUNA_BASE_PATH if set."""
    import os
    from pathlib import Path

    # If already absolute, use as-is
    if os.path.isabs(db_path):
        return db_path

    # Use LUNA_BASE_PATH if available
    base_path = os.environ.get("LUNA_BASE_PATH")
    if base_path:
        full_path = Path(base_path) / db_path
        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)
        return str(full_path)

    # Fallback to relative (original behavior)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_validator(db_path: str = "data/qa.db") -> QAValidator:
    """Get or create the global QA validator instance."""
    global _global_validator
    if _global_validator is None:
        resolved_path = _resolve_db_path(db_path)
        logger.info(f"Initializing QAValidator with db_path: {resolved_path}")
        _global_validator = QAValidator(resolved_path)
    return _global_validator
