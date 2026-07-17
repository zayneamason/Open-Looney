"""
Knowledge Event Bus
===================

In-process publish-subscribe bus for broadcasting knowledge pipeline events
(entity creation, fact extraction, edge creation, etc.) to WebSocket clients.

Distinct from core/events.py which defines InputBuffer event types for the
engine tick loop.
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeEvent:
    """A single event emitted by the extraction pipeline."""

    type: str
    payload: dict
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """
    Simple in-process async event bus.

    Actors emit events via ``emit()``.  WebSocket handlers subscribe via
    ``subscribe()`` and receive events as async callbacks scheduled on
    the running event loop.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, channel: str, callback: Callable) -> None:
        """Register *callback* for events on *channel*."""
        self._subscribers[channel].append(callback)

    def emit(self, channel: str, event: KnowledgeEvent) -> None:
        """
        Fan-out *event* to all subscribers on *channel*.

        Callbacks are scheduled as tasks on the current event loop so
        ``emit()`` never blocks the caller (extraction pipeline).
        """
        callbacks = self._subscribers.get(channel)
        if not callbacks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("event_bus.emit called outside async context, dropping %s", event.type)
            return
        for cb in callbacks:
            loop.call_soon(lambda c=cb, e=event: asyncio.ensure_future(c(e)))


# Module-level singleton — import and use directly.
event_bus = EventBus()
