"""
Input Buffer for Luna Engine
============================

Events from interfaces (Voice, Desktop, MCP) land in the buffer.
The Engine polls the buffer at each tick, providing situational awareness.

Key insight: Engine PULLS from buffer. It doesn't get PUSHED to.
This gives the engine complete control over when and what to process.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from .events import InputEvent, EventPriority

logger = logging.getLogger(__name__)


class InputBuffer:
    """
    Async buffer for input events.

    Benefits over pure event-driven:
    - Situational awareness: Engine sees ALL pending input before deciding
    - Abort control: Can cancel current inference if user interrupts
    - Priority ordering: Process urgent events first
    - Stale dropping: Discard outdated partials
    - No interrupt storms: Buffer absorbs bursts
    """

    def __init__(self, max_size: int = 100, stale_threshold_seconds: float = 5.0):
        self._queue: asyncio.Queue[InputEvent] = asyncio.Queue(maxsize=max_size)
        self.stale_threshold = stale_threshold_seconds
        self._dropped_count = 0
        self._total_received = 0

    async def put(self, event: InputEvent) -> bool:
        """
        Push an event into the buffer.

        Called by input interfaces (Voice, Desktop, MCP).
        Returns False if buffer is full.
        """
        self._total_received += 1

        try:
            self._queue.put_nowait(event)
            logger.debug(f"Buffer received: {event}")
            return True
        except asyncio.QueueFull:
            self._dropped_count += 1
            logger.warning(f"Buffer full, dropped event: {event}")
            return False

    def poll_all(self) -> List[InputEvent]:
        """
        Non-blocking: get all pending events, sorted by priority.

        Called by Engine at each cognitive tick.
        Returns events sorted by (priority, timestamp).
        """
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # Sort by priority (lower = more urgent), then timestamp
        return sorted(events, key=lambda e: (e.priority, e.timestamp))

    def poll_fresh(self, max_age_seconds: Optional[float] = None) -> List[InputEvent]:
        """
        Get all pending events, dropping stale ones.

        Useful for discarding old partials after user finished speaking.
        """
        threshold = max_age_seconds or self.stale_threshold
        all_events = self.poll_all()

        fresh = []
        for event in all_events:
            if event.age_seconds <= threshold:
                fresh.append(event)
            else:
                self._dropped_count += 1
                logger.debug(f"Dropped stale event ({event.age_seconds:.1f}s old): {event}")

        return fresh

    def has_interrupt(self) -> bool:
        """
        Check if there's an interrupt pending.

        Non-destructive peek at queue.
        Used by Director to check if it should abort generation.
        """
        # We need to peek without consuming
        # Since asyncio.Queue doesn't support peek, we poll and re-add
        events = []
        has_interrupt = False

        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                events.append(event)
                if event.priority == EventPriority.INTERRUPT:
                    has_interrupt = True
            except asyncio.QueueEmpty:
                break

        # Put them back
        for event in events:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Should never happen since we just removed them

        return has_interrupt

    @property
    def size(self) -> int:
        """Current number of events in buffer."""
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        """Whether buffer is empty."""
        return self._queue.empty()

    @property
    def stats(self) -> dict:
        """Buffer statistics."""
        return {
            "size": self.size,
            "total_received": self._total_received,
            "dropped_count": self._dropped_count,
        }

    def clear(self) -> int:
        """Clear all pending events. Returns number cleared."""
        count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        return count
