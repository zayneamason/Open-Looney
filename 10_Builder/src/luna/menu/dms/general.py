"""GeneralDM — research/synthesis scaffold.

Real tools (web_search, aibrarian_search, memory_matrix_search) are stubbed
for MVP. The Observe→Think→Act loop is real; the act step deterministically
mutates state to satisfy the exit condition.

Replace `_act()` with real tool calls in a follow-up handoff.
"""
import asyncio
import logging
import time

from luna.menu.dispatcher import GameSpec, OrderResult, OnComplete
from luna.menu.dsl import ExitConditionError, ExitConditionEvaluator

logger = logging.getLogger(__name__)

STEP_DELAY_S = 0.05


class GeneralDM:
    async def run(
        self,
        spec: GameSpec,
        context: dict,
        exit_condition: str,
        on_complete: OnComplete,
    ) -> None:
        evaluator = ExitConditionEvaluator()
        state = {
            "sources_cited": 0,
            "summary_written": False,
            "turns_processed": 0,
            "nodes_written": 0,
        }
        start = time.time()
        nodes_written: list[str] = []

        try:
            while time.time() - start < spec.timeout_s:
                state = self._act(state, nodes_written)
                state["turns_processed"] += 1

                try:
                    if evaluator.check(exit_condition, state):
                        on_complete(OrderResult(
                            order_id=spec.order_id,
                            task_id=spec.task_id,
                            payload=dict(state),
                            exit_satisfied=True,
                            duration_s=time.time() - start,
                            nodes_written=list(nodes_written),
                        ))
                        return
                except ExitConditionError as e:
                    logger.error("[dm:general] exit condition error: %s", e)

                await asyncio.sleep(STEP_DELAY_S)

            logger.warning("[dm:general] timeout after %ds", spec.timeout_s)
            on_complete(OrderResult(
                order_id=spec.order_id,
                task_id=spec.task_id,
                payload=dict(state),
                exit_satisfied=False,
                duration_s=spec.timeout_s,
                nodes_written=list(nodes_written),
            ))
        except Exception as e:
            logger.error("[dm:general] crashed: %s", e)
            on_complete(OrderResult(
                order_id=spec.order_id,
                task_id=spec.task_id,
                payload={"error": str(e), **state},
                exit_satisfied=False,
                duration_s=time.time() - start,
                nodes_written=list(nodes_written),
            ))

    @staticmethod
    def _act(state: dict, nodes_written: list[str]) -> dict:
        """Stub act step. Real impl: web_search → aibrarian → matrix lookups."""
        logger.debug("[dm:general] (stub) acting at turn=%d", state["turns_processed"])
        if state["sources_cited"] < 3:
            state["sources_cited"] += 1
            nodes_written.append(f"stub_source_{state['sources_cited']}")
            state["nodes_written"] = len(nodes_written)
        else:
            state["summary_written"] = True
        return state
