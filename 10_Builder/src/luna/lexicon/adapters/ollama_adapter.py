"""Ollama-backed Lexicon remote adapter."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .base import RemoteAdapter


class OllamaAdapter(RemoteAdapter):
    """RemoteAdapter pinned to the Ollama provider stack."""

    adapter_name = "ollama"
    provider_name = "ollama"

    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        stream: bool = True,
        cache_breakpoints: Optional[Sequence[Any]] = None,
    ) -> Optional[Any]:
        try:
            content = await self._complete_via_provider_stack(
                provider_name=self.provider_name,
                messages=messages,
                max_tokens=1024,
            )
            return content
        except Exception as exc:
            self._emit_failure(
                "generate",
                "adapter_error",
                provider=self.provider_name,
                stream=stream,
                cache_breakpoints_set=cache_breakpoints is not None,
                exception_type=type(exc).__name__,
            )
            return None

    async def curate(
        self,
        messages: Sequence[Mapping[str, Any]],
        schema: Any,
        *,
        cache_breakpoints: Optional[Sequence[Any]] = None,
    ) -> Optional[Any]:
        # Ollama does not currently support schema-constrained curated output in
        # this path. Return None + telemetry per Lexicon contract.
        self._emit_failure(
            "curate",
            "curate_unsupported_for_provider",
            provider=self.provider_name,
            cache_breakpoints_set=cache_breakpoints is not None,
        )
        return None
