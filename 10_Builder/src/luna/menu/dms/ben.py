"""BenDM — turn extraction scaffold.

Real tools (luna_record_turn, aibrarian_ingest, memory_matrix_add_node) are
stubbed for MVP. The classification loop is structurally correct;
classification work is deferred to a follow-up.
"""
import asyncio
import logging
import time

from luna.menu.dispatcher import GameSpec, OrderResult, OnComplete
from luna.menu.dsl import ExitConditionError, ExitConditionEvaluator

logger = logging.getLogger(__name__)

STEP_DELAY_S = 0.05


class BenDM:
    async def run(
        self,
        spec: GameSpec,
        context: dict,
        exit_condition: str,
        on_complete: OnComplete,
    ) -> None:
        evaluator = ExitConditionEvaluator()
        unclassified = int(context.get("unclassified_turns", 0))
        state = {
            "all_unclassified_turns": unclassified,
            "turns_processed": 0,
            "nodes_written": 0,
        }
        start = time.time()
        nodes_written: list[str] = []

        try:
            while time.time() - start < spec.timeout_s:
                state = self._act(state, nodes_written)

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
                    logger.error("[dm:ben] exit condition error: %s", e)

                await asyncio.sleep(STEP_DELAY_S)

            logger.warning("[dm:ben] timeout after %ds", spec.timeout_s)
            on_complete(OrderResult(
                order_id=spec.order_id,
                task_id=spec.task_id,
                payload=dict(state),
                exit_satisfied=False,
                duration_s=spec.timeout_s,
                nodes_written=list(nodes_written),
            ))
        except Exception as e:
            logger.error("[dm:ben] crashed: %s", e)
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
        """Stub act step. Real impl: classify each unclassified turn."""
        if state["all_unclassified_turns"] > 0:
            state["all_unclassified_turns"] -= 1
            nodes_written.append(f"stub_classified_{state['turns_processed']}")
            state["nodes_written"] = len(nodes_written)
        state["turns_processed"] += 1
        return state
