"""
Haiku Subtask Backend
=====================

Drop-in replacement for LocalInference that calls Claude Haiku
for lightweight classification tasks.

Prefers the LLM registry (async, no direct SDK dependency).
Falls back to direct Anthropic SDK if registry unavailable.

Same interface as LocalInference:
  - .is_loaded -> bool
  - .generate(user_message, system_prompt, max_tokens) -> result with .text
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HaikuResult:
    """Mimics LocalInference result shape."""
    text: str


class HaikuSubtaskBackend:
    """
    Calls Claude Haiku for subtask classification.

    Implements the same interface as LocalInference so it slots
    directly into LocalSubtaskRunner without changes to the runner.
    """

    HAIKU_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self):
        self._provider = None  # Registry provider (async)
        self._client = None    # Direct SDK client (sync fallback)
        self._available = False
        self._use_registry = False
        self._init_client()

    def _init_client(self):
        """Initialize via registry first, then direct SDK fallback."""
        # Ensure .env is loaded (key may not be in os.environ yet)
        try:
            from dotenv import load_dotenv
            from luna.core.paths import project_root
            _env = project_root() / ".env"
            if _env.exists():
                load_dotenv(_env, override=True)
        except Exception:
            pass

        # Try LLM registry (preferred — async, no direct dependency)
        try:
            from luna.llm import get_provider
            provider = get_provider("claude")
            if provider and provider.is_available:
                self._provider = provider
                self._available = True
                self._use_registry = True
                logger.info("[HAIKU-SUBTASK] Backend ready via registry (model: %s)", self.HAIKU_MODEL)
                return
        except ImportError:
            pass

        # Fallback: direct Anthropic SDK
        try:
            import anthropic
            self._client = anthropic.Anthropic()
            self._available = self._client.api_key is not None
            if self._available:
                logger.info("[HAIKU-SUBTASK] Backend ready via direct SDK (model: %s)", self.HAIKU_MODEL)
            else:
                logger.warning("[HAIKU-SUBTASK] No API key found")
        except ImportError:
            logger.warning("[HAIKU-SUBTASK] No Claude provider available (anthropic not installed, registry unavailable)")
        except Exception as e:
            logger.warning("[HAIKU-SUBTASK] Init failed: %s", e)

    @property
    def is_loaded(self) -> bool:
        """Compatible with LocalInference.is_loaded check."""
        return self._available

    async def generate(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 60,
        **kwargs,
    ) -> HaikuResult:
        """
        Call Haiku for a classification subtask.

        Compatible with LocalInference.generate() signature.
        Uses async registry when available, sync SDK in executor otherwise.
        """
        # Prefer async registry path (no executor needed)
        if self._use_registry and self._provider:
            try:
                from luna.llm import Message as LLMMessage
                messages = []
                if system_prompt:
                    messages.append(LLMMessage(role="system", content=system_prompt))
                messages.append(LLMMessage(role="user", content=user_message))
                result = await self._provider.complete(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    model=self.HAIKU_MODEL,
                )
                return HaikuResult(text=result.content)
            except Exception as e:
                logger.warning("[HAIKU-SUBTASK] Registry call failed, trying direct SDK: %s", e)

        # Fallback: sync SDK in executor
        if self._client:
            import asyncio

            def _call():
                msgs = [{"role": "user", "content": user_message}]
                create_kwargs = {
                    "model": self.HAIKU_MODEL,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "messages": msgs,
                }
                if system_prompt:
                    create_kwargs["system"] = system_prompt
                response = self._client.messages.create(**create_kwargs)
                return response.content[0].text

            text = await asyncio.get_event_loop().run_in_executor(None, _call)
            return HaikuResult(text=text)

        raise RuntimeError("No Claude provider available for Haiku subtask")
