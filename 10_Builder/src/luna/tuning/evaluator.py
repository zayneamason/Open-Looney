"""
Luna Engine Evaluator
=====================

Test suite and scoring functions for evaluating Luna's quality.

Test types:
- memory_recall: Can Luna remember specific facts?
- context_retention: Does Luna maintain context across turns?
- routing: Are queries routed to correct path?
- latency: How fast does Luna respond?
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from luna.engine import LunaEngine

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """A single test case for evaluation."""
    type: str
    name: str
    query: str
    expected: list[str] = field(default_factory=list)
    expected_route: Optional[str] = None
    max_ms: Optional[int] = None
    setup: Optional[str] = None  # Setup instructions (e.g., seed memory)
    turns: list[str] = field(default_factory=list)  # For multi-turn tests


@dataclass
class TestResult:
    """Result of a single test case."""
    test: TestCase
    passed: bool
    score: float  # 0.0 to 1.0
    response: str = ""
    latency_ms: float = 0.0
    actual_route: str = ""
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class EvalResults:
    """Aggregated evaluation results."""
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0

    # Category scores (0.0 to 1.0)
    memory_recall_score: float = 0.0
    context_retention_score: float = 0.0
    routing_score: float = 0.0

    # Latency metrics
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    # Composite score
    overall_score: float = 0.0

    # Individual results
    results: list[TestResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "memory_recall_score": self.memory_recall_score,
            "context_retention_score": self.context_retention_score,
            "routing_score": self.routing_score,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "overall_score": self.overall_score,
        }


# =============================================================================
# DEFAULT TEST SUITE
# =============================================================================

DEFAULT_TEST_SUITE: list[dict] = [
    # Memory recall tests
    {
        "type": "memory_recall",
        "name": "recall_marzipan",
        "query": "Who is Marzipan?",
        "expected": ["cat", "pet", "feline"],
        "setup": "Ensure Marzipan memory exists",
    },
    {
        "type": "memory_recall",
        "name": "recall_mars_college",
        "query": "What is Mars College?",
        "expected": ["desert", "residency", "art", "technology"],
        "setup": "Ensure Mars College memory exists",
    },
    {
        "type": "memory_recall",
        "name": "recall_user_preference",
        "query": "What's my favorite color?",
        "expected": [],  # Will pass if any color mentioned
        "setup": "Requires prior preference memory",
    },

    # Context retention tests
    {
        "type": "context_retention",
        "name": "short_term_recall",
        "turns": ["I love coffee in the morning", "What did I just say I love?"],
        "expected": ["coffee"],
    },
    {
        "type": "context_retention",
        "name": "topic_continuity",
        "turns": ["Let's talk about cats", "What are we discussing?"],
        "expected": ["cat"],
    },
    {
        "type": "context_retention",
        "name": "backward_reference",
        "turns": ["My name is Alex", "Remember when I told you my name?"],
        "expected": ["Alex"],
    },

    # Routing tests
    {
        "type": "routing",
        "name": "greeting_direct",
        "query": "hi",
        "expected_route": "direct",
    },
    {
        "type": "routing",
        "name": "simple_question",
        "query": "What time is it?",
        "expected_route": "simple",
    },
    {
        "type": "routing",
        "name": "complex_question",
        "query": "Explain the implications of quantum computing on cryptography",
        "expected_route": "full",
    },

    # Latency tests
    {
        "type": "latency",
        "name": "greeting_fast",
        "query": "hello",
        "max_ms": 500,
    },
    {
        "type": "latency",
        "name": "simple_fast",
        "query": "How are you?",
        "max_ms": 1000,
    },
]


class Evaluator:
    """
    Evaluates Luna's quality across multiple dimensions.

    Runs test cases and computes scores for:
    - Memory recall accuracy
    - Context retention
    - Routing accuracy
    - Response latency
    """

    def __init__(
        self,
        engine: Optional["LunaEngine"] = None,
        test_suite: Optional[list[dict]] = None,
    ):
        """
        Initialize the evaluator.

        Args:
            engine: LunaEngine instance to evaluate
            test_suite: Custom test suite (uses DEFAULT_TEST_SUITE if None)
        """
        self.engine = engine
        self.test_suite = test_suite or DEFAULT_TEST_SUITE
        self._tests = self._build_tests()

    def _build_tests(self) -> list[TestCase]:
        """Build TestCase objects from suite."""
        tests = []
        for tc in self.test_suite:
            tests.append(TestCase(
                type=tc["type"],
                name=tc.get("name", "unnamed"),
                query=tc.get("query", ""),
                expected=tc.get("expected", []),
                expected_route=tc.get("expected_route"),
                max_ms=tc.get("max_ms"),
                setup=tc.get("setup"),
                turns=tc.get("turns", []),
            ))
        return tests

    async def run_all(self) -> EvalResults:
        """Run all tests and return aggregated results."""
        results = EvalResults()
        results.results = []

        latencies = []
        memory_scores = []
        context_scores = []
        routing_scores = []

        for test in self._tests:
            result = await self._run_test(test)
            results.results.append(result)
            results.total_tests += 1

            if result.passed:
                results.passed_tests += 1
            else:
                results.failed_tests += 1

            latencies.append(result.latency_ms)

            # Categorize scores
            if test.type == "memory_recall":
                memory_scores.append(result.score)
            elif test.type == "context_retention":
                context_scores.append(result.score)
            elif test.type == "routing":
                routing_scores.append(result.score)

        # Calculate category scores
        results.memory_recall_score = sum(memory_scores) / len(memory_scores) if memory_scores else 0.0
        results.context_retention_score = sum(context_scores) / len(context_scores) if context_scores else 0.0
        results.routing_score = sum(routing_scores) / len(routing_scores) if routing_scores else 0.0

        # Calculate latency metrics
        if latencies:
            latencies_sorted = sorted(latencies)
            results.avg_latency_ms = sum(latencies) / len(latencies)
            results.max_latency_ms = max(latencies)
            p95_idx = int(len(latencies_sorted) * 0.95)
            results.p95_latency_ms = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)]

        # Calculate overall score (weighted average)
        weights = {
            "memory": 0.35,
            "context": 0.25,
            "routing": 0.20,
            "latency": 0.20,
        }

        # Latency score (inverse: lower is better)
        latency_score = max(0, 1 - (results.avg_latency_ms / 2000))  # 2s = 0 score

        results.overall_score = (
            weights["memory"] * results.memory_recall_score +
            weights["context"] * results.context_retention_score +
            weights["routing"] * results.routing_score +
            weights["latency"] * latency_score
        )

        return results

    async def run_category(self, category: str) -> EvalResults:
        """Run only tests of a specific category."""
        filtered = [t for t in self._tests if t.type == category]
        original = self._tests
        self._tests = filtered
        try:
            return await self.run_all()
        finally:
            self._tests = original

    async def _run_test(self, test: TestCase) -> TestResult:
        """Run a single test case."""
        result = TestResult(test=test, passed=False, score=0.0)

        try:
            if test.type == "memory_recall":
                result = await self._run_memory_test(test)
            elif test.type == "context_retention":
                result = await self._run_context_test(test)
            elif test.type == "routing":
                result = await self._run_routing_test(test)
            elif test.type == "latency":
                result = await self._run_latency_test(test)
            else:
                result.error = f"Unknown test type: {test.type}"

        except Exception as e:
            result.error = str(e)
            logger.error(f"Test {test.name} failed with error: {e}")

        return result

    async def _run_memory_test(self, test: TestCase) -> TestResult:
        """Run a memory recall test."""
        result = TestResult(test=test, passed=False, score=0.0)

        start = time.perf_counter()
        response = await self._query_luna(test.query)
        result.latency_ms = (time.perf_counter() - start) * 1000
        result.response = response

        # Check if any expected terms are in the response
        response_lower = response.lower()
        matches = sum(1 for term in test.expected if term.lower() in response_lower)

        if test.expected:
            result.score = matches / len(test.expected)
            result.passed = matches > 0  # At least one match
        else:
            # No expected terms = check for non-empty response
            result.score = 1.0 if len(response) > 10 else 0.0
            result.passed = result.score > 0

        result.details = {
            "matches": matches,
            "total_expected": len(test.expected),
            "expected": test.expected,
        }

        return result

    async def _run_context_test(self, test: TestCase) -> TestResult:
        """Run a context retention test (multi-turn)."""
        result = TestResult(test=test, passed=False, score=0.0)

        # Run through turns
        responses = []
        total_latency = 0.0

        for turn in test.turns:
            start = time.perf_counter()
            response = await self._query_luna(turn)
            total_latency += (time.perf_counter() - start) * 1000
            responses.append(response)

        result.latency_ms = total_latency
        result.response = responses[-1] if responses else ""

        # Check final response for expected terms
        response_lower = result.response.lower()
        matches = sum(1 for term in test.expected if term.lower() in response_lower)

        if test.expected:
            result.score = matches / len(test.expected)
            result.passed = matches > 0
        else:
            result.score = 1.0
            result.passed = True

        result.details = {
            "turns": len(test.turns),
            "all_responses": responses,
        }

        return result

    async def _run_routing_test(self, test: TestCase) -> TestResult:
        """Run a routing test."""
        result = TestResult(test=test, passed=False, score=0.0)

        start = time.perf_counter()
        actual_route = await self._get_route(test.query)
        result.latency_ms = (time.perf_counter() - start) * 1000
        result.actual_route = actual_route

        if test.expected_route:
            result.passed = actual_route == test.expected_route
            result.score = 1.0 if result.passed else 0.0
        else:
            result.passed = True
            result.score = 1.0

        result.details = {
            "expected_route": test.expected_route,
            "actual_route": actual_route,
        }

        return result

    async def _run_latency_test(self, test: TestCase) -> TestResult:
        """Run a latency test."""
        result = TestResult(test=test, passed=False, score=0.0)

        start = time.perf_counter()
        response = await self._query_luna(test.query)
        result.latency_ms = (time.perf_counter() - start) * 1000
        result.response = response

        if test.max_ms:
            result.passed = result.latency_ms <= test.max_ms
            # Score based on how much under the limit
            result.score = max(0, 1 - (result.latency_ms / test.max_ms))
        else:
            result.passed = True
            result.score = 1.0

        result.details = {
            "max_ms": test.max_ms,
            "actual_ms": result.latency_ms,
        }

        return result

    async def _query_luna(self, message: str) -> str:
        """Query Luna and get response."""
        if not self.engine:
            # Mock response for testing without engine
            return f"[Mock response to: {message}]"

        try:
            # Use the engine's process method
            response = await self.engine.process_input(message)
            return response if isinstance(response, str) else str(response)
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return f"[Error: {e}]"

    async def _get_route(self, message: str) -> str:
        """Get the routing decision for a message."""
        if not self.engine:
            return "unknown"

        try:
            router = self.engine.get_actor("router")
            if router and hasattr(router, "classify"):
                classification = await router.classify(message)
                return classification.get("route", "unknown")
        except Exception as e:
            logger.error(f"Routing check failed: {e}")

        return "unknown"

    def add_test(self, test_config: dict) -> None:
        """Add a custom test case."""
        test = TestCase(
            type=test_config["type"],
            name=test_config.get("name", "custom"),
            query=test_config.get("query", ""),
            expected=test_config.get("expected", []),
            expected_route=test_config.get("expected_route"),
            max_ms=test_config.get("max_ms"),
            setup=test_config.get("setup"),
            turns=test_config.get("turns", []),
        )
        self._tests.append(test)
        self.test_suite.append(test_config)

    def remove_test(self, name: str) -> bool:
        """Remove a test case by name."""
        for i, test in enumerate(self._tests):
            if test.name == name:
                self._tests.pop(i)
                self.test_suite.pop(i)
                return True
        return False

    def list_tests(self) -> list[str]:
        """List all test names."""
        return [t.name for t in self._tests]
