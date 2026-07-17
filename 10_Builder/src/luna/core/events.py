"""
Event Types for Luna Engine
===========================

Events flow into the InputBuffer and get polled by the engine.
Priority determines processing order within a tick.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any


class EventPriority(IntEnum):
    """
    Event priority for processing order.

    Lower number = higher priority.
    """
    INTERRUPT = 0      # Barge-in, abort current generation
    FINAL = 1          # Complete utterance ready to process
    PARTIAL = 2        # Still speaking (for speculative retrieval)
    MCP = 3            # Desktop/API request
    INTERNAL = 4       # Actor-to-actor messages


class EventType(IntEnum):
    """Types of input events."""
    # Voice events
    TRANSCRIPT_PARTIAL = 1
    TRANSCRIPT_FINAL = 2
    USER_INTERRUPT = 3
    # Voice v2.0 Step 4 — synthetic follow-up after a non-CANCEL interrupt
    # classification. Carries InterruptPayload + InterruptClassification in
    # `data`/`payload`; routed to Director via mailbox.
    RESUMPTION_TRIGGER = 4

    # Text events
    TEXT_INPUT = 10

    # MCP events
    MCP_REQUEST = 20
    MCP_TOOL_RESULT = 21

    # Internal events
    RETRIEVAL_COMPLETE = 30
    GENERATION_COMPLETE = 31
    ACTOR_MESSAGE = 32

    # Identity events (FaceID)
    IDENTITY_RECOGNIZED = 40
    IDENTITY_LOST = 41

    # System events
    SHUTDOWN = 90
    HEARTBEAT = 91


@dataclass
class InputEvent:
    """
    An event from any input source (voice, desktop, MCP).

    Events land in the InputBuffer and get polled every tick.
    """
    type: EventType
    payload: Any
    priority: EventPriority = EventPriority.MCP
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "unknown"
    correlation_id: str | None = None
    metadata: dict | None = None

    def __post_init__(self):
        # Auto-assign priority based on type if not explicit
        if self.priority == EventPriority.MCP:
            self.priority = self._infer_priority()

    def _infer_priority(self) -> EventPriority:
        """Infer priority from event type."""
        match self.type:
            case EventType.USER_INTERRUPT:
                return EventPriority.INTERRUPT
            case EventType.TRANSCRIPT_FINAL | EventType.TEXT_INPUT | EventType.RESUMPTION_TRIGGER:
                return EventPriority.FINAL
            case EventType.TRANSCRIPT_PARTIAL:
                return EventPriority.PARTIAL
            case EventType.MCP_REQUEST | EventType.MCP_TOOL_RESULT:
                return EventPriority.MCP
            case EventType.IDENTITY_RECOGNIZED | EventType.IDENTITY_LOST:
                return EventPriority.INTERNAL
            case _:
                return EventPriority.INTERNAL

    @property
    def age_seconds(self) -> float:
        """How old this event is."""
        return (datetime.now() - self.timestamp).total_seconds()

    def __repr__(self) -> str:
        preview = str(self.payload)[:30]
        return f"InputEvent({self.type.name}, priority={self.priority}, '{preview}')"
