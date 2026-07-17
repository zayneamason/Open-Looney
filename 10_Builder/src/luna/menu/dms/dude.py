"""DudeDM — graph linking scaffold.

Thinnest of the three DMs per the handoff. Logs the call and immediately
exits with success. Real graph traversal lands in a follow-up handoff.
"""
import logging
import time

from luna.menu.dispatcher import GameSpec, OrderResult, OnComplete

logger = logging.getLogger(__name__)


class DudeDM:
    async def run(
        self,
        spec: GameSpec,
        context: dict,
        exit_condition: str,
        on_complete: OnComplete,
    ) -> None:
        logger.info("[dm:dude] (scaffold) order=%s task=%s — not yet implemented",
                    spec.order_id[:8], spec.task_id)
        start = time.time()
        on_complete(OrderResult(
            order_id=spec.order_id,
            task_id=spec.task_id,
            payload={"orphan_entity_count": 0, "max_hops_satisfied": True,
                     "hops_completed": 0},
            exit_satisfied=True,
            duration_s=time.time() - start,
            nodes_written=[],
        ))
