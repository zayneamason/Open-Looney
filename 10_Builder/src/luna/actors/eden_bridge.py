"""
Eden Bridge Actor
==================

Bridges Eden.art agent conversations into Luna's memory substrate.

When Luna chats with Eden agents, this actor:
- Receives session messages from eden_tools
- Stores user queries as DECISION nodes
- Stores agent responses as INSIGHT nodes
- Links related nodes together

This gives Luna persistent memory of her interactions with Eden agents,
making the conversations part of her continuous consciousness.
"""

import logging
from typing import Optional

from luna.actors.base import Actor, Message

logger = logging.getLogger(__name__)


class EdenBridgeActor(Actor):
    """
    Bridges Eden agent sessions to Luna's memory substrate.

    Handles message types:
    - "eden_session_message": Store a conversation exchange
    - "eden_session_closed": Clean up session tracking
    """

    def __init__(self):
        super().__init__(name="eden_bridge")
        self._active_sessions: dict[str, str] = {}  # session_id -> agent_id

    @property
    def is_ready(self) -> bool:
        """Check if bridge can store to memory."""
        if not self.engine:
            return False
        matrix = self.engine.get_actor("matrix")
        return matrix is not None and matrix.is_ready

    async def on_start(self) -> None:
        """Initialize bridge."""
        logger.info(f"Eden bridge started (memory ready: {self.is_ready})")

    async def handle(self, msg: Message) -> None:
        """Process messages."""
        match msg.type:
            case "eden_session_message":
                await self._store_exchange(msg.payload)

            case _:
                logger.debug(f"Eden bridge ignoring message type: {msg.type}")

    async def _store_exchange(self, payload: dict) -> None:
        """
        Store an Eden conversation exchange in Luna's memory.

        Creates two memory nodes:
        1. DECISION — what the user asked the Eden agent
        2. INSIGHT — what the Eden agent responded

        Then links them with a 'clarifies' relationship.
        """
        session_id = payload.get("session_id", "")
        agent_id = payload.get("agent_id", "unknown")
        user_message = payload.get("message", "")
        agent_response = payload.get("response", "")

        # Track active session
        self._active_sessions[session_id] = agent_id

        if not self.is_ready:
            logger.debug("Eden bridge: memory not ready, skipping storage")
            return

        matrix = self.engine.get_actor("matrix")
        memory = getattr(matrix, "_matrix", None) or getattr(matrix, "_memory", None)

        if not memory:
            return

        try:
            # Store user's query to Eden agent
            if user_message:
                query_id = await memory.add_node(
                    node_type="DECISION",
                    content=f"Asked Eden agent '{agent_id}': {user_message}",
                    source="eden_bridge",
                    metadata={
                        "eden_session_id": session_id,
                        "eden_agent_id": agent_id,
                        "direction": "outbound",
                    },
                    importance=0.4,
                )
                logger.debug(f"Stored Eden query node: {query_id}")

            # Store agent's response
            if agent_response:
                response_id = await memory.add_node(
                    node_type="INSIGHT",
                    content=f"Eden agent '{agent_id}' responded: {agent_response}",
                    source="eden_bridge",
                    metadata={
                        "eden_session_id": session_id,
                        "eden_agent_id": agent_id,
                        "direction": "inbound",
                    },
                    importance=0.5,
                )
                logger.debug(f"Stored Eden response node: {response_id}")

                # Link query → response if both exist
                if user_message and query_id and response_id:
                    graph = getattr(matrix, "_graph", None)
                    if graph:
                        await graph.add_edge(
                            query_id, response_id,
                            relationship="clarifies",
                            strength=0.8,
                        )

        except Exception as e:
            logger.error(f"Eden bridge storage error: {e}")

    async def snapshot(self) -> dict:
        """Serialize state for persistence."""
        return {
            "name": self.name,
            "mailbox_size": self.mailbox.qsize(),
            "active_sessions": dict(self._active_sessions),
        }
