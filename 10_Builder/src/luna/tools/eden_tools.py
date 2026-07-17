"""
Eden Tools for Luna Engine
============================

Tools for interacting with Eden.art — image/video generation
and agent conversations.

These tools allow Luna to create media and chat with Eden agents
programmatically through the agentic tool-use pipeline.

Pattern follows memory_tools.py:
- Global adapter reference (set during engine init)
- Async tool implementations
- Tool dataclass definitions
- register_eden_tools() convenience function
"""

import logging
from typing import Any, Optional

from .registry import Tool

logger = logging.getLogger(__name__)


# =============================================================================
# EDEN ADAPTER INTERFACE
# =============================================================================

# Global eden adapter reference (set by engine initialization)
_eden_adapter = None

# Global engine reference (for consciousness updates on errors)
_engine = None

# Global policy reference (loaded during engine init)
_eden_policy = None


def set_eden_adapter(adapter, engine=None) -> None:
    """
    Set the global Eden adapter reference.

    Called during engine initialization to provide the tools
    access to Eden.art API.

    Args:
        adapter: The EdenAdapter instance (already entered as context manager)
        engine: Optional LunaEngine reference for consciousness updates
    """
    global _eden_adapter, _engine, _eden_policy
    _eden_adapter = adapter
    _engine = engine

    # Load policy
    try:
        from luna.services.eden.policy import EdenPolicy
        _eden_policy = EdenPolicy.load()
        logger.info(f"Eden policy loaded: enabled={_eden_policy.enabled}")
    except Exception as e:
        logger.warning(f"Failed to load Eden policy, using defaults: {e}")
        from luna.services.eden.policy import EdenPolicy
        _eden_policy = EdenPolicy()

    logger.info("Eden tools connected to EdenAdapter")


def get_eden_adapter():
    """
    Get the current Eden adapter.

    Returns:
        The EdenAdapter instance, or None if not set
    """
    return _eden_adapter


def get_eden_policy():
    """
    Get the current Eden policy.

    Returns:
        The EdenPolicy instance, or None if not loaded
    """
    return _eden_policy


def _check_policy(tool_name: str) -> Optional[str]:
    """
    Check policy before executing an Eden tool.

    Returns:
        None if allowed, or an error message string if blocked.
    """
    if _eden_policy is None:
        return None  # No policy = allow all

    if not _eden_policy.enabled:
        return "Eden tools are currently disabled by policy"

    if not _eden_policy.check_budget(tool_name):
        remaining = _eden_policy.generation_budget_remaining
        return f"Session budget exceeded. {remaining} generations remaining"

    return None  # Allowed


def _record_usage(tool_name: str) -> None:
    """Record tool usage for budget tracking."""
    if _eden_policy:
        _eden_policy.record_usage(tool_name)


def _audit_eden_call(tool_name: str, params: dict, result: dict) -> None:
    """
    Audit trail: log every Eden call to memory if policy requires it.
    """
    if not _eden_policy or not _eden_policy.audit_to_memory:
        return
    if not _engine:
        return

    try:
        bridge = _engine.get_actor("eden_bridge")
        if bridge:
            from luna.actors.base import Message
            import asyncio
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.ensure_future(
                    bridge.mailbox.put(Message(
                        type="eden_audit",
                        payload={
                            "tool": tool_name,
                            "params": params,
                            "result_status": "success" if "error" not in result else "error",
                            "result_summary": result.get("url") or result.get("response", "")[:100],
                        },
                    ))
                )
            )
    except Exception as e:
        logger.debug(f"Eden audit trail error: {e}")


def _update_consciousness_on_error(error: Exception) -> None:
    """
    Update Luna's consciousness when an Eden operation fails.

    Decreases coherence and shifts mood to reflect the disruption.
    """
    if _engine is None:
        return

    try:
        consciousness = _engine.consciousness
        # Decrease coherence — external service failure is disorienting
        consciousness.coherence = max(0.3, consciousness.coherence - 0.15)
        consciousness.focus_on("eden_error", weight=0.6)
        logger.debug(f"Consciousness updated after Eden error: coherence={consciousness.coherence:.2f}")
    except Exception as e:
        logger.debug(f"Failed to update consciousness: {e}")


def _update_consciousness_on_success(action: str) -> None:
    """
    Update Luna's consciousness after a successful Eden operation.

    Boosts coherence slightly and tracks attention.
    """
    if _engine is None:
        return

    try:
        consciousness = _engine.consciousness
        consciousness.coherence = min(1.0, consciousness.coherence + 0.05)
        consciousness.focus_on(f"eden_{action}", weight=0.4)
    except Exception:
        pass


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

async def eden_create_image(
    prompt: str,
    wait: bool = True,
    tool: str = "create",
    public: bool = False,
) -> dict:
    """
    Create an image using Eden.art.

    Args:
        prompt: Description of the image to generate
        wait: If True, blocks until generation completes (default True)
        tool: Eden tool to use (default "create")
        public: Whether to make the creation public on Eden

    Returns:
        Dict with task_id, status, url, and completion info

    Raises:
        RuntimeError: If Eden adapter is not initialized
    """
    if _eden_adapter is None:
        raise RuntimeError("Eden adapter not initialized. Is EDEN_API_KEY set?")

    # Policy enforcement
    policy_error = _check_policy("eden_create_image")
    if policy_error:
        return {"error": policy_error, "policy_blocked": True}

    try:
        task = await _eden_adapter.create_image(
            prompt, wait=wait, tool=tool, public=public
        )

        _update_consciousness_on_success("create_image")
        _record_usage("eden_create_image")

        result = {
            "task_id": task.id,
            "status": task.status.value,
            "url": task.first_output_url,
            "is_complete": task.is_complete,
            "is_failed": task.is_failed,
            "error": task.error,
        }
        _audit_eden_call("eden_create_image", {"prompt": prompt}, result)
        return result

    except Exception as e:
        _update_consciousness_on_error(e)
        raise


async def eden_create_video(
    prompt: str,
    wait: bool = True,
    tool: str = "create",
    public: bool = False,
) -> dict:
    """
    Create a video using Eden.art.

    Args:
        prompt: Description of the video to generate
        wait: If True, blocks until generation completes (default True)
        tool: Eden tool to use (default "create")
        public: Whether to make the creation public on Eden

    Returns:
        Dict with task_id, status, url, and completion info

    Raises:
        RuntimeError: If Eden adapter is not initialized
    """
    if _eden_adapter is None:
        raise RuntimeError("Eden adapter not initialized. Is EDEN_API_KEY set?")

    # Policy enforcement
    policy_error = _check_policy("eden_create_video")
    if policy_error:
        return {"error": policy_error, "policy_blocked": True}

    try:
        task = await _eden_adapter.create_video(
            prompt, wait=wait, tool=tool, public=public
        )

        _update_consciousness_on_success("create_video")
        _record_usage("eden_create_video")

        result = {
            "task_id": task.id,
            "status": task.status.value,
            "url": task.first_output_url,
            "is_complete": task.is_complete,
            "is_failed": task.is_failed,
            "error": task.error,
        }
        _audit_eden_call("eden_create_video", {"prompt": prompt}, result)
        return result

    except Exception as e:
        _update_consciousness_on_error(e)
        raise


async def eden_chat(
    agent_id: str,
    message: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Chat with an Eden agent.

    Creates a new session if session_id is not provided,
    then sends the message and retrieves the response.

    Args:
        agent_id: Eden agent ID to chat with
        message: Message to send
        session_id: Optional existing session ID to continue

    Returns:
        Dict with session_id, response, and message count

    Raises:
        RuntimeError: If Eden adapter is not initialized
    """
    if _eden_adapter is None:
        raise RuntimeError("Eden adapter not initialized. Is EDEN_API_KEY set?")

    # Policy enforcement
    policy_error = _check_policy("eden_chat")
    if policy_error:
        return {"error": policy_error, "policy_blocked": True}

    try:
        # Create session if needed
        if not session_id:
            session_id = await _eden_adapter.create_session([agent_id])

        # Send message
        await _eden_adapter.send_message(session_id, message)

        # Get session to read response
        session = await _eden_adapter.get_session(session_id)

        last_response = ""
        if session and session.messages:
            # Find last assistant/eden message
            for msg in reversed(session.messages):
                if msg.role.value in ("assistant", "eden"):
                    last_response = msg.content
                    break

        _update_consciousness_on_success("chat")

        # Bridge to Luna memory if engine available
        if _engine:
            await _bridge_eden_message(
                session_id=session_id,
                agent_id=agent_id,
                user_message=message,
                agent_response=last_response,
            )

        _record_usage("eden_chat")
        result = {
            "session_id": session_id,
            "agent_id": agent_id,
            "response": last_response,
            "messages_count": len(session.messages) if session else 0,
        }
        _audit_eden_call("eden_chat", {"agent_id": agent_id, "message": message}, result)
        return result

    except Exception as e:
        _update_consciousness_on_error(e)
        raise


async def eden_list_agents() -> dict:
    """
    List available Eden agents.

    Returns:
        Dict with list of agents

    Raises:
        RuntimeError: If Eden adapter is not initialized
    """
    if _eden_adapter is None:
        raise RuntimeError("Eden adapter not initialized. Is EDEN_API_KEY set?")

    agents = await _eden_adapter.list_agents()
    return {
        "count": len(agents),
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "tools": list(a.tools.keys()),
            }
            for a in agents
        ],
    }


async def eden_health() -> dict:
    """
    Check Eden API health.

    Returns:
        Dict with health status
    """
    if _eden_adapter is None:
        return {"healthy": False, "reason": "Eden adapter not initialized"}

    healthy = await _eden_adapter.health_check()
    return {"healthy": healthy}


# =============================================================================
# MEMORY BRIDGE
# =============================================================================

async def _bridge_eden_message(
    session_id: str,
    agent_id: str,
    user_message: str,
    agent_response: str,
) -> None:
    """
    Bridge an Eden conversation exchange into Luna's memory.

    Sends a message to the eden_bridge actor if registered,
    otherwise stores directly via the engine's record API.
    """
    if not _engine:
        return

    try:
        bridge = _engine.get_actor("eden_bridge")
        if bridge:
            from luna.actors.base import Message
            await bridge.mailbox.put(Message(
                type="eden_session_message",
                payload={
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "message": user_message,
                    "response": agent_response,
                },
            ))
        else:
            # Fallback: store directly via engine conversation API
            if agent_response:
                await _engine.record_conversation_turn(
                    role="assistant",
                    content=f"[Eden agent {agent_id}]: {agent_response}",
                    source="eden",
                )
    except Exception as e:
        logger.debug(f"Eden memory bridge error: {e}")


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

eden_create_image_tool = Tool(
    name="eden_create_image",
    description="Generate an image using Eden.art AI. Returns the image URL when complete.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Description of the image to generate",
            },
            "wait": {
                "type": "boolean",
                "description": "Wait for generation to complete (default: true)",
                "default": True,
            },
            "tool": {
                "type": "string",
                "description": "Eden generation tool (default: 'create')",
                "default": "create",
            },
            "public": {
                "type": "boolean",
                "description": "Make the creation public on Eden (default: false)",
                "default": False,
            },
        },
        "required": ["prompt"],
    },
    execute=eden_create_image,
    requires_confirmation=True,
    timeout_seconds=180,  # Image generation can take time
)

eden_create_video_tool = Tool(
    name="eden_create_video",
    description="Generate a video using Eden.art AI. Returns the video URL when complete.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Description of the video to generate",
            },
            "wait": {
                "type": "boolean",
                "description": "Wait for generation to complete (default: true)",
                "default": True,
            },
            "tool": {
                "type": "string",
                "description": "Eden generation tool (default: 'create')",
                "default": "create",
            },
            "public": {
                "type": "boolean",
                "description": "Make the creation public on Eden (default: false)",
                "default": False,
            },
        },
        "required": ["prompt"],
    },
    execute=eden_create_video,
    requires_confirmation=True,
    timeout_seconds=300,  # Video generation takes longer
)

eden_chat_tool = Tool(
    name="eden_chat",
    description="Chat with an Eden AI agent. Creates a session if needed. Returns the agent's response.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "Eden agent ID to chat with",
            },
            "message": {
                "type": "string",
                "description": "Message to send to the agent",
            },
            "session_id": {
                "type": "string",
                "description": "Optional existing session ID to continue a conversation",
            },
        },
        "required": ["agent_id", "message"],
    },
    execute=eden_chat,
    requires_confirmation=True,
    timeout_seconds=60,
)

eden_list_agents_tool = Tool(
    name="eden_list_agents",
    description="List available Eden AI agents with their capabilities.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    execute=eden_list_agents,
    requires_confirmation=False,
    timeout_seconds=30,
)

eden_health_tool = Tool(
    name="eden_health",
    description="Check if the Eden.art API is reachable and the API key is valid.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    execute=eden_health,
    requires_confirmation=False,
    timeout_seconds=15,
)


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

ALL_EDEN_TOOLS = [
    eden_create_image_tool,
    eden_create_video_tool,
    eden_chat_tool,
    eden_list_agents_tool,
    eden_health_tool,
]


def register_eden_tools(registry) -> None:
    """
    Register all Eden tools with a ToolRegistry.

    Args:
        registry: The ToolRegistry to register tools with
    """
    for tool in ALL_EDEN_TOOLS:
        registry.register(tool)
    logger.info(f"Registered {len(ALL_EDEN_TOOLS)} Eden tools")


def get_eden_policy_status() -> dict:
    """Get current policy status for debugging/display."""
    if _eden_policy is None:
        return {"loaded": False}
    return {
        "loaded": True,
        "enabled": _eden_policy.enabled,
        "auto_approve": _eden_policy.auto_approve,
        "require_approval": _eden_policy.require_approval,
        "generation_budget_remaining": _eden_policy.generation_budget_remaining,
        "chat_budget_remaining": _eden_policy.chat_budget_remaining,
    }
