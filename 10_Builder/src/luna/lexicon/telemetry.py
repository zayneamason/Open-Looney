"""Lexicon telemetry records.

Lightweight serializable failure and shadow-comparison records. Phase A
emits failure records only; shadow comparisons are defined here for
Phase B without runtime callers yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict

logger = logging.getLogger("luna.lexicon")


@dataclass(frozen=True)
class LexiconFailure:
    role: str
    error_code: str
    call_meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "error_code": self.error_code,
            "call_meta": dict(self.call_meta),
        }


@dataclass(frozen=True)
class ShadowComparison:
    endpoint: str
    legacy_route: str
    pivot_route: str
    legacy_backend: str
    pivot_backend: str
    matched: bool
    diff: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "legacy_route": self.legacy_route,
            "pivot_route": self.pivot_route,
            "legacy_backend": self.legacy_backend,
            "pivot_backend": self.pivot_backend,
            "matched": self.matched,
            "diff": dict(self.diff),
        }


def emit_failure(record: LexiconFailure) -> None:
    """Emit a structured failure record at WARNING level."""
    logger.warning("[LEXICON] %s", record.to_dict())
