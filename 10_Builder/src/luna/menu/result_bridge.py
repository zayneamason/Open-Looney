"""ResultBridge — surfaces DM completions as assistant conversation turns.

See `Docs/Design/LunaAssistantMapping/DESIGN_DM_Result_Delivery.md` for the
canonical design (7 locked answers). The bridge owns the on_complete callback
shape that OrderDispatcher invokes, queues results FIFO, and drains them at
safe boundaries via record_conversation_turn so they flow through history,
turn_completed fan-out, and (soon) UI rendering for free.

Loop prevention: every surfaced turn carries `dm_origin: True` in metadata.
record_conversation_turn honors that flag and skips Scribe extraction so DM
results don't re-enter the extraction pipeline (Q6).
"""
import asyncio
import collections
import logging
import time
from dataclasses import dataclass

from luna.menu.dispatcher import OrderResult

logger = logging.getLogger(__name__)


@dataclass
class QueuedResult:
    task_name: str
    result: OrderResult
    original_turn_id: int | None
    arrived_at: float


def make_completion_strip(
    task_name: str,
    nodes_written_count: int,
    *,
    exit_satisfied: bool,
    timed_out: bool,
    errored: bool,
) -> str:
    """Differentiated strip per failure mode — see DESIGN_DM_Result_Delivery.md Q5."""
    suffix = "node" if nodes_written_count == 1 else "nodes"
    if errored:
        return f"◎ {task_name} · failed"
    if timed_out and nodes_written_count == 0:
        return f"◎ {task_name} · timed out"
    if not exit_satisfied:
        return f"◎ {task_name} · partial · {nodes_written_count} {suffix} written · timed out"
    return f"◎ {task_name} · done · {nodes_written_count} {suffix} written"


class ResultBridge:
    """Owns the on_complete callback shape OrderDispatcher invokes.

    Queues results and drains at safe boundaries (text-mode end-of-turn,
    idle cognitive tick) to avoid mid-turn / mid-utterance races.
    """

    def __init__(self, engine):
        self._engine = engine
        self._pending: collections.deque[QueuedResult] = collections.deque()
        self._lock = asyncio.Lock()

    def make_callback(self, task_name: str, original_turn_id: int | None):
        """Return a sync callable suitable for OrderDispatcher.place_order(on_complete=...).

        The dispatcher invokes on_complete synchronously from the DM's event-loop
        coroutine (verified: dispatcher.py uses asyncio.create_task; DMs call
        on_complete(result) without awaiting). Append on a deque is atomic under
        the single-threaded asyncio model, so no lock is needed here. The drain
        loop holds self._lock to serialize itself against concurrent drains.
        """
        def _on_complete(result: OrderResult) -> None:
            self._pending.append(QueuedResult(
                task_name=task_name,
                result=result,
                original_turn_id=original_turn_id,
                arrived_at=time.time(),
            ))
            logger.info(
                "[result-bridge] enqueued task=%s order=%s exit_satisfied=%s",
                task_name, result.order_id[:8], result.exit_satisfied,
            )
        return _on_complete

    async def drain(self) -> int:
        """Pop every queued result and surface as an assistant turn.

        Called by the engine at safe boundaries. Returns count drained.
        A failure surfacing one result never blocks the rest of the drain.
        """
        drained = 0
        async with self._lock:
            while self._pending:
                q = self._pending.popleft()
                try:
                    await self._surface_one(q)
                    drained += 1
                except Exception as e:
                    logger.error(
                        "[result-bridge] surface failed for task=%s: %s",
                        q.task_name, e,
                    )
        if drained:
            logger.info("[result-bridge] drained %d result(s)", drained)
        return drained

    async def _surface_one(self, q: QueuedResult) -> None:
        result = q.result
        payload = result.payload or {}
        errored = "error" in payload
        timed_out = (not result.exit_satisfied) and (not errored)

        strip = make_completion_strip(
            q.task_name,
            len(result.nodes_written),
            exit_satisfied=result.exit_satisfied,
            timed_out=timed_out,
            errored=errored,
        )
        body = self._format_payload(payload, errored=errored)
        content = f"{strip}\n\n{body}" if body else strip

        logger.info(
            "[result-bridge] surfacing result for task=%s as assistant turn",
            q.task_name,
        )
        await self._engine.record_conversation_turn(
            role="assistant",
            content=content,
            source="dm_result",
            turn_metadata={
                "dm_origin": True,
                "task_id": result.task_id,
                "task_name": q.task_name,
                "order_id": result.order_id,
                "exit_satisfied": result.exit_satisfied,
                "timed_out": timed_out,
                "errored": errored,
                "duration_s": result.duration_s,
                "nodes_written": result.nodes_written,
                "original_turn_id": q.original_turn_id,
            },
        )

    def _format_payload(self, payload: dict, *, errored: bool) -> str:
        if errored:
            return f"error: {payload.get('error', 'unknown')}"
        if not payload:
            return ""
        if "summary" in payload:
            return str(payload["summary"])
        return str(payload)
