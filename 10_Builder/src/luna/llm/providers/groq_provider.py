"""
Groq LLM Provider.

Uses the Groq API for fast inference with Llama and Mixtral models.
Free tier: ~30 RPM, 14,400 requests/day.
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

# Model info (updated Jan 2026 - llama-3.1 models decommissioned)
GROQ_MODELS = {
    "llama-3.3-70b-versatile": ModelInfo("llama-3.3-70b-versatile", 128000, True),
    "llama-3.3-70b-specdec": ModelInfo("llama-3.3-70b-specdec", 8192, True),
    "llama3-70b-8192": ModelInfo("llama3-70b-8192", 8192, True),
    "llama3-8b-8192": ModelInfo("llama3-8b-8192", 8192, True),
    "mixtral-8x7b-32768": ModelInfo("mixtral-8x7b-32768", 32768, True),
}


class GroqProvider:
    """Groq API provider implementation."""

    name = "groq"

    def __init__(self):
        self._client = None
        self._api_key = os.environ.get("GROQ_API_KEY")

    def _get_client(self):
        """Lazy-load the Groq client."""
        if self._client is None and self._api_key:
            try:
                from groq import AsyncGroq
                self._client = AsyncGroq(api_key=self._api_key)
            except ImportError:
                logger.warning("groq package not installed. Run: pip install groq")
        return self._client

    @property
    def is_available(self) -> bool:
        """Check if Groq is configured."""
        return self._api_key is not None and len(self._api_key) > 0

    def get_status(self) -> ProviderStatus:
        """Get provider status."""
        if not self._api_key:
            return ProviderStatus.NOT_CONFIGURED
        return ProviderStatus.AVAILABLE

    def get_limits(self) -> ProviderLimits:
        """Get Groq rate limits."""
        return ProviderLimits(
            requests_per_minute=30,
            tokens_per_day=None,  # Based on request count not tokens
            requires_payment=False,
        )

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """Get model information."""
        if model is None:
            config = get_config()
            pconfig = config.get_provider_config("groq")
            model = pconfig.default_model if pconfig else "llama-3.3-70b-versatile"
        return GROQ_MODELS.get(model, GROQ_MODELS["llama-3.3-70b-versatile"])

    def list_models(self) -> list[str]:
        """List available models."""
        return list(GROQ_MODELS.keys())

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
            raise RuntimeError("Groq client not available")

        if model is None:
            model = self.get_model_info().name

        # Convert messages to Groq format
        groq_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=groq_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            return CompletionResult(
                content=response.choices[0].message.content,
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                provider="groq",
            )
        except Exception as e:
            logger.error(f"Groq completion failed: {e}")
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
            raise RuntimeError("Groq client not available")

        if model is None:
            model = self.get_model_info().name

        groq_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=groq_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Groq stream failed: {e}")
            raise
