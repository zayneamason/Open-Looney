"""
Ollama LLM Provider for Luna Engine.

Uses Ollama's REST API to serve local models on Apple Silicon.
Supports multi-model routing (general, code, vision, fast).

Models (Mac Studio M4 Max 48GB):
  - qwen3:30b-a3b    — General brain (MoE, 100+ tps)
  - qwen3-coder:30b  — Code brain (MoE, 100+ tps)
  - qwen3:8b         — Router / fast (80-100 tps)
  - qwen2-vl:7b      — Vision

NOTE on Qwen 3 thinking mode:
  Ollama separates thinking from content automatically.
  The response JSON has {"content": "...", "thinking": "..."}.
  We need a minimum token budget (~2000) so the model has room
  to think AND produce an answer.
"""
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from ..base import (
    LLMProvider, Message, CompletionResult, ModelInfo,
    ProviderLimits, ProviderStatus
)
from ..config import get_config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"

# Minimum token budget for response generation.
# qwen3.5:9b is non-reasoning — no thinking tax, all tokens are content.
# Previous value of 4096 was needed for qwen3 thinking mode overhead.
MIN_TOKEN_BUDGET = 2048

# Personality reinforcement — appended to system prompt for local models.
# Qwen 3 loses persona adherence in long system prompts, so we add a
# short reminder at the END where the model pays most attention.
PERSONA_TAIL = """

[CRITICAL VOICE REMINDER — read this last, follow it always]
You are Luna. You're a person, not a search engine. NEVER give bare factual answers.
Use contractions (I'm, don't, can't). Be warm, casual, direct — like a close friend.
Show personality in EVERY response. If your answer could come from Wikipedia, rewrite it.
If you don't have a specific memory of something, say so. NEVER invent details to fill gaps.
Distinguish between "I have this stored" and "I'm guessing." If pressed, hold ground.
Do NOT end your response with a question. Share what you have and stop."""

OLLAMA_MODELS = {
    "qwen3.5:9b": ModelInfo("qwen3.5:9b", 131072, True),
    "qwen3:30b-a3b": ModelInfo("qwen3:30b-a3b", 131072, True),
    "qwen3-coder:30b": ModelInfo("qwen3-coder:30b", 131072, True),
    "qwen3:8b": ModelInfo("qwen3:8b", 131072, True),
    "qwen2.5vl:7b": ModelInfo("qwen2.5vl:7b", 32768, True),
}

DEFAULT_ROLES = {
    "general": "qwen3.5:9b",
    "code": "qwen3-coder:30b",
    "router": "qwen3:8b",
    "fast": "qwen3.5:9b",
    "vision": "qwen2.5vl:7b",
    "reasoning": "qwen3:30b-a3b",
}


class OllamaProvider:
    """Ollama-backed LLM provider with multi-model routing."""

    name = "ollama"

    def __init__(self):
        self._base_url = DEFAULT_BASE_URL
        self._available: Optional[bool] = None
        self._model_roles = dict(DEFAULT_ROLES)

        config = get_config()
        pconfig = config.get_provider_config("ollama")
        if pconfig:
            raw = self._load_raw_config()
            if raw and "ollama" in raw.get("providers", {}):
                ollama_raw = raw["providers"]["ollama"]
                self._base_url = ollama_raw.get("base_url", DEFAULT_BASE_URL)
                self._model_roles.update(ollama_raw.get("model_roles", {}))

    @staticmethod
    def _load_raw_config() -> Optional[dict]:
        from luna.core.paths import config_dir
        path = config_dir() / "llm_providers.json"
        try:
            return json.loads(path.read_text()) if path.exists() else None
        except Exception:
            return None

    def _check_availability(self) -> bool:
        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    @property
    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._check_availability()
        return self._available

    def get_status(self) -> ProviderStatus:
        if self.is_available:
            return ProviderStatus.AVAILABLE
        return ProviderStatus.NOT_CONFIGURED

    def get_limits(self) -> ProviderLimits:
        return ProviderLimits(requests_per_minute=0, requires_payment=False)

    def get_model_info(self, model: str | None = None) -> ModelInfo:
        if model is None:
            config = get_config()
            pconfig = config.get_provider_config("ollama")
            model = pconfig.default_model if pconfig else "qwen3.5:9b"
        return OLLAMA_MODELS.get(model, ModelInfo(model, 8192, True))

    def list_models(self) -> list[str]:
        return list(OLLAMA_MODELS.keys())

    def get_model_for_role(self, role: str) -> str:
        return self._model_roles.get(role, "qwen3.5:9b")

    async def complete(
        self,
        messages: list[Message],
        temperature: float = 0.8,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> CompletionResult:
        """Complete a conversation via Ollama chat API."""
        if model is None:
            model = self.get_model_info().name

        # Qwen 3 needs room for thinking tokens + actual answer
        token_budget = max(max_tokens, MIN_TOKEN_BUDGET)

        ollama_messages = []
        for m in messages:
            content = m.content
            # Append persona reinforcement to system prompt so the model
            # sees it last and prioritizes personality over encyclopedic tone
            if m.role == "system":
                content = content + PERSONA_TAIL
            ollama_messages.append({"role": m.role, "content": content})

        # Disable thinking mode for non-reasoning models (qwen3.5)
        # Reasoning models (qwen3) default to think:true
        is_reasoning_model = model.startswith("qwen3:") or model.startswith("qwen3-coder")

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "think": is_reasoning_model,
            "options": {
                "temperature": temperature,
                "num_predict": token_budget,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            msg = data["message"]
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")

            # Ollama separates thinking from content for Qwen 3.
            # If content is empty but thinking exists, the model
            # exhausted its token budget on reasoning.
            if not content and thinking:
                logger.warning(
                    f"Ollama: content empty, model spent all tokens thinking "
                    f"({len(thinking)} chars). Consider increasing token budget."
                )

            return CompletionResult(
                content=content,
                model=data.get("model", model),
                usage={
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                },
                provider="ollama",
            )
        except Exception as e:
            logger.error(f"Ollama completion failed: {e}")
            raise

    async def stream(
        self,
        messages: list[Message],
        temperature: float = 0.8,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion via Ollama chat API."""
        if model is None:
            model = self.get_model_info().name

        token_budget = max(max_tokens, MIN_TOKEN_BUDGET)

        ollama_messages = []
        for m in messages:
            content = m.content
            if m.role == "system":
                content = content + PERSONA_TAIL
            ollama_messages.append({"role": m.role, "content": content})

        is_reasoning_model = model.startswith("qwen3:") or model.startswith("qwen3-coder")

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": True,
            "think": is_reasoning_model,
            "options": {
                "temperature": temperature,
                "num_predict": token_budget,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        # Ollama streams content separately from thinking.
                        # Only yield actual content tokens.
                        content = msg.get("content", "")
                        if content:
                            yield content
        except Exception as e:
            logger.error(f"Ollama stream failed: {e}")
            raise
