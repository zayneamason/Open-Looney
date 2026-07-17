"""ListenerActor — pre-loads context for the menu framework.

Spec: `Docs/Design/LunaAssistantMapping/DESIGN_Agentic_Menu_Framework.md` §2.
ADR: `Docs/Design/LunaAssistantMapping/ADR_Menu_Framework_Decisions.md` ADR-001.

Subscribes to engine `turn_completed` events. Scores each menu task against
the recent turn window; for tasks above the prediction threshold, pre-loads
the context_slots so dispatch can flush them in <50ms when an order lands.

MVP scope:
- substring scoring on trigger_signals (no embedding similarity)
- only `user_query` context slot is populated; other slots are empty
- single-task best-match per turn (no multi-prediction yet)
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from luna.actors.base import Actor, Message
from luna.menu.registry import MenuRegistry, MenuTask

logger = logging.getLogger(__name__)

PREDICTION_THRESHOLD = 0.55
WINDOW_SIZE = 3


@dataclass
class ListenerPrediction:
    task_id: str
    confidence: float
    preloaded_context: dict = field(default_factory=dict)
    turn_id: Optional[str] = None


class ListenerActor(Actor):
    def __init__(self, menu_registry: MenuRegistry, name: str = "listener"):
        super().__init__(name)
        self._registry = menu_registry
        self._cache: dict[str, ListenerPrediction] = {}
        self._window: deque = deque(maxlen=WINDOW_SIZE)

    async def handle(self, msg: Message) -> None:
        if msg.type != "turn_completed":
            return
        await self._process_turn(msg.payload)

    async def _process_turn(self, turn) -> None:
        text = getattr(turn, "content", "") or ""
        turn_id = getattr(turn, "id", None)
        self._window.append(text)

        for task in self._registry.get_all_tasks():
            if task.dm is None:
                continue
            score = self._score_signals(task)
            if score >= PREDICTION_THRESHOLD:
                ctx = self._preload_context(task, last_turn_text=text)
                self._cache[task.id] = ListenerPrediction(
                    task_id=task.id,
                    confidence=score,
                    preloaded_context=ctx,
                    turn_id=str(turn_id) if turn_id is not None else None,
                )
                logger.debug(
                    "[listener] cached prediction for %s (score=%.2f)", task.id, score,
                )

    def flush_prediction(self, task_id: str, timeout_ms: int = 50) -> dict:
        """LatencyGate calls this when an order is placed.

        timeout_ms is documented for parity with the spec. Lookup is in-memory
        and synchronous, so the timeout is effectively unused — it's there so a
        future async preload can honor it without changing the call site.
        """
        pred = self._cache.get(task_id)
        return pred.preloaded_context if pred else {}

    def predictions(self) -> list[ListenerPrediction]:
        """Snapshot of current cached predictions for LatencyGate."""
        return list(self._cache.values())

    def _score_signals(self, task: MenuTask) -> float:
        if not task.trigger_signals or not self._window:
            return 0.0
        joined = " ".join(self._window).lower()
        hits = sum(1 for sig in task.trigger_signals if sig.lower() in joined)
        return min(hits / len(task.trigger_signals), 1.0)

    def _preload_context(self, task: MenuTask, last_turn_text: str) -> dict:
        ctx: dict = {}
        for slot in task.context_slots:
            if slot == "user_query":
                ctx[slot] = last_turn_text
            # Other slots (active_entities, related_matrix_nodes, session_id, etc.)
            # land in a follow-up once Matrix preload is wired.
        return ctx
