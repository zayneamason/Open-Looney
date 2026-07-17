"""Stager data contracts — System Spec v1.2 Section 9.2.

Phase A: define StagePacket plus Route and Trace as frozen dataclasses
with to_dict() round-trip for debug payloads. No runtime caller wires
these yet; Phase B integrates them with the Director loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

RoutePath = Literal["direct", "agentic"]
RouteBackend = Literal["local", "delegate"]
StageWarning = Literal["pass_ceiling", "degraded_fallback"]


@dataclass(frozen=True)
class Route:
    path: RoutePath
    backend: RouteBackend

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "backend": self.backend}


@dataclass(frozen=True)
class Trace:
    pass_count: int
    exit_reason: str
    plan_signals: List[Any] = field(default_factory=list)
    lexicon_calls: int = 0
    total_latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pass_count": self.pass_count,
            "exit_reason": self.exit_reason,
            "plan_signals": list(self.plan_signals),
            "lexicon_calls": self.lexicon_calls,
            "total_latency_ms": self.total_latency_ms,
        }


@dataclass(frozen=True)
class StagePacket:
    minted_prompt: str
    route: Route
    trace: Trace
    warning: Optional[StageWarning] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "minted_prompt": self.minted_prompt,
            "route": self.route.to_dict(),
            "warning": self.warning,
            "trace": self.trace.to_dict(),
        }
