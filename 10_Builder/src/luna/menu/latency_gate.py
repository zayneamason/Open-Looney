"""LatencyGate — unified dispatch decision.

Spec: `Docs/Design/LunaAssistantMapping/DESIGN_Agentic_Menu_Framework.md` §3.

The gate makes two sub-decisions in one pass:
  A. complexity tier (feeds AutonomySynthesizer) — read from QueryRouter
  B. native vs. menu dispatch — match against ListenerActor predictions
"""
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from luna.agentic.router import QueryRouter
from luna.llm.class_detector import ClassDetection
from luna.menu.registry import MenuRegistry, MenuTask

logger = logging.getLogger(__name__)

MENU_MATCH_THRESHOLD = 0.45


@dataclass
class LatencyDecision:
    complexity_tier: Literal["direct", "simple_plan", "full_plan", "background"]
    path: Literal["native", "menu"]
    task_id: Optional[str]
    preloaded_context: dict = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


class LatencyGate:
    def __init__(
        self,
        registry: MenuRegistry,
        listener,                         # ListenerActor (avoid hard import for testability)
        query_router: QueryRouter,
    ):
        self._registry = registry
        self._listener = listener
        self._query_router = query_router

    def evaluate(
        self,
        detection: ClassDetection,
        message: str,
        predictions: list,                # list[ListenerPrediction]
    ) -> LatencyDecision:
        tier = self._query_router.route(message).name.lower()

        best_task = self._match_task(predictions)

        if best_task is None or best_task.dm is None:
            return LatencyDecision(
                complexity_tier=tier,
                path="native",
                task_id=None,
                preloaded_context={},
                confidence=1.0 if best_task else 0.0,
                reason="native or no match",
            )

        preloaded = self._listener.flush_prediction(best_task.id, timeout_ms=50)
        top_conf = max((p.confidence for p in predictions if p.task_id == best_task.id),
                       default=0.0)
        return LatencyDecision(
            complexity_tier=tier,
            path="menu",
            task_id=best_task.id,
            preloaded_context=preloaded,
            confidence=top_conf,
            reason=f"matched task={best_task.id}",
        )

    def _match_task(self, predictions: list) -> Optional[MenuTask]:
        """Pick highest-confidence prediction above the menu match threshold."""
        if not predictions:
            return None
        top = max(predictions, key=lambda p: p.confidence)
        if top.confidence < MENU_MATCH_THRESHOLD:
            return None
        return self._registry.get_task(top.task_id)
