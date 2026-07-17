"""
Luna QA System v2
=================

Live validation system for Luna's inference pipeline.

Components:
- InferenceContext: Full telemetry for a single inference
- Assertions: Pattern-based and built-in validation rules
- QAValidator: Runs assertions against inferences
- QADatabase: SQLite storage for reports and bugs

Usage:
    from luna.qa import QAValidator, InferenceContext

    validator = QAValidator()
    ctx = InferenceContext(query="hello", final_response="hey there!")
    report = validator.validate(ctx)

    if not report.passed:
        print(f"QA Failed: {report.diagnosis}")
"""

from .context import InferenceContext, RequestStep
from .assertions import (
    Assertion,
    AssertionResult,
    PatternConfig,
    get_default_assertions,
)
from .validator import QAValidator, QAReport
from .database import QADatabase

__all__ = [
    "InferenceContext",
    "RequestStep",
    "Assertion",
    "AssertionResult",
    "PatternConfig",
    "get_default_assertions",
    "QAValidator",
    "QAReport",
    "QADatabase",
]
