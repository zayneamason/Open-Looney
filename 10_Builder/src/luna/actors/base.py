"""
Actor Base Class for Luna Engine
=================================

Actors are isolated components with their own state and mailbox.
They communicate via async message passing, providing fault isolation.

If one actor crashes, others continue running.
"""

from abc import ABC, abstractmethod
from asyncio import Queue
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING
import asyncio
import logging
import uuid

if TYPE_CHECKING:
    from luna.engine import LunaEngine

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """
    A message passed between actors.

    All inter-actor communication happens via Messages in mailboxes.
    """
    type: str
    payload: Any = None
    sender: str | None = None
    reply_to: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        return f"Message({self.type}, from={self.sender}, id={self.correlation_id})"


class Actor(ABC):
    """
    Base class for all Luna actors.

    Each actor:
    - Has its own isolated state
    - Has a private mailbox (async queue)
    - Processes messages one at a time
    - Can send messages to other actors

    Lifecycle:
        start() -> on_start() -> message loop -> on_stop() -> stopped
    """

    def __init__(self, name: str, engine: Optional["LunaEngine"] = None):
        self.name = name
        self.engine = engine
        self.mailbox: Queue[Message] = Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the actor's message processing loop."""
        if self._running:
            logger.warning(f"Actor {self.name} already running")
            return

        self._running = True
        logger.info(f"Actor {self.name} starting")

        try:
            await self.on_start()

            while self._running:
                try:
                    # Wait for message with timeout for graceful shutdown
                    msg = await asyncio.wait_for(
                        self.mailbox.get(),
                        timeout=1.0
                    )
                    await self._handle_safe(msg)

                except asyncio.TimeoutError:
                    # No message - continue loop (allows checking _running)
                    continue

        except asyncio.CancelledError:
            logger.info(f"Actor {self.name} cancelled")

        except Exception as e:
            logger.error(f"Actor {self.name} crashed: {e}")
            raise

        finally:
            await self.on_stop()
            logger.info(f"Actor {self.name} stopped")

    async def _handle_safe(self, msg: Message) -> None:
        """Handle message with error isolation."""
        try:
            await self.handle(msg)
        except Exception as e:
            logger.error(f"Actor {self.name} failed to handle {msg}: {e}")
            await self.on_error(e, msg)
            # Don't re-raise - actor continues running

    async def stop(self) -> None:
        """Stop the actor gracefully."""
        logger.info(f"Stopping actor {self.name}")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def send(self, target: "Actor", msg: Message) -> None:
        """Send a message to another actor."""
        msg.sender = self.name
        await target.mailbox.put(msg)
        logger.debug(f"{self.name} -> {target.name}: {msg.type}")

    async def send_to_engine(self, event_type: str, payload: Any) -> None:
        """Send an event back to the engine's input buffer."""
        if self.engine:
            from luna.core.events import InputEvent, EventType
            event = InputEvent(
                type=EventType.ACTOR_MESSAGE,
                payload={"type": event_type, "data": payload},
                source=self.name,
            )
            await self.engine.input_buffer.put(event)
            print(f"🔔 [SEND_TO_ENGINE] {self.name} → {event_type} (buffer size={self.engine.input_buffer.qsize() if hasattr(self.engine.input_buffer, 'qsize') else '?'})")
        else:
            print(f"⛔ [SEND_TO_ENGINE] {self.name} has NO engine ref! Cannot send {event_type}")

    # =========================================================================
    # Abstract methods - subclasses must implement
    # =========================================================================

    @abstractmethod
    async def handle(self, msg: Message) -> None:
        """
        Process a message from the mailbox.

        This is the main logic. Pattern match on msg.type and process.
        """
        pass

    # =========================================================================
    # Lifecycle hooks - subclasses may override
    # =========================================================================

    async def on_start(self) -> None:
        """Called when actor starts. Override to initialize resources."""
        pass

    async def on_stop(self) -> None:
        """Called when actor stops. Override to cleanup resources."""
        pass

    async def on_error(self, error: Exception, msg: Message) -> None:
        """Called when message handling fails. Override for custom error handling."""
        logger.error(f"Actor {self.name} error handling {msg}: {error}")

    # =========================================================================
    # State serialization - for snapshot/restore
    # =========================================================================

    async def snapshot(self) -> dict:
        """Return state to serialize. Override to add actor-specific state."""
        return {
            "name": self.name,
            "mailbox_size": self.mailbox.qsize(),
        }

    async def restore(self, state: dict) -> None:
        """Restore from serialized state. Override for actor-specific restoration."""
        pass
