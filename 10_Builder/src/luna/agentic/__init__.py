"""
Agentic Architecture for Luna Engine
=====================================

This module implements the agentic framework from Part XIV of the Luna Engine Bible.
It transforms Luna from a chatbot-with-memory into a Personal Palantir with:

- Agent Loop: Autonomous observe -> think -> act -> repeat cycle
- Planning Layer: Task decomposition and multi-step reasoning
- Query Routing: Adaptive complexity-based path selection
- Swarm Coordination: Parallel execution for complex tasks (future)

The key insight: 90% of queries should hit DIRECT or SIMPLE_PLAN paths.
The heavy machinery only spins up when needed.
"""

from .loop import (
    AgentLoop,
    AgentResult,
    Observation,
    Action,
    ActionResult,
    WorkingContext,
)
from .planner import (
    Planner,
    Plan,
    PlanStep,
    PlanStepType,
)
from .router import (
    QueryRouter,
    ExecutionPath,
)
from .directives import DirectiveEngine

__all__ = [
    # Agent Loop
    "AgentLoop",
    "AgentResult",
    "Observation",
    "Action",
    "ActionResult",
    "WorkingContext",
    # Planner
    "Planner",
    "Plan",
    "PlanStep",
    "PlanStepType",
    # Router
    "QueryRouter",
    "ExecutionPath",
    # Intent Layer
    "DirectiveEngine",
]
