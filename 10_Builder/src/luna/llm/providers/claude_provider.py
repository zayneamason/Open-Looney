"""
Anthropic Claude LLM Provider.

Uses the Anthropic API for Claude models.
Pay-as-you-go pricing (no free tier).
"""
import os
import logging
from typing import AsyncIterator, Optional

from ..base import (
    LLMProvider, Message, CompletionResult, ModelInfo,
    ProviderLimits, ProviderStatus
)
from ..config import get_config

logger = logging.getLogger(__name__)

# Model info (updated March 2026 — current Claude 4.x models)
CLAUDE_MODELS = {
    # Claude 4.x (current)
    "claude-haiku-4-5-20251001": ModelInfo("claude-haiku-4-5-20251001", 200000, True),
    "claude-sonnet-4-6": ModelInfo("claude-sonnet-4-6", 200000, True),
    "claude-opus-4-6": ModelInfo("claude-opus-4-6", 200000, True),
    # Claude 3.x (legacy)
    "claude-3-5-sonnet-20241022": ModelInfo("claude-3-5-sonnet-20241022", 200000, True),
    "claude-3-haiku-20240307": ModelInfo("claude-3-haiku-20240307", 200000, True),
    "claude-3-opus-20240229": ModelInfo("claude-3-opus-20240229", 200000, True),
}


class ClaudeProvider:
    """Anthropic Claude API provider implementation."""

    name = "claude"

    def __init__(self):
        self._client = None
        self._api_key = os.environ.get("ANTHROPIC_API_KEY")

    def _get_client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None and self._api_key:
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                logger.warning("anthropic package not installed. Run: pip install anthropic")
        return self._client

    @property
    def is_available(self) -> bool:
        """Check if Claude is configured."""
        return self._api_key is not None and len(self._api_key) > 0

    def get_status(self) -> ProviderStatus:
        """Get provider status."""
        if not self._api_key:
            return ProviderStatus.NOT_CONFIGURED
        return ProviderStatus.AVAILABLE

    def get_limits(self) -> ProviderLimits:
        """Get Claude rate limits."""
        return ProviderLimits(
            requests_per_minute=60,  # Depends on tier
            tokens_per_day=None,
            requires_payment=True,
        )

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """Get model information."""
        if model is None:
            config = get_config()
            pconfig = config.get_provider_config("claude")
            model = pconfig.default_model if pconfig else "claude-haiku-4-5-20251001"
        return CLAUDE_MODELS.get(model, CLAUDE_MODELS["claude-haiku-4-5-20251001"])

    def list_models(self) -> list[str]:
        """List available models."""
        return list(CLAUDE_MODELS.keys())

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """
        Convert messages to Anthropic format.

        Anthropic uses separate system parameter.
        """
        system = ""
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content
            else:
                anthropic_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        # Anthropic SDK >=0.76 requires system as list of content blocks
        if isinstance(system, str):
            system = [{"type": "text", "text": system}]

        return system, anthropic_messages

    async def complete(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None,
    ) -> CompletionResult:
        """Complete a conversation."""
        client = self._get_client()
        if not client:
            raise RuntimeError("Claude client not available")

        if model is None:
            model = self.get_model_info().name

        system, anthropic_messages = self._convert_messages(messages)

        try:
            response = await client.messages.create(
                model=model,
                messages=anthropic_messages,
                system=system if system else None,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = ""
            if response.content:
                content = response.content[0].text

            return CompletionResult(
                content=content,
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                },
                provider="claude",
            )
        except Exception as e:
            logger.error(f"Claude completion failed: {e}")
            raise

    async def stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion."""
        client = self._get_client()
        if not client:
            raise RuntimeError("Claude client not available")

        if model is None:
            model = self.get_model_info().name

        system, anthropic_messages = self._convert_messages(messages)

        try:
            async with client.messages.stream(
                model=model,
                messages=anthropic_messages,
                system=system if system else None,
                temperature=temperature,
                max_tokens=max_tokens,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error(f"Claude stream failed: {e}")
            raise
