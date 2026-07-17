"""Anthropic tool-use loop for Director's delegated path.

Enables tools (count_recent_mentions, search_recent_turns, etc.) to be
called by Claude during a delegated turn. Runs non-streaming because
tool_use blocks aren't compatible with text-token streaming; the caller
can replay the final text through its streaming callback for UX parity.

Distinct from luna.agentic.loop.AgentLoop, which is a custom reasoning
loop that does NOT use Claude's native tools= API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Heuristic — when does a delegated turn warrant tool access?
# Keep tight: every match adds non-streaming latency, so only fire on
# queries that visibly want introspection. Broad matches like "what" or
# "tell me" would gate too aggressively.
_TOOL_QUERY_PATTERN = re.compile(
    r"\b("
    r"how many|how often|count|number of times|"
    r"in the last \d+|in the past \d+|"
    r"times (?:have|did) (?:i|you|we)|"
    r"recently said|recently mentioned|recently talked about|"
    r"last (?:few |several )?(?:minutes?|hours?|turns?)"
    r")\b",
    re.IGNORECASE,
)


def message_wants_tools(message: str) -> bool:
    """Cheap heuristic: does this user message look like an introspection
    query that warrants tool access? Used to gate the non-streaming
    tool-use path in Director.delegated."""
    if not message:
        return False
    return bool(_TOOL_QUERY_PATTERN.search(message))


def tool_to_anthropic_schema(tool) -> dict:
    """Convert a luna.tools.registry.Tool to Anthropic's tool schema."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


async def run_tool_use_turn(
    *,
    client: Any,
    model: str,
    system: str,
    messages: list[dict],
    tools: list,
    max_tokens: int = 1024,
    max_iterations: int = 4,
    abort_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """Run an Anthropic tool-use loop until the model returns text.

    Non-streaming. Each iteration: call the API; if response is a tool_use,
    execute the tool, append tool_result to messages, loop. When response is
    text, return it.

    Returns None on hard failure (caller should fall through to the regular
    streaming path). Returns "" if the loop terminates with no text content
    (rare; treated like None at call sites that need a real response).

    Args:
        client: anthropic.Anthropic sync client
        model: model id (e.g. "claude-sonnet-4-5")
        system: system prompt
        messages: initial messages list (will not be mutated; copy made)
        tools: list of luna.tools.registry.Tool objects
        max_tokens: per-turn max output tokens
        max_iterations: hard ceiling on tool_use rounds before giving up
        abort_check: optional callable returning True to abort mid-loop
    """
    if not tools:
        return None

    anthropic_tools = [tool_to_anthropic_schema(t) for t in tools]
    by_name = {t.name: t for t in tools}
    convo = list(messages)  # shallow copy; we append to it

    for iteration in range(max_iterations):
        if abort_check and abort_check():
            logger.info("[TOOL_USE_LOOP] aborted at iteration %d", iteration)
            return None

        try:
            response = await asyncio.to_thread(
                client.messages.create,
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=convo,
                tools=anthropic_tools,
            )
        except Exception as e:
            logger.warning(f"[TOOL_USE_LOOP] API call failed at iter {iteration}: {e}")
            return None

        stop_reason = getattr(response, "stop_reason", None)
        content_blocks = list(getattr(response, "content", []) or [])

        if stop_reason != "tool_use":
            text_parts = [
                getattr(b, "text", "") for b in content_blocks
                if getattr(b, "type", None) == "text"
            ]
            text = "".join(text_parts).strip()
            logger.info(
                "[TOOL_USE_LOOP] terminated stop_reason=%s iter=%d text_len=%d",
                stop_reason, iteration, len(text),
            )
            return text

        # tool_use: execute every tool_use block in this assistant turn.
        tool_use_blocks = [b for b in content_blocks if getattr(b, "type", None) == "tool_use"]
        if not tool_use_blocks:
            logger.warning("[TOOL_USE_LOOP] stop_reason=tool_use but no tool_use blocks")
            return None

        tool_results = []
        for tu in tool_use_blocks:
            name = getattr(tu, "name", "")
            args = getattr(tu, "input", {}) or {}
            tool = by_name.get(name)
            if tool is None:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": getattr(tu, "id", ""),
                    "content": f"unknown tool: {name}",
                    "is_error": True,
                })
                continue
            try:
                result = await tool.execute(**args)
            except Exception as e:
                logger.warning(f"[TOOL_USE_LOOP] tool '{name}' failed: {e}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": getattr(tu, "id", ""),
                    "content": f"tool error: {e}",
                    "is_error": True,
                })
                continue
            try:
                payload = json.dumps(result, default=str)
            except Exception:
                payload = str(result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": getattr(tu, "id", ""),
                "content": payload,
            })
            logger.info(
                "[TOOL_USE_LOOP] executed tool=%s args=%s payload_len=%d",
                name, list(args.keys()), len(payload),
            )

        # Append assistant turn (full content list, including tool_use) and
        # user turn with tool_results, then continue.
        convo.append({"role": "assistant", "content": content_blocks})
        convo.append({"role": "user", "content": tool_results})

    logger.warning(
        "[TOOL_USE_LOOP] hit max_iterations=%d without text terminus",
        max_iterations,
    )
    return None
