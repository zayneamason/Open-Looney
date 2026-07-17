"""Remote adapter base contract for Lexicon generation roles."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional, Sequence

from luna.llm import Message as LLMMessage
from luna.llm import get_registry, init_providers
from luna.llm.fallback import FallbackChain, get_fallback_chain

from ..telemetry import LexiconFailure, emit_failure


class RemoteAdapter(ABC):
    """Base class for Lexicon remote adapters.

    Adapters must normalize their own failures to `None` and emit a
    `LexiconFailure` record. Exceptions must not escape Lexicon's public API.
    """

    adapter_name: str = "remote"
    provider_name: str = ""

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        stream: bool = True,
        cache_breakpoints: Optional[Sequence[Any]] = None,
    ) -> Optional[Any]:
        """Generate a response from remote provider(s)."""

    @abstractmethod
    async def curate(
        self,
        messages: Sequence[Mapping[str, Any]],
        schema: Any,
        *,
        cache_breakpoints: Optional[Sequence[Any]] = None,
    ) -> Optional[Any]:
        """Return structured output from remote provider(s)."""

    def _emit_failure(self, role: str, error_code: str, **call_meta: Any) -> None:
        """Best-effort structured failure telemetry."""
        filtered_meta = {
            key: value for key, value in call_meta.items() if value is not None
        }
        try:
            emit_failure(
                LexiconFailure(
                    role=role,
                    error_code=error_code,
                    call_meta={"adapter": self.adapter_name, **filtered_meta},
                )
            )
        except Exception:
            # Telemetry must never raise into runtime callers.
            pass

    def _normalize_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        """Coerce arbitrary message-like values into fallback-compatible dicts."""
        normalized: list[dict[str, str]] = []
        for raw in messages:
            role = str(raw.get("role", "user"))
            if role not in {"system", "user", "assistant"}:
                role = "user"
            content = raw.get("content", "")
            normalized.append({"role": role, "content": str(content)})
        return normalized

    def _schema_prompt(self, schema: Any) -> str:
        """Build a strict-json prompt for structured curation output."""
        try:
            schema_repr = json.dumps(schema, ensure_ascii=True, sort_keys=True)
        except Exception:
            schema_repr = str(schema)
        return (
            "You are a structured-output service. "
            "Return only valid JSON with no markdown fences. "
            "The JSON must conform to this schema: "
            f"{schema_repr}"
        )

    def _to_llm_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        system: str = "",
    ) -> list[LLMMessage]:
        llm_messages: list[LLMMessage] = []
        if system:
            llm_messages.append(LLMMessage(role="system", content=system))
        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
            llm_messages.append(LLMMessage(role=role, content=content))
        return llm_messages

    async def _complete_via_provider_stack(
        self,
        *,
        provider_name: str,
        messages: Sequence[Mapping[str, Any]],
        system: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """Generate text through existing fallback/provider stack.

        If a global fallback chain exists, run through an isolated one-provider
        chain so adapter routing does not mutate Director's runtime chain.
        Otherwise, call the registered provider directly.
        """
        normalized_messages = self._normalize_messages(messages)

        chain = get_fallback_chain()
        if chain is not None:
            isolated_chain = FallbackChain(
                registry=getattr(chain, "_registry", None),
                local_inference=getattr(chain, "_local", None),
                chain=[provider_name],
                per_provider_timeout_ms=getattr(chain, "_timeout_ms", 30000),
                max_retries_per_provider=getattr(chain, "_max_retries", 1),
            )
            result = await isolated_chain.generate(
                normalized_messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return result.content

        registry = get_registry()
        provider = registry.get(provider_name)
        if provider is None:
            init_providers()
            provider = get_registry().get(provider_name)

        if provider is None:
            raise RuntimeError(f"Provider not registered: {provider_name}")
        if not provider.is_available:
            raise RuntimeError(f"Provider unavailable: {provider_name}")

        completion = await provider.complete(
            self._to_llm_messages(normalized_messages, system=system),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return completion.content
