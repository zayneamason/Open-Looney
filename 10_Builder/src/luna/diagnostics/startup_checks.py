"""
Startup Health Checks for Luna Engine
======================================

Critical system verification before server accepts requests.
Prevents silent failures where Luna runs without key systems.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CheckStatus(Enum):
    """Status of a startup check."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    """Result of a single startup check."""
    name: str
    status: CheckStatus
    message: str
    required: bool = False


class StartupChecks:
    """
    Run critical system checks before server startup.

    Usage:
        checks = StartupChecks(require_local_inference=True)
        results = await checks.run_all()
        if not checks.passed:
            raise RuntimeError(checks.failure_summary())
    """

    def __init__(
        self,
        require_local_inference: bool = False,
        require_database: bool = True,
        require_embeddings: bool = False,
    ):
        """
        Configure startup requirements.

        Args:
            require_local_inference: Block startup if MLX unavailable
            require_database: Block startup if database unreachable
            require_embeddings: Block startup if embedding model unavailable
        """
        self.require_local_inference = require_local_inference
        self.require_database = require_database
        self.require_embeddings = require_embeddings
        self.results: list[CheckResult] = []

    @property
    def passed(self) -> bool:
        """True if all required checks passed."""
        return all(
            r.status != CheckStatus.FAIL
            for r in self.results
            if r.required
        )

    @property
    def warnings(self) -> list[CheckResult]:
        """Get all warnings."""
        return [r for r in self.results if r.status == CheckStatus.WARN]

    @property
    def failures(self) -> list[CheckResult]:
        """Get all failures."""
        return [r for r in self.results if r.status == CheckStatus.FAIL]

    def failure_summary(self) -> str:
        """Get summary of failures for error message."""
        failed = [r for r in self.results if r.status == CheckStatus.FAIL and r.required]
        if not failed:
            return "All checks passed"
        lines = ["Critical startup checks failed:"]
        for r in failed:
            lines.append(f"  - {r.name}: {r.message}")
        return "\n".join(lines)

    async def run_all(self) -> list[CheckResult]:
        """Run all configured checks."""
        self.results = []

        # Check 1: MLX / Local Inference
        self.results.append(await self._check_local_inference())

        # Check 2: Database connectivity
        self.results.append(await self._check_database())

        # Check 3: Embedding model
        self.results.append(await self._check_embeddings())

        # Log results
        for r in self.results:
            if r.status == CheckStatus.PASS:
                logger.info("[STARTUP] %s: %s", r.name, r.message)
            elif r.status == CheckStatus.WARN:
                logger.warning("[STARTUP] %s: %s", r.name, r.message)
            else:
                req = " (REQUIRED)" if r.required else ""
                logger.error("[STARTUP] %s: %s%s", r.name, r.message, req)

        return self.results

    async def _check_local_inference(self) -> CheckResult:
        """Check if MLX local inference is available."""
        try:
            from luna.inference.local import LocalInference
            inference = LocalInference()

            if inference.is_available():
                return CheckResult(
                    name="Local Inference (MLX)",
                    status=CheckStatus.PASS,
                    message="MLX available for local generation",
                    required=self.require_local_inference,
                )
            else:
                from luna.diagnostics.maturity import is_compiled
                if is_compiled():
                    # Compiled builds use online LLMs only — not a problem
                    return CheckResult(
                        name="Local Inference (MLX)",
                        status=CheckStatus.PASS,
                        message="Compiled build — using cloud LLM providers",
                        required=False,
                    )
                return CheckResult(
                    name="Local Inference (MLX)",
                    status=CheckStatus.FAIL if self.require_local_inference else CheckStatus.WARN,
                    message="MLX not available - will use Claude API only",
                    required=self.require_local_inference,
                )
        except ImportError as e:
            return CheckResult(
                name="Local Inference (MLX)",
                status=CheckStatus.FAIL if self.require_local_inference else CheckStatus.WARN,
                message=f"Import error: {e}",
                required=self.require_local_inference,
            )
        except Exception as e:
            return CheckResult(
                name="Local Inference (MLX)",
                status=CheckStatus.FAIL if self.require_local_inference else CheckStatus.WARN,
                message=f"Check failed: {e}",
                required=self.require_local_inference,
            )

    async def _check_database(self) -> CheckResult:
        """Check if database is accessible."""
        try:
            from luna.substrate.database import MemoryDatabase
            db = MemoryDatabase()
            await db.connect()

            # Quick sanity check
            result = await db.fetchone("SELECT COUNT(*) FROM memory_nodes")
            count = result[0] if result else 0
            await db.close()

            return CheckResult(
                name="Memory Database",
                status=CheckStatus.PASS,
                message=f"Connected, {count:,} nodes",
                required=self.require_database,
            )
        except Exception as e:
            return CheckResult(
                name="Memory Database",
                status=CheckStatus.FAIL if self.require_database else CheckStatus.WARN,
                message=f"Connection failed: {e}",
                required=self.require_database,
            )

    async def _check_embeddings(self) -> CheckResult:
        """Check if embedding model is available."""
        try:
            from luna.substrate.local_embeddings import LocalEmbeddings
            embeddings = LocalEmbeddings()

            # Try to load model
            await embeddings.load_model()

            if embeddings.is_loaded:
                return CheckResult(
                    name="Embedding Model",
                    status=CheckStatus.PASS,
                    message="Local embeddings ready",
                    required=self.require_embeddings,
                )
            else:
                return CheckResult(
                    name="Embedding Model",
                    status=CheckStatus.FAIL if self.require_embeddings else CheckStatus.WARN,
                    message="Model not loaded",
                    required=self.require_embeddings,
                )
        except ImportError:
            return CheckResult(
                name="Embedding Model",
                status=CheckStatus.WARN,
                message="LocalEmbeddings not available",
                required=self.require_embeddings,
            )
        except Exception as e:
            return CheckResult(
                name="Embedding Model",
                status=CheckStatus.FAIL if self.require_embeddings else CheckStatus.WARN,
                message=f"Check failed: {e}",
                required=self.require_embeddings,
            )


async def run_startup_checks(
    require_local: bool = False,
    require_db: bool = True,
) -> bool:
    """
    Convenience function to run startup checks.

    Args:
        require_local: Fail if MLX unavailable
        require_db: Fail if database unreachable

    Returns:
        True if all required checks passed

    Raises:
        RuntimeError if required checks fail
    """
    checks = StartupChecks(
        require_local_inference=require_local,
        require_database=require_db,
    )
    await checks.run_all()

    if not checks.passed:
        raise RuntimeError(checks.failure_summary())

    return True
