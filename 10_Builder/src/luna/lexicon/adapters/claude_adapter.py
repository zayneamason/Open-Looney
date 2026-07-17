"""Claude-backed Lexicon remote adapter."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from .base import RemoteAdapter


class ClaudeAdapter(RemoteAdapter):
    """RemoteAdapter pinned to the Claude provider stack."""

    adapter_name = "claude"
    provider_name = "claude"

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
        try:
            content = await self._complete_via_provider_stack(
                provider_name=self.provider_name,
                messages=messages,
                system=self._schema_prompt(schema),
                max_tokens=512,
                temperature=0.0,
            )
            try:
                return json.loads(content)
            except Exception as parse_exc:
                self._emit_failure(
                    "curate",
                    "curate_parse_error",
                    provider=self.provider_name,
                    cache_breakpoints_set=cache_breakpoints is not None,
                    exception_type=type(parse_exc).__name__,
                )
                return None
        except Exception as exc:
            self._emit_failure(
                "curate",
                "adapter_error",
                provider=self.provider_name,
                cache_breakpoints_set=cache_breakpoints is not None,
                exception_type=type(exc).__name__,
            )
            return None
