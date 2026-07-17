"""
Google Gemini LLM Provider.

Uses the Gemini API for long-context tasks.
Free tier: 15 RPM, 1M tokens/day, 1500 requests/day.
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

# Model info (updated Jan 2026 - 1.5 models sunset, using 2.x)
GEMINI_MODELS = {
    "gemini-2.0-flash": ModelInfo("gemini-2.0-flash", 1000000, True),
    "gemini-2.5-flash": ModelInfo("gemini-2.5-flash", 1000000, True),
    "gemini-2.5-pro": ModelInfo("gemini-2.5-pro", 2000000, True),
}


class GeminiProvider:
    """Google Gemini API provider implementation."""

    name = "gemini"

    def __init__(self):
        self._model = None
        self._api_key = os.environ.get("GOOGLE_API_KEY")
        self._configured = False

    def _configure(self):
        """Configure the Gemini SDK."""
        if self._configured or not self._api_key:
            return

        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._configured = True
        except ImportError:
            logger.warning("google-generativeai not installed. Run: pip install google-generativeai")

    def _get_model(self, model_name: str = None):
        """Get a Gemini model instance."""
        self._configure()
        if not self._configured:
            return None

        try:
            import google.generativeai as genai
            if model_name is None:
                config = get_config()
                pconfig = config.get_provider_config("gemini")
                model_name = pconfig.default_model if pconfig else "gemini-2.0-flash"
            return genai.GenerativeModel(model_name)
        except Exception as e:
            logger.error(f"Failed to get Gemini model: {e}")
            return None

    @property
    def is_available(self) -> bool:
        """Check if Gemini is configured."""
        return self._api_key is not None and len(self._api_key) > 0

    def get_status(self) -> ProviderStatus:
        """Get provider status."""
        if not self._api_key:
            return ProviderStatus.NOT_CONFIGURED
        return ProviderStatus.AVAILABLE

    def get_limits(self) -> ProviderLimits:
        """Get Gemini rate limits."""
        return ProviderLimits(
            requests_per_minute=15,
            tokens_per_day=1000000,
            requires_payment=False,
        )

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        """Get model information."""
        if model is None:
            config = get_config()
            pconfig = config.get_provider_config("gemini")
            model = pconfig.default_model if pconfig else "gemini-2.0-flash"
        return GEMINI_MODELS.get(model, GEMINI_MODELS["gemini-2.0-flash"])

    def list_models(self) -> list[str]:
        """List available models."""
        return list(GEMINI_MODELS.keys())

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """
        Convert messages to Gemini format.

        Gemini uses a different format:
        - System message becomes system_instruction
        - User/assistant messages go in contents
        """
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                # Gemini uses "user" and "model" roles
                role = "user" if msg.role == "user" else "model"
                contents.append({"role": role, "parts": [msg.content]})

        return system_instruction, contents

    async def complete(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None,
    ) -> CompletionResult:
        """Complete a conversation."""
        import asyncio

        model_instance = self._get_model(model)
        if not model_instance:
            raise RuntimeError("Gemini model not available")

        system_instruction, contents = self._convert_messages(messages)

        # Gemini SDK is sync, run in thread
        def _sync_generate():
            try:
                import google.generativeai as genai

                # Recreate model with system instruction if needed
                if system_instruction:
                    model_with_sys = genai.GenerativeModel(
                        model_instance.model_name,
                        system_instruction=system_instruction,
                    )
                else:
                    model_with_sys = model_instance

                # Start a chat for multi-turn
                chat = model_with_sys.start_chat(history=contents[:-1] if len(contents) > 1 else [])

                # Send the last message
                last_content = contents[-1]["parts"][0] if contents else ""
                response = chat.send_message(
                    last_content,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    }
                )
                return response
            except Exception as e:
                logger.error(f"Gemini generation failed: {e}")
                raise

        response = await asyncio.to_thread(_sync_generate)

        return CompletionResult(
            content=response.text,
            model=model_instance.model_name,
            usage={
                "prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', 0),
                "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0),
            },
            provider="gemini",
        )

    async def stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion."""
        import asyncio

        model_instance = self._get_model(model)
        if not model_instance:
            raise RuntimeError("Gemini model not available")

        system_instruction, contents = self._convert_messages(messages)

        # Gemini streaming is sync, need to wrap
        def _sync_stream():
            try:
                import google.generativeai as genai

                if system_instruction:
                    model_with_sys = genai.GenerativeModel(
                        model_instance.model_name,
                        system_instruction=system_instruction,
                    )
                else:
                    model_with_sys = model_instance

                chat = model_with_sys.start_chat(history=contents[:-1] if len(contents) > 1 else [])
                last_content = contents[-1]["parts"][0] if contents else ""

                response = chat.send_message(
                    last_content,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    },
                    stream=True,
                )

                for chunk in response:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                logger.error(f"Gemini stream failed: {e}")
                raise

        # Wrap sync generator for async
        loop = asyncio.get_event_loop()
        gen = _sync_stream()
        while True:
            try:
                chunk = await loop.run_in_executor(None, next, gen)
                yield chunk
            except StopIteration:
                break
