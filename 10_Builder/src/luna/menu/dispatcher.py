"""OrderDispatcher — places menu orders against DM runtimes.

Spec: `Docs/Design/LunaAssistantMapping/DESIGN_Agentic_Menu_Framework.md` §§4–5.

place_order() is non-blocking: it snapshots the task definition, fires the DM
runtime as a background task, and returns an OrderReceipt synchronously. The
on_complete callback receives the OrderResult when the DM exits or times out.
"""
import asyncio
import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional, Protocol

from luna.menu.registry import MenuRegistry, MenuTask

logger = logging.getLogger(__name__)


@dataclass
class GameSpec:
    order_id: str
    task_id: str
    name: str
    dm: str
    timeout_s: int


@dataclass
class OrderReceipt:
    order_id: str
    task_id: str
    task_snapshot: MenuTask
    dm: str
    status: Literal["queued", "running", "done", "failed", "timeout"]
    eta_s: Optional[float]


@dataclass
class OrderResult:
    order_id: str
    task_id: str
    payload: dict = field(default_factory=dict)
    exit_satisfied: bool = False
    duration_s: float = 0.0
    nodes_written: list[str] = field(default_factory=list)


OnComplete = Callable[[OrderResult], None]


class DMRuntime(Protocol):
    """All DM runtimes implement this."""
    async def run(
        self,
        spec: GameSpec,
        context: dict,
        exit_condition: str,
        on_complete: OnComplete,
    ) -> None: ...


class OrderDispatcher:
    def __init__(
        self,
        registry: MenuRegistry,
        dms: Optional[dict[str, DMRuntime]] = None,
    ):
        self._registry = registry
        self._dms: dict[str, DMRuntime] = dms or {}

    def register_dm(self, name: str, dm: DMRuntime) -> None:
        self._dms[name] = dm

    def place_order(
        self,
        task_id: str,
        context: dict,
        exit_condition: str,
        on_complete: OnComplete,
    ) -> OrderReceipt:
        task = self._registry.get_task(task_id)
        if task is None:
            raise ValueError(f"OrderDispatcher: unknown task_id '{task_id}'")
        if task.dm is None:
            raise ValueError(f"OrderDispatcher: task '{task_id}' is native (no DM)")

        snapshot = copy.deepcopy(task)
        order_id = str(uuid.uuid4())

        dm = self._dms.get(task.dm)
        if dm is None:
            raise ValueError(
                f"OrderDispatcher: no DM registered for '{task.dm}' "
                f"(needed by task '{task_id}')"
            )

        spec = GameSpec(
            order_id=order_id,
            task_id=task_id,
            name=task.name,
            dm=task.dm,
            timeout_s=task.timeout_s or 60,
        )

        coro = dm.run(spec, dict(context), exit_condition, on_complete)
        asyncio.create_task(coro, name=f"order-{order_id[:8]}")

        receipt = OrderReceipt(
            order_id=order_id,
            task_id=task_id,
            task_snapshot=snapshot,
            dm=task.dm,
            status="queued",
            eta_s=float(spec.timeout_s),
        )
        logger.info(
            "[dispatcher] queued order=%s task=%s dm=%s timeout=%ds",
            order_id[:8], task_id, task.dm, spec.timeout_s,
        )
        return receipt
